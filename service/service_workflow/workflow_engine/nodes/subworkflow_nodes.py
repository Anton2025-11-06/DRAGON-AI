# -*- coding: utf-8 -*-
"""WORKFLOW 工作流节点：在 workflow 服务内部直接调执行引擎跑一条子工作流（嵌套调用）。

调用链口径（需求：api-key 只作为「用哪套凭证/限流」的配置项记下来）：
1. 先校验 `apiKeyId` 指向的 key：属于该子工作流、存在、ACTIVE、未过期，再按 key 登记的
   rateLimit 过 QPS 限流 —— 与网关 workflow_api_proxy 同一口径；任一不通过即节点失败，
   不静默降级（否则用户以为在用某个 key 跑，实际绕过了鉴权与限流）。
2. 版本：`versionMode=LATEST` → tb_workflow.current_version；`SPECIFIC` → 校验那一版
   本确实存在且已发布（发布快照不可变，指定版本即锁定图拓扑）。
3. 入参绑定行与 TOOL / CODE 节点同一份语义（py_sandbox.resolve_kwargs）。
4. 子执行在本进程内跑完（service.run_child），子流程的节点明细/事件/暂停全部照普通
   执行落库；子流程里有审批节点 → 子执行落 PAUSED → 父节点记 childExecutionId 后抛
   AwaitingApproval，父执行跟着 PAUSED（不占 worker 任务槽、不受节点超时约束）。
5. 子执行进终态时由 service 层把父执行按 CONTINUE 再提交推起来；父本轮重跑到本节点
   走下面的「已有子执行 → 直接取结果」分支，绝不会把子流程再跑一遍。

本模块同时是「子工作流调用核」的归属地（check_api_key / resolve_version /
invoke_child_workflow）：LLM 节点插入的工作流工具走的是同一条核，两处对凭证、
版本策略、挂起-恢复的口径只有一份实现。
"""
from __future__ import annotations

import json
from typing import Optional

from common.common_log.log_init import log
from service.service_workflow.workflow_engine import py_sandbox
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.nodes.approval_nodes import (
    approver_field_name,
)
from service.service_workflow.workflow_engine.nodes.base import (
    APPROVAL_SCOPE_DOWNSTREAM, AwaitingApproval, BaseNodeExecutor, NodeResult,
    NodeExecutionError, issue,
)

# 父节点挂起的外发标记：结论要提交给**子执行**（childExecutionId），不是父执行
CHILD_AWAITING_KIND = "CHILD_WORKFLOW"

# 子工作流 END 输出里最常当作文本回答的键（按此顺序挑一个当 text）
TEXT_KEYS = ("answer", "text", "output", "result")


def child_output_as_text(outputs: Optional[dict]) -> str:
    """子执行 END 输出 → 一段可读文本（下游引用 {nodeId.text} 时的取值口径）。

    子工作流的 END 输出形态由它自己定（单变量 / 多变量 / 模板），父侧只能挑：
    优先几个公认的回答键，其次单键直取，其余整体序列化 —— 至少要让人看出子流程
    答了什么，而不是拿到一个 dict 的 repr。
    """
    data = outputs or {}
    if not isinstance(data, dict) or not data:
        return ""
    for key in TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    if len(data) == 1:
        value = next(iter(data.values()))
        return value if isinstance(value, str) else _dumps(value)
    return _dumps(data)


def child_output_payload(outputs: Optional[dict], output_var: str) -> dict:
    """子执行 END 输出 → 父节点输出 {output_var: 全量输出, text: 可读文本}。

    首跑与恢复轮共用这一份映射：两处各写一份迟早会分叉（下游引用的键名对不上）。
    """
    data = outputs if isinstance(outputs, dict) else {}
    return {output_var: data, "text": child_output_as_text(data)}


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ==================== 子工作流调用核（【工作流】节点与 LLM 的工作流工具共用） ====================


async def check_api_key(api_key_id: int, workflow_id: int, label: str) -> None:
    """api-key 校验 + 限流（与网关同一口径）：不通过直接节点失败。"""
    from common.common_utils.rate_limiter import RateLimiter
    from service.service_workflow.services.workflow_apikey_service import (
        WorkflowApiKeyService,
    )

    keys = await WorkflowApiKeyService.list_by_workflow(workflow_id)
    row = next((k for k in keys if int(k.get("id") or 0) == api_key_id), None)
    if row is None:
        raise NodeExecutionError(
            f"节点「{label}」的 API Key 不存在或不属于该子工作流")
    api_key = str(row.get("apiKey") or "")
    # verify 顺带刷使用统计并把过期 key 置 EXPIRED，与第三方走网关时同一份判定
    cfg = await WorkflowApiKeyService.verify(api_key)
    if not cfg:
        raise NodeExecutionError(f"节点「{label}」的 API Key 已停用或已过期")
    rate_limit = int(cfg.get("rateLimit") or 0)
    if rate_limit > 0 and not await RateLimiter.check_workflow_api_qps(api_key, rate_limit):
        raise NodeExecutionError(
            f"子工作流调用频率超过 API Key 限制（每分钟 {rate_limit} 次），请稍后再试")


