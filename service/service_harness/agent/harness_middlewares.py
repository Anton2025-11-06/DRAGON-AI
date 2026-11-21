# -*- coding: utf-8 -*-
"""智能体中间件：把"不许提前收敛"从提示词里的口号变成运行时机制。

agentscope 的 ReAct 循环只看一件事——本轮推理有没有产出工具调用；模型给了一段
纯文本就判 `ReplyFinishedReason.COMPLETED` 并退出（见 `_next_action` Step 3）。
小模型最常见的失效正好落在这条路径上：清单里还挂着 pending，它就宣布完成。

`AutonomyGuard` 卡在 `on_reply` 这个切面上：`_reply_impl` 已经把退出事件交出来了，
只要它**没有**穿过中间件链到达外层（外层用 `_receive_reply_end` 记这个事实），
循环就会把 `final_msg` 清空再跑一轮。于是这里在放行前做一次判定：

- 只处理 `COMPLETED`（用户中断、超过 max_iters、报错三类收尾都照原样放行）；
- 只在这轮真的**有过工具调用**之后才拦（否则"照着用户要求只回答一个问题"
  这种纯文本轮次会被硬拖着去干活）；
- 只在建了清单且仍有未完成项时拦——清单是模型自己立下的承诺，拿它当终止权
  比拿它自己的感觉当终止权可靠；
- 有预算（默认 3 次）：提醒完还是坚持收尾就放行，把差距写进结论里，
  不做无界拉扯（外层也有一道"连续两次吞事件之间必须有过进展"的 RuntimeError 兜底）。

每次纠正都同时做两件事：往 state 塞一条 HintBlock（模型看得见的提醒），并往外
发一条 `HintBlockEvent`（worker 看得见的信号）。后者由 worker 转成 `turn.nudge`
SSE 帧给前端渲染，口径见 harness_pubsub.EVT_TURN_NUDGE；除了 source 是我们这个
来源的 hint，其余 hint（比如运行时状态注入）worker 一律不发帧。

实例跟着 Agent 每轮重建（见 harness_agent.build_agent：Toolkit 与 Agent 一起重建），
所以计数器天然按"一轮对话"归零，不能做成模块级单例——那样预算会被历轮耗光。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Callable

from agentscope.event import HintBlockEvent, ReplyEndEvent, ReplyFinishedReason
from agentscope.message import HintBlock
from agentscope.middleware import MiddlewareBase

from common.common_log.log_init import log
from service.service_harness.agent.harness_prompts import CONTINUE_NUDGE_HINT

# 视为"还没做完"的任务状态（agentscope 的 Task.state 取值）
UNFINISHED_TASK_STATES = ("pending", "in_progress")
# 注入块的 source：前端/排查时辨认这条 hint 是谁塞进来的，
# worker 侧也按它过滤"哪些 hint 值得发帧"（同一字符串，两处不另立约定）
NUDGE_SOURCE = '{"label": "System", "sublabel": "Autonomy Guard"}'


class AutonomyGuard(MiddlewareBase):
    """任务清单未走完就想用纯文本收尾时，压掉本轮 ReplyEndEvent 再推它一轮。"""

    def __init__(self, max_nudges: int = 3) -> None:
        self.max_nudges = max_nudges
        self._nudges = 0

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        # 本轮是否出现过工具调用：没有任何调用的收尾按"直接回答"处理，不拦
        saw_tool_call = False
        async for item in next_handler():
            if type(item).__name__.startswith("ToolCall"):
                saw_tool_call = True
            if (
                isinstance(item, ReplyEndEvent)
                and item.finished_reason == ReplyFinishedReason.COMPLETED
                and saw_tool_call
                and self._should_nudge(agent)
            ):
                self._nudges += 1
                unfinished = self._unfinished_count(agent)
                hint = CONTINUE_NUDGE_HINT.format(
                    unfinished=unfinished,
                    nudge=self._nudges,
                    max_nudges=self.max_nudges,
                )
                block = HintBlock(source=NUDGE_SOURCE, hint=hint)
                agent.state.append_context(agent.name, [block])
                # 腾出一格迭代额度：正好在最后一步迭代上压掉 COMPLETED，下一轮
                # 直接变成 EXCEED_MAX_ITERS，外层会判成"吞事件却没进展"报错
                try:
                    agent.state.cur_iter = min(
                        agent.state.cur_iter,
                        agent.react_config.max_iters - 1,
                    )
                except (AttributeError, TypeError):  # 拿不到额度就不动，不牵连本轮
                    pass
                log.info(
                    "harness autonomy guard: turn={} agent={} nudged={}/{} unfinished={}",
                    agent.state.reply_id, agent.name, self._nudges,
                    self.max_nudges, unfinished,
                )
                # 提醒本身给模型（上面那条），这条事件给 worker → turn.nudge 帧
                yield HintBlockEvent(
                    reply_id=item.reply_id,
                    block_id=block.id,
                    source=NUDGE_SOURCE,
                    hint=hint,
                    metadata={"nudge": self._nudges,
                              "max_nudges": self.max_nudges,
                              "unfinished": unfinished},
                )
                # 关键：这一条 ReplyEndEvent 不往外发，_reply_impl 恢复后继续循环
                continue
            yield item

    def _should_nudge(self, agent: Any) -> bool:
        return (
            self._nudges < self.max_nudges
            and self._unfinished_count(agent) > 0
        )

    @staticmethod
    def _unfinished_count(agent: Any) -> int:
        """清单里未完成项数（拿不到结构时按 0 处理，绝不因钩子把轮次带崩）。"""
        try:
            tasks = agent.state.tasks_context.tasks
        except AttributeError:
            return 0
        return sum(
            1 for t in tasks if getattr(t, "state", None) in UNFINISHED_TASK_STATES
        )
