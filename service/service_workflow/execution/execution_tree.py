# -*- coding: utf-8 -*-
"""父子执行之间的交接：子执行欠着的审批往上冒，子执行收尾了把父执行叫醒。

读：父子两行的执行现状。写：只经 ExecutionStateStore 推父行的状态与代次。
结论往下投那一跳不在这里：它是一次普通的提交，判定在 submit_resolver 的 forwards。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.execution import approval_projection
from service.service_workflow.execution.execution_state import ExecutionStateStore
from service.service_workflow.execution.pause_state import PauseState
from service.service_workflow.models.workflow_entity import WorkflowExecution
from service.service_workflow.workflow_engine.engine import (
    STATUS_AWAITING, STATUS_PAUSED, STATUS_RUNNING, TERMINAL_STATUSES,
)


@dataclass
class WaitingParent:
    """正因这份子执行而挂着的那条父执行。

    :param node_id: 父图里卡在子流程上的那个节点，唤醒时只把它退回待跑
    :param generation: 读到的挂起代次，CAS 依据——不符即已经有人推过或换了一轮
    """

    execution_id: str
    node_id: str
    generation: int
    trigger_type: str


def bubble_up(child_execution_id: str, pause: PauseState, node_states: dict,
              awaiting_node_id: Optional[str]) -> Optional[dict]:
    """子执行欠着的那道审批 → 父侧镜像要用的那份上下文（要审的数据 + 提交坐标）。

    提交坐标只指本条子执行：多层嵌套时由子执行自己再往下转一跳，校验与审批人
    判定才能一直落在真正有审批节点的那张图上。
    一份都取不到时返回 None（没人欠着 / 旧数据无 pauseState）：父侧退回「只说在
    等子流程」的形。
    """
    contexts = approval_projection.approval_contexts(pause, node_states)
    if not contexts:
        return None
    node_id = (awaiting_node_id if awaiting_node_id in contexts
               else next(iter(contexts)))
    return dict(contexts[node_id], approvalExecutionId=child_execution_id,
                approvalNodeId=node_id)


async def _running_sibling(session, states: dict, node_id: str) -> Optional[str]:
    """父本轮还有别的节点在等一份正跑着的子执行 → 返回那条子执行 id。

    兄弟没收尾就不推父执行：父重跑到那个节点会读到 RUNNING 的子行（拿不到结果），
    把那个节点判失败。不会把父永久卡住——那条子执行进终态时会再走一遍本函数。
    """
    for other_id, state in states.items():
        if other_id == node_id or not isinstance(state, dict):
            continue
        if state.get("status") != STATUS_AWAITING:
            continue
        sibling = state.get("childExecutionId")
        if not sibling:
            continue
        row = await session.get(WorkflowExecution, sibling)
        if row is not None and row.status == STATUS_RUNNING:
            return sibling
    return None


async def find_waiting_parent(child_execution_id: str) -> Optional[WaitingParent]:
    """找出「因这份子执行而挂着」的那条父执行，不动任何状态。

    子行没进终态、没有父在等、父行不在 PAUSED、父等的不是这一份、兄弟子执行还在
    跑——五种情况都给 None。
    """
    async with mysql_client.get_session() as session:
        child = await session.get(WorkflowExecution, child_execution_id)
        if child is None or not child.parent_exec_id or not child.parent_node_id:
            return None
        if child.status not in TERMINAL_STATUSES:
            return None
        parent = await session.get(WorkflowExecution, child.parent_exec_id)
        if parent is None or parent.status != STATUS_PAUSED:
            return None
        states = dict(parent.node_states or {})
        state = states.get(child.parent_node_id)
        if not isinstance(state, dict) or state.get("status") != STATUS_AWAITING:
            return None
        recorded = state.get("childExecutionId")
        if recorded and recorded != child_execution_id:
            # 父等的是另一份子执行（父被重跑过），这份的结果不算数
            return None
        sibling = await _running_sibling(session, states, child.parent_node_id)
        if sibling is not None:
            log.info("resume parent exec={} deferred: sibling child exec={} still running",
                     parent.id, sibling)
            return None
        return WaitingParent(execution_id=parent.id, node_id=child.parent_node_id,
                             generation=int(parent.pause_generation or 1),
                             trigger_type=parent.trigger_type)


async def wake_parent_execution(child_execution_id: str) -> Optional[WaitingParent]:
    """子执行进终态后，把正等它的父执行推回 RUNNING，返回这条父执行。

    节点状态退回 PENDING（结果由父节点执行器按「子已终态」分支自取）、上轮结论清掉、
    代次 +1；代次不符即已有人在推，同样给 None。谁来让它真的跑起来是调用方的事。
    """
    waiting = await find_waiting_parent(child_execution_id)
    if waiting is None:
        return None
    if await ExecutionStateStore.wake_to_running(
            waiting.execution_id, expected_generation=waiting.generation,
            reset_node_ids=[waiting.node_id]) == 0:
        return None
    log.info("resume parent exec={} from child exec={} node={}",
             waiting.execution_id, child_execution_id, waiting.node_id)
    return waiting