async def resolve_version(workflow_id: int, cfg: dict, label: str) -> int:
    """版本策略 → 实际版本号（LATEST 取当前版本，SPECIFIC 校验那一版已发布）。

    cfg 只读三个键：versionMode / version / workflowName（展示用）。【工作流】节点传
    整份节点配置，LLM 的工具绑定传同名字段子集 —— 两种入口的版本语义必须是一份。
    """
    from service.service_workflow.services.workflow_service import WorkflowService

    detail = await WorkflowService.detail(workflow_id)
    if detail is None:
        raise NodeExecutionError(f"节点「{label}」的子工作流不存在（已被删除？）")
    if str(cfg.get("versionMode") or "LATEST").upper() != "SPECIFIC":
        version = int(detail.get("currentVersion") or 0)
        if version <= 0:
            raise NodeExecutionError(
                f"子工作流「{detail.get('name')}」尚未发布，请先发布后再调用")
        return version
    raw = cfg.get("version")
    if not raw:
        raise NodeExecutionError(f"节点「{label}」选择了指定版本但未填版本号")
    version = int(raw)
    info = await WorkflowService.version_detail(workflow_id, version)
    if info is None or not info.get("published"):
        raise NodeExecutionError(
            f"子工作流「{detail.get('name')}」的 v{version} 不存在或未发布")
    return version


def build_awaiting_context(node_id: str, node_label: str, *, child_id: str,
                           workflow_id, workflow_name: str = "",
                           approval: Optional[dict] = None) -> dict:
    """父侧镜像子执行那道审批的挂起事实：要审的内容 + 该往哪儿提交结论。

    父执行卡在子流程的审批上，但审批节点不在父图里、也不在父画布上。页面只拿得到
    父这一侧的挂起上下文，所以子执行那道审批要审什么（可编辑的上游数据）必须镜像
    上来：否则父侧用户既看不到要审的内容、也找不到提交入口，只能看着一条子执行干等。

    approval 来自 WorkflowExecutionService.get_child_result（子执行那份挂起事实 +
    两个提交坐标），拿不到时退回“只说在等谁”的形（老数据/快照缺失）。
    editableInputs 始终给数组而不是省掉：审批表单按这个键取数，给 undefined 会让面板
    直接报错；拿不到就是空数组（那道审批没有可编辑的上游数据）。

    子执行的暂停范围与审批人清单不跟着过来：前者是子图自己的语义，后者由子执行按
    自己的图与 inputs 校身份 —— 跳到父侧只会多出一行用户看不懂的“审批人：”。

    父侧只需要知道：等哪条子执行的哪个节点（childExecutionId/approvalNodeId）、
    给人看的名字（nodeName/approvalNodeName/workflowName）、要审的数据。
    """
    data = approval or {}
    return {
        "kind": CHILD_AWAITING_KIND,
        "nodeId": node_id,
        "nodeName": node_label,
        "childExecutionId": child_id,
        "workflowId": workflow_id,
        "workflowName": workflow_name or "",
        # 结论的提交目标：本节点的直接子执行（它在等更深的子流时自己会再往下转）
        "approvalExecutionId": data.get("approvalExecutionId") or child_id,
        "approvalNodeId": data.get("approvalNodeId") or "",
        "approvalNodeName": data.get("nodeName") or "",
        "editableInputs": list(data.get("editableInputs") or []),
    }


def parent_approver_identity(runtime) -> Optional[list]:
    """父流自己那份审批人身份：父图开始节点「审批入参」字段的值（父图没这个字段则 None）。

    起子执行时带上它，子流那道审批的身份校验才有依据可查（口径见
    `approval_nodes.seed_approver_identity`）。工作流节点与 LLM 的内嵌子流工具共用
    本函数：两条入口的审批人语义必须是同一份。
    """
    name = approver_field_name(getattr(runtime, "graph", None))
    if not name:
        return None
    inputs = getattr(getattr(runtime, "ctx", None), "inputs", None) or {}
    return inputs.get(name)


