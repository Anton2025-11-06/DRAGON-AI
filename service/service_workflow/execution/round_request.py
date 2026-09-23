# -*- coding: utf-8 -*-
"""「本轮要消费什么」这份事实：对话轮次 + 待喂给审批节点的结论。

读：worker 重建运行时（ExecutionStateStore.read_round_request）。
写：API 进程提交抢占（claim_for_run）；worker 收尾时清空已消费的结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# variables 列里本事实所在的键
ROUND_REQUEST_KEY = "roundRequest"


@dataclass
class RoundRequest:
    """一次提交留给 worker 的待消费数据。

    提交模式与本轮入参不住这里：它们各有其列（submit_mode / inputs）。
    decisions 形如 {审批节点 id: {"approved", "opinion", "reviewBy", "edits"}}，
    即审批执行器直接取用的那份形状。
    """

    round_no: int = 1
    decisions: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"round": self.round_no, "decisions": self.decisions}

    @classmethod
    def from_dict(cls, data) -> "RoundRequest":
        payload = data if isinstance(data, dict) else {}
        decisions = payload.get("decisions")
        return cls(
            round_no=int(payload.get("round") or 1),
            decisions=dict(decisions) if isinstance(decisions, dict) else {},
        )

    def with_decisions_consumed(self) -> "RoundRequest":
        """结论已被 worker 取走：只剩轮次的那份（轮次是跨轮事实，不能跟着一起丢）。"""
        return RoundRequest(round_no=self.round_no)
