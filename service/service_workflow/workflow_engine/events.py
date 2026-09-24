# -*- coding: utf-8 -*-
"""执行事件定义与发射：节点事件 → Redis Pub/Sub（无本地缓存）。

事件协议对齐前端 types.ts WORKFLOW_RUNTIME_EVENT_TYPES：
    workflow.started
    node.started
    node.delta
    node.completed
    node.failed
    node.timeout
    node.cancelled
    node.paused
    node.tool_call
    node.tool_result
    workflow.completed
    workflow.failed
    workflow.cancelled
    workflow.paused
    workflow.resumed

SSE 帧格式：event: {type}\ndata: {json}\n\n（前端 runtime-events.ts 按行解析）。

node.tool_call / node.tool_result 由大模型节点插入工具后补发（见
nodes/ai_nodes.py::_emit_tool_call/_emit_tool_result）：前者是模型要求调哪个工具、
传了什么参数，后者是这一次调用的结果摘要（按 TOOL_EVENT_SUMMARY_CHARS 截断，
完整结果在节点输出的 toolCalls 里；不勾 emitToolResult 时事件不带摘要，父侧取
子执行结果也看不到那份返回值）。两者靠 payload 的 toolCallId 配对。payload 的
toolName 就是工具登记时的真实名称（工具名不做字符归一；只有重名时引擎内部注册表
会另用带来路前缀的 key 占位，那个名字不外泄）。
不受节点「返回内容」开关（emitOutput）约束——那是给面向业务的输出内容用的，这两个
是过程可见性；工具结果是否作为内容推给客户端由 emitToolResult 单独控制。

node.timeout / node.cancelled 由并行屏障补发（见 engine._settle_branch_nodes）：
「任一完成 + 等待超时」下被砍分支里的节点只有 node.started、没有终态时，
前端会一直显示蓝色「执行中」，这两个事件把它们收敛为黄色「已超时」/
灰色「已取消」。

事件通道（全部走 Redis Pub/Sub，无进程内缓冲/注册表）：
- 引擎每次 emit 立即交给 service 注入的 publish_hook，实时 PUBLISH 到
  Redis 频道 workflow:evt:{execution_id}（含 node.delta token 级流式）；
- 任意进程的 SSE 订阅者 SUBSCRIBE 同一频道实时消费（见
  service_workflow/services/event_pubsub.py）；
- 引擎层不直接依赖 Redis：hook 缺失（直驱引擎测试等场景）时事件仅丢弃。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class WorkflowEvent:
    type: str
    execution_id: str
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    payload: dict = field(default_factory=dict)

    def to_sse_frame(self) -> str:
        data = {"type": self.type, "executionId": self.execution_id,
                "timestamp": self.timestamp, **self.payload}
        # 前端 StreamTokenEvent 等字段名 camelCase，payload 构造时已转换
        return f"event: {self.type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    def to_dict(self) -> dict:
        return {"type": self.type, "executionId": self.execution_id,
                "timestamp": self.timestamp, **self.payload}

    def to_json(self) -> str:
        """序列化为频道消息（跨进程 Pub/Sub 发布用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class EventBus:
    """单次执行的事件发射器（无本地缓存，事件实时走 Redis Pub/Sub）。

    - publish: 引擎侧调用 → 立即交给 publish_hook（service 装配时注入），
      实时 PUBLISH 到 Redis 频道 workflow:evt:{execution_id}；
    - 消费方是任意进程的 SSE 订阅者（subscribe_event_channel），
      本地不做任何缓冲/队列（需求：全部走 redis pub and sub）。
    """

    def __init__(self, execution_id: str,
                 publish_hook: Optional[Any] = None,
                 pause_generation: int = 0):
        """
        :param publish_hook: 跨进程发布回调（service 注入）：
            async (WorkflowEvent) -> None，每次发布实时 PUBLISH 到 Redis 频道；
            约定内部捕获异常，失败不阻断本地执行。
        :param pause_generation: 本次运行的挂起代次（service 从执行行读出后植入），
            非 0 时盖进每一帧 payload，消费方据此丢弃过期帧
            （docs/workflow-execution-contract.md §2.3）。同一次运行的所有帧同号。
        """
        self.execution_id = execution_id
        self.publish_hook = publish_hook
        self.pause_generation = int(pause_generation or 0)

    async def publish(self, event: WorkflowEvent) -> None:
        if self.publish_hook is not None:
            await self.publish_hook(event)

    # ---------- 便捷发布 ----------

    async def emit(self, event_type: str, **payload) -> None:
        # payload 字段名 camelCase 由调用方保证（nodeId/duration 等）
        if self.pause_generation:
            payload["pauseGeneration"] = self.pause_generation
        await self.publish(WorkflowEvent(event_type, self.execution_id, payload=payload))