async def invoke_child_workflow(
    *, workflow_id: int, api_key_id: int, cfg: dict, runtime, node_id: str,
    label: str, output_var: str, inputs: Optional[dict] = None,
    resumed_child_id: Optional[str] = None,
    cache_key: Optional[str] = None) -> dict:
    """执行（或取回）一次子工作流，返回父节点输出 payload。

    子流程 PAUSED → 把 childExecutionId 记进父节点状态后抛 AwaitingApproval，
    父执行跟着落 PAUSED（结论属于那条子执行，不在本节点收）。

    resumed_child_id 非空即恢复轮：只按 id 取结果、绝不重新起一份子执行。inputs
    只在首跑时需要（恢复轮上轮已解析过，本轮再解析可能被未执行分支卡住）。

    cache_key(子workflow_id + 子inputs) 非空即 LLM 工具路径：本节点已用同一组入参起过子执行时直接复用那份
    结果（当恢复轮取），不重起——否则带审批的子流程每次重调都会再要一遍审批。
    首跑起出新子执行后按 cache_key 记下它的 id，供后续同参调用命中。
    """
    from service.service_workflow.services.workflow_execution_service import (
        WorkflowExecutionService,
    )
    # 状态常量住在 engine（nodes 包由 engine 导入，模块级 import 会绕成循环）
    from service.service_workflow.workflow_engine.engine import (
        STATUS_CANCELLED, STATUS_COMPLETED, STATUS_PAUSED,
    )

    state = runtime.node_states.get(node_id)

    child_id = resumed_child_id
    # 同参复用：LLM 工具带了 cache_key，本节点此前已用同一组入参起过一份子执行，
    # 就把它当恢复轮直接取那份结果，绝不重起
    if not child_id and cache_key and state is not None:
        child_id = (state.childToolResults or {}).get(cache_key)

    result = None
    if child_id:
        try:
            result = await WorkflowExecutionService.get_child_result(child_id)
        except ValueError as e:
            # 缓存指向的子执行已不存在（被清理）：作废这条缓存，按首跑重起一份；
            # 恢复轮（resumed_child_id）取不到就是真错，不能默默重跑，直接上抛
            if not resumed_child_id and cache_key and state is not None:
                log.warning("workflow tool cache stale exec={} node={} child={}: {}",
                            runtime.execution_id, node_id, child_id, e)
                if isinstance(state.childToolResults, dict):
                    state.childToolResults.pop(cache_key, None)
                child_id = None
            else:
                raise

    if not child_id:
        await check_api_key(int(api_key_id), workflow_id, label)
        version = await resolve_version(workflow_id, cfg, label)
        result = await WorkflowExecutionService.run_child(
            workflow_id=workflow_id, version=version, inputs=inputs or {},
            user_id=runtime.user_id, parent_exec_id=runtime.execution_id,
            parent_node_id=node_id,
            approver_identity=parent_approver_identity(runtime))
        child_id = result["executionId"]
        # 记下这份子执行：同参数的再次调用直接复用它的终态结果
        if cache_key and state is not None:
            if not isinstance(state.childToolResults, dict):
                state.childToolResults = {}
            state.childToolResults[cache_key] = child_id

    status = result.get("status")
    if status == STATUS_COMPLETED:
        return child_output_payload(result.get("outputs"), output_var)
    if status == STATUS_PAUSED:
        # 子流程卡在审批：父节点跟着在节点边界挂起（本轮拿不到子流程的终态）
        if state is not None:
            state.childExecutionId = child_id
        raise AwaitingApproval(
            node_id,
            build_awaiting_context(node_id, label, child_id=child_id,
                                   workflow_id=workflow_id,
                                   workflow_name=cfg.get("workflowName") or "",
                                   approval=result.get("approval")),
            APPROVAL_SCOPE_DOWNSTREAM)
    if status == STATUS_CANCELLED:
        raise NodeExecutionError(f"子工作流执行已被取消（executionId={child_id}）")
    raise NodeExecutionError(
        f"子工作流执行失败: {result.get('errorMessage') or '未知错误'}"
        f"（executionId={child_id}）")


class WorkflowNodeExecutor(BaseNodeExecutor):
    """工作流节点：按 api-key 校验 + 限流后，进程内执行一条已发布子工作流。"""

    node_type = "WORKFLOW"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        workflow_id = cfg.get("workflowId")
        api_key_id = cfg.get("apiKeyId")
        if not workflow_id or not api_key_id:
            raise NodeExecutionError(
                f"节点「{self.node.label}」未选择子工作流或未配置执行用 API Key")
        child_id = self._resumed_child_id()
        payload = await invoke_child_workflow(
            workflow_id=int(workflow_id), api_key_id=int(api_key_id), cfg=cfg,
            runtime=self.runtime, node_id=self.node.id, label=self.node.label,
            output_var=str(cfg.get("outputVariable") or "result"),
            inputs=None if child_id else py_sandbox.resolve_kwargs(
                cfg.get("inputs") or [], ctx.resolve_ref),
            resumed_child_id=child_id)
        return NodeResult(output=payload)

    # ---------- 挂起与恢复 ----------

    def _resumed_child_id(self) -> Optional[str]:
        state = self.runtime.node_states.get(self.node.id)
        return getattr(state, "childExecutionId", None) if state is not None else None

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("workflowId"):
            issues.append(issue("WORKFLOW_NO_TARGET", "ERROR", "工作流节点未选择子工作流", node))
        if not data.get("apiKeyId"):
            issues.append(issue("WORKFLOW_NO_API_KEY", "ERROR",
                                "工作流节点未选择执行用 API Key", node,
                                suggestion="子工作流需要先发布并创建 API Key"))
        if str(data.get("versionMode") or "LATEST").upper() == "SPECIFIC" and not data.get("version"):
            issues.append(issue("WORKFLOW_NO_VERSION", "ERROR",
                                f"工作流节点「{node.label}」选择了指定版本但未填版本号", node))
        return issues
