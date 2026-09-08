# -*- coding: utf-8 -*-
"""节点执行器协议（参照 MaxKB application/flow/i_step_node.py 的 INode/NodeResult 设计，
重写为 asyncio 原生：execute() 直接是 async，流式内容通过 runtime.emit('node.delta') 推送）。

职责边界：
- 引擎（engine.py）：调度、node.started/completed/failed 事件、计时、上下文写入、汇聚/分支路由
- 节点执行器：只实现 execute(ctx) -> NodeResult，通过 runtime 发 node.delta 流式事件
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from service.service_workflow.workflow_engine.context import ExecutionContext
    from service.service_workflow.workflow_engine.engine import WorkflowRuntime


@dataclass
class NodeResult:
    """节点执行结果。

    - output: 写入上下文的输出变量 {var: value}（下游通过 {{nodeId.var}} 引用）
    - branch_id: 分支路由端口 ID（IF_ELSE/QUESTION_CLASSIFIER/LOOP），None=默认 output 端口
    - stream_text: 流式聚合后的完整文本（LLM 节点填，供 nodeStates 快速展示）
    """
    output: dict = field(default_factory=dict)
    branch_id: Optional[str] = None
    stream_text: Optional[str] = None


class NodeExecutionError(Exception):
    """节点执行失败（引擎捕获后发 node.failed 并终止/走异常分支）。"""


# 节点「返回内容」开关字段名(画布 data 中):显式 false 时该节点的数据事件不广播给客户端
EMIT_OUTPUT_KEY = "emitOutput"


class BaseNodeExecutor:
    """节点执行器基类。子类需设置 node_type 并实现 execute()。"""

    node_type: str = ""

    def __init__(self, node, runtime: "WorkflowRuntime"):
        self.node = node
        self.runtime = runtime

    # ---------- 子类接口 ----------

    async def execute(self, ctx: "ExecutionContext") -> NodeResult:
        raise NotImplementedError

    # ---------- 静态校验钩子 ----------

    @staticmethod
    def validate_node(node, graph) -> list:
        """保存/发布时的节点级静态校验，返回 GraphIssue 列表。默认无检查。"""
        return []

    # ---------- 便捷方法 ----------

    @property
    def config(self) -> dict:
        """节点配置（画布 data 字段）。前端保存的配置结构见 types.ts NodeConfigMap。"""
        return self.node.data or {}

    @property
    def graph(self):
        """所属工作流图（来自 runtime，供复合节点做子图路由）。"""
        return self.runtime.graph

    def cfg(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def emit_output_enabled(self) -> bool:
        """节点「返回内容」开关:显式 false 才关闭,缺省/其他值视为开启。

        关闭时节点数据事件(node.completed 的 output、node.delta token 流)
        不广播给客户端;节点执行与数据持久化(落库)与开关无关,始终进行。
        """
        return self.node.data.get(EMIT_OUTPUT_KEY, True) is not False

    async def emit_delta(self, token: str) -> None:
        """流式节点推送增量内容(LLM 等);返回内容开关关闭时跳过广播。"""
        if not token or not self.emit_output_enabled():
            return
        await self.runtime.emit("node.delta", nodeId=self.node.id, token=token)

    def require_model_id(self) -> int:
        model_id = self.cfg("modelId")
        if not model_id:
            raise NodeExecutionError(f"节点「{self.node.label}」未配置模型")
        return int(model_id)


# ==================== 校验工具（供 validate_node 使用） ====================


def issue(code, severity, message, node, suggestion=None):
    from service.service_workflow.workflow_engine.graph import GraphIssue
    return GraphIssue(code=code, severity=severity, message=message,
                      node_id=node.id, node_label=node.label, node_type=node.type,
                      suggestion=suggestion)
