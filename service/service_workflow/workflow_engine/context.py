# -*- coding: utf-8 -*-
"""执行上下文与变量渲染（参照 MaxKB WorkflowManage 三级上下文 + reset_prompt 算法重写）。

作用域（从高到低）：
1. global  —— 全局变量（工作流级， VARIABLE_ASSIGNER 写入）
2. node    —— 节点输出上下文 {node_id: {var: value}}（含 START 输入）
3. local   —— LOOP/ITERATION 迭代内 item / index

变量引用语法（对齐前端 types.ts 注释）：{{nodeName.variableName}}
解析顺序：node_id 精确匹配 → node label 匹配 → global 作用域。
支持嵌套路径：{{node.obj.field}} / {{node.arr.0}}。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from service.service_workflow.workflow_engine.graph import WorkflowGraph

# {{xxx.yyy[.zzz]}} —— 允许字母/数字/下划线/中文/点号
VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_\u4e00-\u9fa5.\-\[\]0-9]+)\s*\}\}")


class VariableNotFound(Exception):
    """strict 模式下变量引用无法解析"""


class ExecutionContext:
    """单次执行的上下文容器（非线程安全；引擎在单 asyncio 任务内串行访问，
    并行分支通过引擎拷贝快照传递只读视图）。"""

    def __init__(self, graph: WorkflowGraph, inputs: Optional[dict] = None):
        self.graph = graph
        self.inputs: dict = dict(inputs or {})
        self.global_vars: dict = {}          # VARIABLE_ASSIGNER / 调试 API 写入
        self.node_outputs: dict = {}          # {node_id: {var: value}}
        self.scopes: list[dict] = []          # 局部作用域栈（LOOP/ITERATION item/index）
        self.executed: list[str] = []         # 已执行节点顺序（nodeStates.order）

    # ==================== 写入 ====================

    def set_node_output(self, node_id: str, output: dict) -> None:
        """节点完成后写输出上下文（合并语义，流式节点可多次更新）。"""
        self.node_outputs[node_id] = {**self.node_outputs.get(node_id, {}), **(output or {})}

    def push_scope(self, variables: dict) -> None:
        self.scopes.append(variables)

    def pop_scope(self) -> Optional[dict]:
        return self.scopes.pop() if self.scopes else None

    # ==================== 读取 ====================

    def get_node_output(self, node_id: str) -> dict:
        return self.node_outputs.get(node_id, {})

    def to_dict(self) -> dict:
        """调试快照：全部上下文导出。"""
        return {
            "inputs": self.inputs,
            "global": self.global_vars,
            "nodes": self.node_outputs,
            "scopes": list(self.scopes),
            "executed": list(self.executed),
        }

    def restore(self, snapshot: dict) -> None:
        self.inputs = dict(snapshot.get("inputs") or {})
        self.global_vars = dict(snapshot.get("global") or {})
        self.node_outputs = {k: dict(v) for k, v in (snapshot.get("nodes") or {}).items()}
        self.scopes = [dict(s) for s in (snapshot.get("scopes") or [])]
        self.executed = list(snapshot.get("executed") or [])

    # ==================== 变量解析 ====================

    def resolve(self, ref: str) -> Any:
        """解析单个变量引用（不带 {{}}）。找不到时返回 None（非 strict 场景）。"""
        node = self.graph.resolve_node(ref)
        if node is not None:
            return self._value_of_node(node.id)
        # 已写入的节点输出兜底（快照恢复/图变更场景）
        if ref in self.node_outputs:
            return self.node_outputs[ref]
        # 作用域栈（栈顶优先）：item/index
        for scope in reversed(self.scopes):
            if ref in scope:
                return scope[ref]
        # global 作用域（支持 global.xxx 前缀与裸名）
        if ref.startswith("global."):
            return _dig(self.global_vars, ref[len("global."):])
        if ref in self.global_vars:
            return self.global_vars[ref]
        # 直接是输入参数名
        if ref in self.inputs:
            return self.inputs[ref]
        # 带路径的引用：xxx.yyy.zzz —— xxx 是节点，后为路径
        head, _, rest = ref.partition(".")
        if rest:
            node = self.graph.resolve_node(head)
            if node is not None:
                return _dig(self._value_of_node(node.id), rest)
            if head in self.node_outputs:
                return _dig(self.node_outputs[head], rest)
            if head in self.global_vars:
                return _dig(self.global_vars[head], rest)
        return None

    def _value_of_node(self, node_id: str) -> Any:
        out = self.node_outputs.get(node_id)
        return out

    def resolve_or_raw(self, ref: str, strict: bool = False) -> Any:
        value = self.resolve(ref)
        if value is None and strict:
            raise VariableNotFound(f"变量引用无法解析: {ref}")
        return value

    # ==================== 模板渲染 ====================

    def render(self, template: Any, strict: bool = False,
               keep_unresolved: bool = True) -> Any:
        """渲染模板中的 {{引用}}。递归处理 dict/list；非字符串原样返回。

        strict=True 时未解析引用抛错（TEMPLATE 节点 strictMode / 发布校验）；
        keep_unresolved=True 保留原文占位（默认，便于调试定位）；
        keep_unresolved=False 时未解析引用渲染为空串（END 汇聚多分支场景：
        未执行分支的引用不应残留在最终输出里）。
        """
        return self._render_value(template, strict, keep_unresolved)

    def _render_value(self, value: Any, strict: bool, keep_unresolved: bool = True) -> Any:
        if isinstance(value, str):
            return self._render_string(value, strict, keep_unresolved)
        if isinstance(value, dict):
            return {k: self._render_value(v, strict, keep_unresolved) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_value(v, strict, keep_unresolved) for v in value]
        return value

    def _render_string(self, text: str, strict: bool, keep_unresolved: bool = True) -> str:
        def _sub(m: re.Match) -> str:
            ref = m.group(1)
            value = self.resolve(ref)
            if value is None:
                if strict:
                    raise VariableNotFound(f"变量引用无法解析: {ref}")
                return m.group(0) if keep_unresolved else ""  # 保留原占位或渲染为空串
            if isinstance(value, (dict, list)):
                import json
                return json.dumps(value, ensure_ascii=False)
            return "" if value is None else str(value)

        return VAR_PATTERN.sub(_sub, text)

    def extract_refs(self, text: str) -> list[str]:
        """提取模板中的全部变量引用（依赖分析/校验用）。"""
        return [m.group(1) for m in VAR_PATTERN.finditer(text or "")]


def _dig(value: Any, path: str) -> Any:
    """按点分路径取嵌套值，支持数组下标：a.b.0.c。"""
    if value is None:
        return None
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if value is None:
            return None
    return value
