# -*- coding: utf-8 -*-
"""「这一次提交想干什么、允不允许干」的唯一判定处：提交意图推导 + 入参校验 + 审批凭据幂等。

读：WorkflowSubmitReq 与 ExecutionStateStore 里的执行现状。写：不落库，产出 SubmitPlan 交编排层执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from service.service_workflow.execution.execution_state import (
    ExecutionStateStore, SubmitContext,
)
from service.service_workflow.execution.pause_state import (
    APPROVE_ACTION, REASON_CHILD_APPROVAL, PendingApproval,
)
from service.service_workflow.schemas.workflow_schema import WorkflowSubmitReq
from service.service_workflow.workflow_engine.engine import (
    STATUS_PAUSED, STATUS_PENDING, SUBMIT_MODE_CONTINUE, SUBMIT_MODE_RETRY,
    compute_graph_hash,
)
from service.service_workflow.workflow_engine.graph import WorkflowGraph
from service.service_workflow.workflow_engine.nodes.approval_nodes import (
    lift_approver_edits, verify_approver,
)

# 提交意图：新开一条执行 / 答复审批后接着跑 / 放弃现状全量重跑
MODE_NEW = "NEW"
MODE_CONTINUE = SUBMIT_MODE_CONTINUE
MODE_RETRY = SUBMIT_MODE_RETRY

RUNNING_MESSAGE = "上一条还在执行中，可等待或取消"
RESUME_MESSAGE = "答复审批只适用于已暂停的执行"

# (workflow_id, workflow_version) → 那一版图（取发布快照还是取草稿由调用方分）
GraphLoader = Callable[[int, int], Awaitable[Optional[dict]]]


class SubmitRejected(Exception):
    """这次提交不被允许：文案直接面向调用方，code 是响应体里的业务码。"""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ForwardedRun:
    """要转给一条子执行的结论集合，以及本级哪几份凭据因它算「已交出去」。"""

    execution_id: str
    decisions: dict = field(default_factory=dict)
    approval_tokens: list[str] = field(default_factory=list)


@dataclass
class SubmitPlan:
    """这次提交要在某条执行上落的动作；duplicated 为真时什么都不做。"""

    mode: str
    execution_id: str = ""
    workflow_id: int = 0
    graph_hash: str = ""
    # 投递失败时这一行该退回的状态；空串表示它还没有前一个状态（新建）
    prev_status: str = ""
    # None = 沿用这一行已有的 inputs；重开时可由调用方换成新一轮业务输入
    values: Optional[dict] = None
    decisions: dict = field(default_factory=dict)
    node_states: dict = field(default_factory=dict)
    round_no: int = 1
    forwards: list[ForwardedRun] = field(default_factory=list)
    duplicated: bool = False

    @property
    def is_new(self) -> bool:
        return self.mode == MODE_NEW

    @property
    def runs_here(self) -> bool:
        """这一跳要不要把本执行抢成待跑：只往外转结论、以及重复答复都不抢。"""
        return not self.forwards and not self.duplicated

    @property
    def restarts(self) -> bool:
        """这一跑是不是从头来：新建与重开都要归零上轮的统计。"""
        return self.mode != MODE_CONTINUE


async def resolve_submit(req: WorkflowSubmitReq, *, load_graph: GraphLoader,
                         user_id: int = 0) -> SubmitPlan:
    """给出这次提交要在哪条执行上落什么动作。

    :param load_graph: 取这一版图的读口（本模块不认识 MySQL 与服务层）
    :param user_id: 审批留痕的兜底身份（API Key 调用为 0，只留审批人填写的那份）
    :raises SubmitRejected: 入参组合或执行现状不允许提交
    不带 executionId 就是新建会话：这里只判入参，插行归编排层（它还要看工作流发布态）。
    """
    if not req.executionId:
        return _plan_new(req)
    context = await ExecutionStateStore.read_submit_context(req.executionId)
    if context is None:
        raise SubmitRejected("执行记录不存在")
    _reject(bool(req.workflowId) and req.workflowId != context.workflow_id,
            "该执行不归属于此工作流")
    _reject(context.is_running, RUNNING_MESSAGE, 409)
    if req.decisions:
        _reject(req.values is not None, "审批结论与新一轮输入不能同时提交")
        conclusions = _conclusions(req, context)
        if conclusions is None:
            return SubmitPlan(mode=MODE_CONTINUE, execution_id=context.execution_id,
                              workflow_id=context.workflow_id, duplicated=True)
        return await _plan_continue(context, conclusions, load_graph, user_id)
    if not req.restart:
        _reject(context.open_approvals, "存在未答复的审批，请先答复或传 restart 重新提交")
    return await _plan_retry(req, context, load_graph)


async def resolve_child_submit(execution_id: str, decisions: dict, *,
                              load_graph: GraphLoader,
                              user_id: int = 0) -> SubmitPlan:
    """把已在父侧解析好的结论落到一条子执行上，返回它要做的事。

    :param decisions: {子图里的审批节点 id: 结论}（父侧登记过每份待办的落点坐标）
    子执行自己也可能在等它的子执行，所以这里同样会产出 forwards —— 每一跳做的动作同构。
    """
    context = await ExecutionStateStore.read_submit_context(execution_id)
    if context is None:
        raise SubmitRejected("等待答复的子执行记录不存在")
    _reject(context.is_running, RUNNING_MESSAGE, 409)
    return await _plan_continue(context, decisions, load_graph, user_id)


# ---------- 意图推导 ----------

def _plan_new(req: WorkflowSubmitReq) -> SubmitPlan:
    """新开一条执行：只认 workflowId 与业务输入这两个键。"""
    _reject(req.decisions or req.restart, "新执行没有可答复的审批")
    _reject(not req.workflowId, "新建执行必须传 workflowId")
    return SubmitPlan(mode=MODE_NEW, workflow_id=req.workflowId,
                      values=dict(req.values or {}))


async def _plan_retry(req: WorkflowSubmitReq, context: SubmitContext,
                      load_graph: GraphLoader) -> SubmitPlan:
    """重开一轮：全量重跑，未答复的审批随那份旧待办清单一起作废。"""
    _, graph_hash = await _load_graph(context, load_graph)
    return SubmitPlan(mode=MODE_RETRY, execution_id=context.execution_id,
                      workflow_id=context.workflow_id, graph_hash=graph_hash,
                      prev_status=context.status,
                      values=(dict(req.values) if req.values is not None else None),
                      node_states=restart_node_states(context.node_states),
                      round_no=context.round_no + 1)


async def _plan_continue(context: SubmitContext, decisions: dict,
                         load_graph: GraphLoader, user_id: int) -> SubmitPlan:
    """接着跑：按「这道审批在不在这张图里」把结论分成落在本级与转给子级两份。"""
    _reject(context.status != STATUS_PAUSED,
            f"{RESUME_MESSAGE}，当前状态 {context.status}；如需重跑请传 restart")
    _reject(not context.stored_graph_hash,
            "历史执行缺少画布指纹，无法安全恢复，请改用 restart 重开")
    open_items = {todo.node_id: todo for todo in context.open_approvals}
    extra = set(decisions) - set(open_items)
    _reject(extra, f"审批结论包含非待审批节点：{sorted(extra)}")
    local: dict = {}
    forwards: dict[str, ForwardedRun] = {}
    for node_id, conclusion in decisions.items():
        child = _child_of(open_items[node_id])
        if child is None:
            local[node_id] = conclusion
            continue
        run = forwards.setdefault(child[0], ForwardedRun(execution_id=child[0]))
        run.decisions[child[1]] = conclusion
        run.approval_tokens.append(open_items[node_id].approval_token)
    _reject(local and forwards,
            "本轮结论里既有本流程自己的审批、又有子工作流的审批，不能一起提交："
            "请先只提交子工作流的结论，子流程跑完后本面板会自动刷新")
    if forwards:
        return SubmitPlan(mode=MODE_CONTINUE, execution_id=context.execution_id,
                          workflow_id=context.workflow_id,
                          forwards=list(forwards.values()))
    return await _plan_local(context, local, load_graph, user_id)


async def _plan_local(context: SubmitContext, decisions: dict,
                      load_graph: GraphLoader, user_id: int) -> SubmitPlan:
    """结论落在本级：审批人身份在这一张图上校验，改过的审批入参抬进本轮 inputs。"""
    graph, graph_hash = await _load_graph(context, load_graph)
    inputs = lift_approver_edits(graph, context.inputs,
                                 [e for c in decisions.values() for e in c["edits"]])
    allowed, review_by = verify_approver(graph, inputs)
    _reject(not allowed, "无审批权限：输入的审批人不符合工作流审批节点的要求")
    for conclusion in decisions.values():
        conclusion["reviewBy"] = review_by or (str(user_id) if user_id else "")
    return SubmitPlan(mode=MODE_CONTINUE, execution_id=context.execution_id,
                      workflow_id=context.workflow_id, graph_hash=graph_hash,
                      prev_status=context.status, values=inputs, decisions=decisions,
                      node_states=resume_node_states(context.node_states, decisions),
                      round_no=context.round_no)


# ---------- 结论形状 ----------

def _conclusions(req: WorkflowSubmitReq, context: SubmitContext) -> Optional[dict]:
    """对外结论 → {本图里的审批节点 id: 结论}；答不了的凭据在这一步整体拦下。

    :return: None 表示这份审批已经不欠着了（答过 / 已交给子执行），调用方据此回 duplicated
    :raises SubmitRejected: 同一份审批答了两遍、或凭据从来没发过
    重复答复是被禁止的行为、不是幂等成功：一次都不落，让调用方看到当前还欠什么。
    """
    tokens = [item.approvalToken for item in req.decisions]
    _reject(len(set(tokens)) != len(tokens), "同一份审批不能在本请求内重复答复")
    todos = []
    for token in tokens:
        todo = context.pause.find_any(token)
        if todo is None:
            raise SubmitRejected(f"审批凭据无效或已失效：{token}")
        if not todo.is_open:
            return None
        todos.append(todo)
    return {todo.node_id: _conclusion(item, todo)
            for item, todo in zip(req.decisions, todos)}


def _conclusion(item, todo: PendingApproval) -> dict:
    """一份对外结论 → 审批执行器直接取用的那份形状。"""
    unknown = set(item.fieldValues or {}) - {f.name for f in todo.fields}
    _reject(unknown, f"字段 {sorted(unknown)} 不属于该审批节点可编辑范围")
    return {"approved": item.action == APPROVE_ACTION,
            "opinion": item.opinion or "",
            "reviewBy": "",
            "edits": todo.to_engine_edits(item.fieldValues)}


def _child_of(todo: PendingApproval) -> Optional[tuple]:
    """这份待办的结论该转给谁（子执行 id, 子图节点 id）；落在本级返回 None。"""
    if todo.reason != REASON_CHILD_APPROVAL:
        return None
    if not todo.target_execution_id or not todo.target_node_id:
        raise SubmitRejected(
            f"等待子工作流的挂起上下文缺少提交目标（节点 {todo.node_id}）："
            "该子执行可能已被重跑或取消，请重新执行本工作流")
    return todo.target_execution_id, todo.target_node_id


# ---------- 节点状态与图 ----------

def restart_node_states(node_states: dict) -> dict:
    """重开一轮的节点状态：只清「本轮跑过什么」，大模型记忆与产出留着。

    review* 必须清：重新执行后审批要从头拿结论，上轮结论留在页面上会被当成本轮已审。
    childExecutionId 也要清：留着【工作流】节点会把上轮那份子执行的结果当成本轮答案。
    """
    cleaned = {}
    for node_id, state in (node_states or {}).items():
        if not isinstance(state, dict):
            continue
        cleaned[node_id] = dict(
            state, status=STATUS_PENDING, duration=0, error=None, skip=False,
            branch=None, review=None, reviewBy=None, reviewOpinion=None,
            reviewDiff=None, childExecutionId=None)
    return cleaned


def resume_node_states(node_states: dict, decisions: dict) -> dict:
    """给出过结论的审批节点退回待跑：不清掉那个标记，本轮又被当成人还没审。"""
    resumed = dict(node_states or {})
    for node_id in decisions:
        state = resumed.get(node_id)
        if isinstance(state, dict):
            resumed[node_id] = dict(state, status=STATUS_PENDING, skip=False)
    return resumed


async def _load_graph(context: SubmitContext, load_graph: GraphLoader) -> tuple:
    """取这一版现在的那张图，顺手把「画布变了」这条硬校验做掉。

    :return: (WorkflowGraph, 图指纹)
    :raises SubmitRejected: 图为空，或拓扑与上次执行不一致（不拦则 skip 与路由随机）
    """
    graph_raw = await load_graph(context.workflow_id, context.workflow_version)
    _reject(not graph_raw, "工作流图为空，无法提交")
    graph_hash = compute_graph_hash(graph_raw)
    _reject(bool(context.stored_graph_hash) and context.stored_graph_hash != graph_hash,
            "画布已变更（节点或连线与上次执行不一致），请重新执行工作流")
    return WorkflowGraph(graph_raw), graph_hash


def _reject(bad, message: str, code: int = 400) -> None:
    """不满足就抛，把「判定 + 抛」压成一行：校验矩阵才读得成一张表。"""
    if bad:
        raise SubmitRejected(message, code)
