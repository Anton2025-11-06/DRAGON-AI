# -*- coding: utf-8 -*-
"""工作流图模型：解析 / 索引 / 校验（参照 MaxKB application/flow/common.py 的设计重写为纯 Python）。

数据契约（对齐前端 types.ts WorkflowGraph）：
    {
      "nodes": [{"id","type","label","position":{"x","y"},"data":{...配置}}],
      "edges": [{"id","source","sourceHandle","target","targetHandle"}]
    }
端口契约（对齐前端 domain/ports.ts）：
    - 普通输出端口: "output"，输入端口: "input"
    - 分支输出端口: "branch:{branchId}"（IF_ELSE 分支 / QUESTION_CLASSIFIER 分类 / LOOP 循环体）

引擎与 ORM/Redis 完全解耦：本模块只做纯数据结构与算法，可独立单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

INPUT_HANDLE = "input"
OUTPUT_HANDLE = "output"
BRANCH_HANDLE_PREFIX = "branch:"


def is_branch_handle(handle: Optional[str]) -> bool:
    return bool(handle) and handle.startswith(BRANCH_HANDLE_PREFIX)


def branch_id_of(handle: Optional[str]) -> Optional[str]:
    """'branch:abc' -> 'abc'"""
    if is_branch_handle(handle):
        return handle[len(BRANCH_HANDLE_PREFIX):]
    return None


class WorkflowGraphError(Exception):
    """图结构非法（保存/发布时校验用）"""

    def __init__(self, issues: list["GraphIssue"]):
        self.issues = issues
        super().__init__("; ".join(f"[{i.severity}] {i.code}: {i.message}" for i in issues))


@dataclass
class GraphIssue:
    code: str
    severity: str  # ERROR / WARNING / SUGGESTION
    message: str
    node_id: Optional[str] = None
    node_label: Optional[str] = None
    node_type: Optional[str] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "nodeId": self.node_id,
            "nodeLabel": self.node_label,
            "nodeType": self.node_type,
            "suggestion": self.suggestion,
        }


@dataclass
class Node:
    id: str
    type: str
    label: str
    position: dict  # {"x": float, "y": float}
    data: dict = field(default_factory=dict)

    @property
    def y(self) -> float:
        try:
            return float(self.position.get("y", 0))
        except (TypeError, ValueError):
            return 0.0

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class Edge:
    id: str
    source: str
    target: str
    source_handle: Optional[str] = None
    target_handle: Optional[str] = None


class WorkflowGraph:
    """解析后的工作流图，提供拓扑索引与基础查询。"""

    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise WorkflowGraphError([GraphIssue("GRAPH_INVALID", "ERROR", "graph 必须是对象")])
        self.raw = raw
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self._node_map: dict[str, Node] = {}
        self._label_map: dict[str, Node] = {}
        self._parse(raw)

        # 拓扑索引
        self._out_edges: dict[str, list[Edge]] = {}
        self._in_edges: dict[str, list[Edge]] = {}
        for e in self.edges:
            self._out_edges.setdefault(e.source, []).append(e)
            self._in_edges.setdefault(e.target, []).append(e)

    # ---------------- 解析 ----------------

    def _parse(self, raw: dict) -> None:
        for n in raw.get("nodes") or []:
            node = Node(
                id=str(n.get("id", "")),
                type=str(n.get("type", "")),
                label=str(n.get("label") or n.get("name") or n.get("id", "")),
                position=n.get("position") or {},
                data=n.get("data") or {},
            )
            self.nodes.append(node)
            self._node_map[node.id] = node
            self._label_map.setdefault(node.label, node)
        for e in raw.get("edges") or []:
            edge = Edge(
                id=str(e.get("id", f"{e.get('source')}->{e.get('target')}")),
                source=str(e.get("source", "")),
                target=str(e.get("target", "")),
                source_handle=e.get("sourceHandle") or OUTPUT_HANDLE,
                target_handle=e.get("targetHandle") or INPUT_HANDLE,
            )
            self.edges.append(edge)

    # ---------------- 查询 ----------------

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._node_map.get(node_id)

    def get_node_by_label(self, label: str) -> Optional[Node]:
        """变量引用 {{nodeName.var}} 允许按节点 label 解析（id 优先）。"""
        return self._label_map.get(label)

    def resolve_node(self, ref: str) -> Optional[Node]:
        """按节点 ID 或 label 解析引用。"""
        return self._node_map.get(ref) or self._label_map.get(ref)

    def get_out_edges(self, node_id: str, branch_id: Optional[str] = None) -> list[Edge]:
        """取节点出边；branch_id 非空时只取该分支端口的边（None=所有）。"""
        result = self._out_edges.get(node_id, [])
        if branch_id is None:
            return list(result)
        want = f"{BRANCH_HANDLE_PREFIX}{branch_id}"
        return [e for e in result if e.source_handle == want]

    def get_in_edges(self, node_id: str) -> list[Edge]:
        return list(self._in_edges.get(node_id, []))

    def get_up_nodes(self, node_id: str) -> list[Node]:
        return [self.get_node(e.source) for e in self.get_in_edges(node_id) if self.get_node(e.source)]

    def get_next_nodes(self, node_id: str, branch_id: Optional[str] = None) -> list[tuple[Node, Edge]]:
        pairs = []
        for e in self.get_out_edges(node_id, branch_id):
            n = self.get_node(e.target)
            if n is not None:
                pairs.append((n, e))
        return pairs

    def find_start_node(self) -> Optional[Node]:
        for n in self.nodes:
            if n.type == "START":
                return n
        return None

    def find_end_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.type == "END"]

    # ---------------- 校验 ----------------

    def validate(self) -> list[GraphIssue]:
        """保存/发布前的静态校验。ERROR 级问题阻断发布。"""
        issues: list[GraphIssue] = []

        def add(code, severity, message, node: Optional[Node] = None, suggestion=None):
            issues.append(GraphIssue(
                code=code, severity=severity, message=message,
                node_id=node.id if node else None,
                node_label=node.label if node else None,
                node_type=node.type if node else None,
                suggestion=suggestion,
            ))

        # 1. 节点 ID 唯一性（_node_map 构建时静默覆盖，此处显式检查）
        seen: set[str] = set()
        for n in self.nodes:
            if not n.id:
                add("NODE_ID_EMPTY", "ERROR", "存在空节点 ID", n)
            elif n.id in seen:
                add("NODE_ID_DUP", "ERROR", f"节点 ID 重复: {n.id}", n)
            seen.add(n.id)

        # 2. 边引用完整性
        for e in self.edges:
            if e.source not in self._node_map:
                add("EDGE_SOURCE_MISSING", "ERROR", f"边 {e.id} 的源节点不存在: {e.source}")
            if e.target not in self._node_map:
                add("EDGE_TARGET_MISSING", "ERROR", f"边 {e.id} 的目标节点不存在: {e.target}")
            if e.source == e.target:
                add("EDGE_SELF_LOOP", "ERROR", f"节点 {e.source} 存在自环", self.get_node(e.source))

        # 3. START / END
        start = self.find_start_node()
        if start is None:
            add("START_MISSING", "ERROR", "缺少 START 节点")
        else:
            starts = [n for n in self.nodes if n.type == "START"]
            if len(starts) > 1:
                add("START_MULTIPLE", "ERROR", "存在多个 START 节点", start)
        end_nodes = self.find_end_nodes()
        if not end_nodes:
            add("END_MISSING", "WARNING", "缺少 END 节点，工作流输出将为空", suggestion="添加 END 节点定义输出变量")

        # 4. 不可达节点（从 START 出发）
        if start is not None:
            reachable = self._reachable(start.id)
            for n in self.nodes:
                if n.id not in reachable and n.type != "START":
                    add("NODE_UNREACHABLE", "WARNING", f"节点「{n.label}」从 START 不可达", n)

        # 5. 悬空节点（无出边且非 END）
        for n in self.nodes:
            if n.type != "END" and not self.get_out_edges(n.id):
                add("NODE_DANGLING", "WARNING", f"节点「{n.label}」没有下游连线", n)

        # 6. 汇聚入边检查（AND 语义依赖所有入边来源已执行；孤立警告）
        for n in self.nodes:
            in_edges = self.get_in_edges(n.id)
            if len(in_edges) > 1:
                # 多入边 = AND 汇聚，正常；但同一源节点多分支连到同一目标属配置错误
                sources = [e.source for e in in_edges]
                if len(sources) != len(set(sources)):
                    add("MERGE_DUP_SOURCE", "ERROR", f"节点「{n.label}」存在来自同一节点的多条入边", n)

        # 7. 类型级校验（由节点注册表补充）
        from service.service_workflow.workflow_engine.nodes import NODE_REGISTRY
        for n in self.nodes:
            validator = NODE_REGISTRY.get(n.type)
            if validator is None:
                add("NODE_TYPE_UNKNOWN", "ERROR", f"未知节点类型: {n.type}", n)
            elif hasattr(validator, "validate_node") and callable(getattr(validator, "validate_node")):
                issues.extend(validator.validate_node(n, self))

        return issues

    def validate_strict(self) -> None:
        """发布用校验：存在 ERROR 即抛 WorkflowGraphError。"""
        issues = self.validate()
        errors = [i for i in issues if i.severity == "ERROR"]
        if errors:
            raise WorkflowGraphError(errors)

    # ---------------- 算法 ----------------

    def _reachable(self, start_id: str) -> set[str]:
        seen, stack = set(), [start_id]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            for e in self.get_out_edges(nid):
                stack.append(e.target)
        return seen

    def has_cycle(self) -> bool:
        """DFS 三色标记检测环（LOOP 循环体回到 LOOP 节点的边不算环，由 LOOP 语义豁免：环上仅含 LOOP 入边的 target）。
        简化实现：普通环（不含 LOOP 回边）返回 True。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n.id: WHITE for n in self.nodes}
        loop_nodes = {n.id for n in self.nodes if n.type in ("LOOP", "ITERATION")}

        def dfs(nid: str) -> bool:
            color[nid] = GRAY
            for e in self.get_out_edges(nid):
                # LOOP 体回边：目标是 LOOP/ITERATION 节点本身 → 语义环，豁免
                if e.target in loop_nodes and nid not in loop_nodes:
                    continue
                if color.get(e.target) == GRAY:
                    return True
                if color.get(e.target) == WHITE and dfs(e.target):
                    return True
            color[nid] = BLACK
            return False

        return any(color[n.id] == WHITE and dfs(n.id) for n in self.nodes)
