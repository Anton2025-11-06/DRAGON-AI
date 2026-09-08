# -*- coding: utf-8 -*-
"""复合节点：LOOP / ITERATION / PARALLEL（子图执行，参照 MaxKB LoopWorkflowManage 思想）。

端口约定（需与前端画布联调确认，详见 README）：
- LOOP：branch:body → 循环体；output 端口 → 满足退出条件后的正常出口
- ITERATION：branch:body → 逐元素处理体（体内引用 item/index）；output → 汇总出口
- PARALLEL：branch:{id} → 并行分支；output → 全部/任一完成后出口
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
)


class LoopNodeExecutor(BaseNodeExecutor):
    """LOOP：while 循环 —— 评估 exitCondition（{{引用}} 或 true/false），不满足则执行 branch:body
    子图，body 收敛后回到本节点重新评估。maxIterations 防失控（默认 1000）。"""

    node_type = "LOOP"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        max_iter = int(cfg.get("maxIterations") or 1000)
        loop_var = cfg.get("loopVariable") or "loopIndex"
        iterations = 0
        last_outputs: dict = {}
        # 循环变量在首次退出条件判断前初始化为 0，避免「global.loopIndex 未写入 → 引用退化成
        # 字符串比较」导致退出条件误判（如 "global.loopIndex" >= "3" 为真，使循环零次退出）。
        if loop_var not in ctx.global_vars:
            ctx.global_vars[loop_var] = 0

        while iterations < max_iter:
            self.runtime._check_cancelled()
            exit_value = self._eval_exit(ctx, cfg.get("exitCondition"))
            if exit_value:
                break
            iterations += 1
            ctx.global_vars[loop_var] = iterations
            body_pairs = self.graph.get_next_nodes(self.node.id, "body")
            if not body_pairs:
                break  # 无循环体连线 → 直接退出
            entry = body_pairs[0][0]
            results = await self.runtime.run_subgraph(entry.id)
            last_outputs = results
            # 循环体内若写入了 loop 变量（如 continue 条件），由 exitCondition 表达
        else:
            raise ValueError(f"循环节点达到最大迭代次数上限: {max_iter}")

        output_var = cfg.get("outputVariable") or "loopResult"
        return NodeResult(
            output={output_var: last_outputs, "iterations": iterations},
            branch_id=None,  # 走默认 output 端口（退出）
        )

    def _eval_exit(self, ctx: ExecutionContext, condition: Optional[str]) -> bool:
        if not condition:
            return True
        rendered = ctx.render(str(condition)).strip().lower()
        if rendered in ("true", "1", "yes"):
            return True
        if rendered in ("false", "0", "no", ""):
            return False
        # 简单比较表达式：a == b / a > b 等
        import re
        m = re.fullmatch(r"(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)", rendered)
        if m:
            from service.service_workflow.workflow_engine.comparators import compare
            op = {"==": "EQUALS", "!=": "NOT_EQUALS", ">": "GREATER_THAN",
                  ">=": "GREATER_OR_EQUAL", "<": "LESS_THAN", "<=": "LESS_OR_EQUAL"}[m.group(2)]
            try:
                return compare(op, m.group(1).strip(), m.group(3).strip())
            except ValueError:
                return False
        return bool(rendered)

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("exitCondition"):
            issues.append(issue("LOOP_NO_EXIT", "ERROR", "循环节点未配置退出条件", node))
        return issues


class IterationNodeExecutor(BaseNodeExecutor):
    """ITERATION：for-each —— arrayVariable 解析为数组，逐元素（或并行）执行 branch:body
    子图，体内通过 {{item}} / {{index}} 引用当前元素。输出 = 每次迭代 body 输出聚合数组。"""

    node_type = "ITERATION"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        arr = ctx.resolve(str(cfg.get("arrayVariable") or ""))
        if arr is None:
            raise ValueError(f"迭代数组变量无法解析: {cfg.get('arrayVariable')}")
        if not isinstance(arr, list):
            raise ValueError(f"迭代变量不是数组: {type(arr).__name__}")
        max_iter = int(cfg.get("maxIterations") or 1000)
        if len(arr) > max_iter:
            raise ValueError(f"迭代元素数量 {len(arr)} 超过上限 {max_iter}")

        mode = (cfg.get("processingMode") or "SEQUENTIAL").upper()
        parallel_count = int(cfg.get("parallelCount") or 3)
        output_var = cfg.get("outputVariable") or "items"

        body_pairs = self.graph.get_next_nodes(self.node.id, "body")
        if not body_pairs:
            return NodeResult(output={output_var: [], "count": len(arr)})
        entry = body_pairs[0][0]
        item_key = self._body_output_key(entry.id)

        results: list = []

        async def _run_one(index: int, item):
            scoped = {"item": item, "index": index}
            outputs = await self.runtime.run_subgraph(entry.id, scope_vars=scoped)
            return outputs.get(item_key, outputs)

        if mode == "PARALLEL":
            sem = asyncio.Semaphore(max(1, parallel_count))

            async def _limited(i, item):
                async with sem:
                    return await _run_one(i, item)
            results = list(await asyncio.gather(*[_limited(i, x) for i, x in enumerate(arr)]))
        else:
            for i, item in enumerate(arr):
                self.runtime._check_cancelled()
                timeout_ms = int(cfg.get("iterationTimeout") or 0)
                if timeout_ms:
                    results.append(await asyncio.wait_for(_run_one(i, item), timeout_ms / 1000))
                else:
                    results.append(await _run_one(i, item))

        return NodeResult(output={output_var: results, "count": len(arr)})

    def _body_output_key(self, entry_id: str) -> str:
        """body 首节点的输出作为每次迭代的代表输出。"""
        return entry_id

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("arrayVariable"):
            issues.append(issue("ITER_NO_ARRAY", "ERROR", "迭代节点未配置数组变量", node))
        return issues


class ParallelNodeExecutor(BaseNodeExecutor):
    """PARALLEL：显式并行屏障 —— 各 branch:{id} 分支并行执行，按 waitStrategy 汇合：
    ALL 等全部 / ANY 任一 / FIRST 第一个（按画布 y 序优先）。"""

    node_type = "PARALLEL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        branches = cfg.get("branches") or []
        if not branches:
            # 未显式配置分支：取全部 branch:* 出边
            outs = self.graph.get_out_edges(self.node.id)
            branch_ids = [h[len("branch:"):] for h in
                          (e.source_handle or "" for e in outs)
                          if h.startswith("branch:")]
            branches = [{"id": b, "name": b} for b in branch_ids]
        if not branches:
            return NodeResult(output={"branches": {}})

        strategy = (cfg.get("waitStrategy") or "ALL").upper()
        timeout_ms = int(cfg.get("timeout") or 0)
        branch_ids = [str(b.get("id")) for b in branches if b.get("id")]

        results = await self.runtime.run_branches(
            self.node, branch_ids, wait_strategy=strategy, timeout_ms=timeout_ms)

        completed = {bid: bool(v) for bid, v in results.items()}
        return NodeResult(output={
            "branches": results,
            "completed": completed,
            "waitStrategy": strategy,
        })

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        outs = [e for e in (graph.get_out_edges(node.id) or [])
                if (e.source_handle or "").startswith("branch:")]
        if not outs:
            issues.append(issue("PAR_NO_BRANCH", "ERROR", "并行节点没有分支连线", node))
        return issues
