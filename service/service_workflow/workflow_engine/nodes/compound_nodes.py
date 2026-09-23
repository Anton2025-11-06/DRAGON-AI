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
from service.service_workflow.workflow_engine.graph import BRANCH_HANDLE_PREFIX
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
)

BRANCH_PREFIX = BRANCH_HANDLE_PREFIX


class LoopNodeExecutor(BaseNodeExecutor):
    """LOOP：while 循环 —— 评估 exitCondition（{{引用}} 或 true/false），不满足则执行 branch:body
    子图，body 收敛后回到本节点重新评估。maxIterations 防失控（默认 3）。"""

    node_type = "LOOP"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        max_iter = int(cfg.get("maxIterations") or 3)
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
        # 循环计数变量一并进 output：跨轮恢复时 global_vars 由「已落库的节点输出」重放
        # 得到（快照不再存 global 副本），只写 ctx.global_vars 会让恢复后的计数归零
        output = {output_var: last_outputs, "iterations": iterations, loop_var: iterations}
        return NodeResult(output=output, branch_id=None)  # 走默认 output 端口（退出）

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
        arr = ctx.resolve_ref(cfg.get("arrayVariable"))
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
    """PARALLEL：显式并行屏障 —— 各分支并行执行，按 waitStrategy 汇合：
    ALL 等全部 / ANY 任一 / FIRST 第一个（按画布 y 序优先）。

    分支识别优先级：
    1. node.data.branches 显式配置的分支 id（走 branch:{id} 端口）
    2. 画布上连出的 branch:{id} 端口出边
    3. BUG15：并行节点在画布上只有一个 output 端口（不像 IF_ELSE 有多个分支端口），
       用户是从同一个出口连出多条线代表多个并行分支，这些边的 source_handle 都是
       output。按端口取不到 branch:* 时，把 output 端口的每条出边各视为一个分支，
       否则并行节点退化成普通扇出、等待策略（任一完成）完全不生效。
    """

    node_type = "PARALLEL"

    # 画布单出口虚拟分支的 id 前缀（不是端口，只用于区分分支归属）
    VIRTUAL_PREFIX = "node:"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        # BUG13：与 IF_ELSE 一致，把入边上游的输出透传进本节点输出（铺底），
        # 否则没有分支连线时追踪里只剩一个空 branches，看不到输入数据流过
        passthrough = self.input_pass_through(ctx)
        # 本节点要等分支汇合后才 COMPLETED，引擎在分支内节点跑完后才统一写 output；
        # 先把透传数据铺进上下文，分支入口节点（并行后直接接的大模型等）才能
        # 引用到 {{并行分支.xxx}}，节点追踪的输入视图也不会空
        if passthrough:
            ctx.set_node_output(self.node.id, passthrough)
        branches = cfg.get("branches") or []
        branch_ids: list[str] = []
        entries: dict[str, list[str]] = {}
        if branches:
            branch_ids = [str(b.get("id")) for b in branches if b.get("id")]
        else:
            for edge in (self.graph.get_out_edges(self.node.id) or []):
                handle = edge.source_handle or ""
                if handle.startswith(BRANCH_PREFIX):
                    bid = handle[len(BRANCH_PREFIX):]
                    entry_ids = [edge.target]
                else:
                    bid = f"{self.VIRTUAL_PREFIX}{edge.target}"
                    entry_ids = [edge.target]
                if bid in entries:
                    # 同一分支端口连多个节点 = 该分支的多个入口，一起收进分支
                    entries[bid].append(edge.target)
                    continue
                entries[bid] = entry_ids
                branch_ids.append(bid)
        if not branch_ids:
            return NodeResult(output={**passthrough, "branches": {}})

        strategy = (cfg.get("waitStrategy") or "ALL").upper()
        timeout_ms = int(cfg.get("timeout") or 0)

        results = await self.runtime.run_branches(
            self.node, branch_ids, wait_strategy=strategy,
            timeout_ms=timeout_ms, entries=entries)

        completed = {bid: bool(v) for bid, v in results.items()}
        return NodeResult(output={
            **passthrough,
            "branches": results,
            "completed": completed,
            "waitStrategy": strategy,
        })

    @staticmethod
    def validate_node(node, graph) -> list:
        # issues = []
        # outs = [e for e in (graph.get_out_edges(node.id) or [])
        #         if (e.source_handle or "").startswith("branch:")]
        # if not outs:
        #     issues.append(issue("PAR_NO_BRANCH", "ERROR", "并行节点没有分支连线", node))
        # return issues
        return []
