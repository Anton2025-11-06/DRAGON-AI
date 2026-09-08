# -*- coding: utf-8 -*-
"""asyncio 工作流执行引擎（参照 MaxKB WorkflowManage 调度策略重写）。

移植自 MaxKB 的核心调度算法：
- run_chain_manage 递归调度  →  _schedule 递归 async 任务
- get_next_node_list 分支路由 →  _next_nodes（branch 端口匹配）
- dependent_node_been_executed AND 汇聚 →  _barrier_ready（入边计数 + 活跃端口匹配）
- 多出边按 y 坐标并行       →  asyncio.gather
- 节点异常分支（exception） →  result.branch_id="exception" 走 branch:exception 端口

新增能力（MaxKB 无 / 前端契约要求）：
- 断点暂停（breakpoints）/ 恢复（resume）/ 取消（cancel）
- 快照（variables + nodeStates + pending nodes）落库与恢复
- 复合节点子图执行（run_subgraph：LOOP/ITERATION/PARALLEL 的循环体/分支体）
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from service.service_workflow.workflow_engine.comparators import evaluate_expression
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.events import EventBus, WorkflowEvent
from service.service_workflow.workflow_engine.graph import (
    OUTPUT_HANDLE, WorkflowGraph, is_branch_handle, branch_id_of,
)
from service.service_workflow.workflow_engine.nodes import NODE_REGISTRY
from service.service_workflow.workflow_engine.nodes.base import (
    EMIT_OUTPUT_KEY, NodeExecutionError, NodeResult,
)
from common.common_log.log_init import log
from common.common_httpx.httpx import httpx_pool

MAX_PARALLEL_BRANCHES = 50  # 单执行并行分支上限(防画布错配导致的任务爆炸)
NODE_TIMEOUT_DEFAULT = 600   # 单节点执行超时(秒):防外部/工具节点永久挂起(MaxKB 无此保护)

# ---------- 执行/节点状态常量(与 tb_workflow_execution.status 及前端展示一致,禁止写裸字符串) ----------
STATUS_PENDING = "PENDING"      # 初始状态(执行未调度 / 节点未开始)
STATUS_RUNNING = "RUNNING"      # 执行中 / 节点运行中
STATUS_PAUSED = "PAUSED"        # 暂停(仅执行记录与运行时使用)
STATUS_COMPLETED = "COMPLETED"  # 成功完成
STATUS_CANCELLED = "CANCELLED"  # 取消(仅执行记录与运行时使用)
STATUS_FAILED = "FAILED"        # 失败


class WorkflowCancelled(Exception):
    pass


class NodeState:
    """节点执行状态（对齐前端 NodeExecutionState）。"""

    __slots__ = ("order", "status", "input", "output", "error", "duration", "started_at")

    def __init__(self):
        self.order: int = 0
        self.status: str = STATUS_PENDING
        self.input: Any = None
        self.output: Any = None
        self.error: Optional[str] = None
        self.duration: int = 0
        self.started_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "duration": self.duration,
        }


class WorkflowRuntime:
    """单次执行运行时（角色对等 MaxKB WorkflowManage）。

    生命周期（需求 4：全部走 execute_async，由 arq worker 任务驱动）：
        runtime = WorkflowRuntime(graph, execution_id, inputs, ...)
        await runtime.run()
        # 事件：节点事件(含 node.delta)经 EventBus pub hook 实时 PUBLISH
        # 到 Redis 频道（Pub/Sub），无进程内注册表与本地事件缓存
    """

    def __init__(self, graph: WorkflowGraph, execution_id: str,
                 inputs: Optional[dict] = None,
                 breakpoints: Optional[list] = None,
                 breakpoint_conditions: Optional[dict] = None,
                 model_provider=None,
                 http_client=None,
                 event_bus: EventBus = None,
                 node_persist_hook=None,
                 state_persist_hook=None,
                 status_check_hook=None,
                 node_timeout: int = NODE_TIMEOUT_DEFAULT,
                 trigger_type: str = "DEBUG",
                 user_id: int = 0,
                 workflow_id: int = 0,
                 workflow_version: int = 0):
        self.graph = graph
        self.execution_id = execution_id
        self.trigger_type = trigger_type
        self.user_id = user_id
        self.workflow_id = workflow_id
        self.workflow_version = workflow_version
        self.ctx = ExecutionContext(graph, inputs)
        self.breakpoints: set[str] = set(breakpoints or [])
        # 条件断点：node_id → 自由文本表达式（{{ref}} 引用 + 比较/逻辑运算）
        self.breakpoint_conditions: dict[str, str] = dict(breakpoint_conditions or {})
        self.bus = event_bus
        self.model_provider = model_provider
        self.http_client = http_client
        self.node_timeout = node_timeout

        # 控制状态(需求 5):取消/暂停不通过进程内信号,全部由 DB 状态驱动。
        # status_check_hook(execution_id) -> str:service 层注入的 DB 状态查询函数,
        # engine 在每个节点执行前调用一次,返回 RUNNING/CANCELLED/PAUSED。
        self.finished = False
        self.status = STATUS_PENDING

        # 执行状态
        self.node_states: dict[str, NodeState] = {}
        self._order_counter = 0
        self._completed_with_branch: dict[str, Optional[str]] = {}  # node_id -> 活跃出边端口
        self._running_tasks: set[asyncio.Task] = set()
        self._pending_nodes: list[str] = []          # 暂停时未调度的节点
        self.outputs: dict = {}
        self.error: Optional[str] = None
        self.duration_ms: int = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_call_count = 0

        # 持久化钩子(service 层注入:节点明细落库 / 执行状态落库 / DB 状态查询)
        self.node_persist_hook = node_persist_hook
        self.state_persist_hook = state_persist_hook
        self.status_check_hook = status_check_hook

    # ==================== 事件 ====================

    async def emit(self, event_type: str, **payload) -> None:
        await self.bus.emit(event_type, **payload)

    def bump_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        self.input_tokens += int(prompt_tokens or 0)
        self.output_tokens += int(completion_tokens or 0)
        self.llm_call_count += 1

    # ==================== 主流程 ====================

    async def run(self) -> dict:
        """执行整个工作流。返回 outputs(或抛出异常由调用方落 FAILED)。
    
        状态控制语义(需求 5):取消/暂停全部由 DB 状态驱动——
        - API 层把执行记录状态改为 CANCELLED/PAUSED;
        - 引擎每个节点的调度入口都会检查一次 DB 状态(_checkpoint):
          仍 RUNNING 则继续;CANCELLED 抛 WorkflowCancelled 走取消分支收尾;
          PAUSED 落库暂停快照并广播 workflow.paused,run() 正常结束(不再阻塞等信号);
        - 恢复由 resume 接口把状态改回 RUNNING 并重新投递 resume 任务,
          任务里从 DB 快照重建运行时 → run() 发现 _pending_nodes 非空即从断点继续。
        """
        start = time.monotonic()
        self.status = STATUS_RUNNING
        try:
            await self.emit("workflow.started", inputs=self.ctx.inputs)
            if self._pending_nodes:
                # 从快照恢复(resume 任务重建运行时):直接调度 pending 节点,
                # 不可再从 START 重跑(已执行节点会被 _schedule 的「已完成守卫」跳过且无后继)。
                pending = self._pending_nodes
                self._pending_nodes = []
                for nid in pending:
                    await self._schedule(nid)
                await self._wait_running()
            else:
                start_node = self.graph.find_start_node()
                if start_node is None:
                    raise NodeExecutionError("工作流缺少 START 节点")
                await self._schedule(start_node.id)
                await self._wait_running()
    
            # 终态检查点:末节点执行期间到达的取消/暂停,在收尾前再查一次 DB 状态,
            # 避免「全部节点已跑完但 DB 已不是 RUNNING」时仍按成功落库。
            await self._checkpoint()
            if self.status == STATUS_PAUSED:
                # 暂停:run() 正常结束(暂停快照与 workflow.paused 事件已由
                # _checkpoint → _enter_paused 完成),等待 resume 任务恢复。
                return self.outputs
            if self.status == STATUS_RUNNING:
                # 全图执行完毕 → 收集 END 节点输出(无 END 则为空)
                self.status = STATUS_COMPLETED
                self.duration_ms = int((time.monotonic() - start) * 1000)
                self.outputs = self._collect_outputs()
                await self._persist_state()
                await self.emit("workflow.completed", outputs=self.outputs, duration=self.duration_ms)
            return self.outputs
        except WorkflowCancelled:
            self.status = STATUS_CANCELLED
            self.duration_ms = int((time.monotonic() - start) * 1000)
            # 取消仍在执行的节点任务:防任务泄漏与取消后半途写库
            for t in list(self._running_tasks):
                t.cancel()
            await self._persist_state()
            await self.emit("workflow.cancelled")
            return {}
        except Exception as e:  # noqa: BLE001
            self.status = STATUS_FAILED
            self.error = str(e)
            self.duration_ms = int((time.monotonic() - start) * 1000)
            await self._persist_state()
            await self.emit("workflow.failed", error=str(e))
            raise
        finally:
            self.finished = True
            # 无本地缓冲/连接可释放:事件已实时 PUBLISH,无需再 close

    async def _wait_running(self, exclude: Optional[set] = None) -> None:
        me = asyncio.current_task()
        excl = exclude or set()
        while True:
            # 排除当前任务自身及其祖先复合节点任务（复合节点以 task 形式被调度时自身在
            # _running_tasks 中，若一并等待会自死锁）
            pending = [t for t in self._running_tasks
                       if t is not me and t not in excl]
            if not pending:
                return
            done, _ = await asyncio.wait(pending, timeout=0.5,
                                         return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                self._running_tasks.discard(t)
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is not None and not isinstance(exc, (WorkflowCancelled,)):
                    # 分支任务里的异常已在其内部处理为 node.failed；此处兜底上抛
                    raise exc
            # 注:取消/暂停不在此处打断等待——相关任务会在各自的调度入口
            # 检查点(_checkpoint)自行终止,这里只需等所有任务自然结束。

    # ==================== 调度（对照 MaxKB run_chain_manage/get_next_node_list） ====================

    async def _schedule(self, node_id: str) -> None:
        """调度一个节点:执行 → 路由出边 → 并行调度后继。"""
        node = self.graph.get_node(node_id)
        if node is None:
            return
        if node_id in {n for n in self.node_states if self.node_states[n].status == STATUS_COMPLETED}:
            return  # 已执行(LOOP 回边场景)

        # 状态检查点(需求 5):每个节点执行前查一次 DB 状态。
        # CANCELLED → 抛 WorkflowCancelled;PAUSED → 落库暂停快照后,
        # 当前节点挂进 _pending_nodes,等 resume 任务从它继续。
        await self._checkpoint()
        if self.status == STATUS_PAUSED:
            self._pending_nodes.append(node_id)
            return

        # 断点:命中断点节点(且已有节点执行过,避免首个节点即暂停)→ 暂停。
        # 条件断点:命中后先求值表达式,true 才暂停;false 放行且保留断点
        # (LOOP 等回边场景下次命中可重新求值)。暂停时清除该断点,
        # 避免 resume 重新调度到它时重复暂停。
        if node_id in self.breakpoints and self.ctx.executed:
            if self._breakpoint_condition_met(node_id):
                self.breakpoints.discard(node_id)
                await self._enter_paused()
                self._pending_nodes.append(node_id)
                return

        result = await self._execute_node(node)

        # 分支失败且无异常分支 → 终止
        if result is None:
            return

        await self._route_next(node, result)

    async def _execute_node(self, node) -> Optional[NodeResult]:
        """执行单个节点（含事件、计时、状态记录、异常处理）。"""
        state = self.node_states.get(node.id) or NodeState()
        self._order_counter += 1
        state.order = self._order_counter
        state.status = STATUS_RUNNING
        state.started_at = time.monotonic()
        self.node_states[node.id] = state

        input_view = self._node_input_view(node)
        state.input = input_view
        await self.emit("node.started", nodeId=node.id, nodeType=node.type, input=input_view)
        if self.node_persist_hook:
            try:
                await self.node_persist_hook(self, node, state)
            except Exception:  # noqa: BLE001
                pass

        started = time.monotonic()
        try:
            executor_cls = NODE_REGISTRY.get(node.type)
            if executor_cls is None:
                raise NodeExecutionError(f"未知节点类型: {node.type}")
            executor = executor_cls(node, self)
            # 单节点超时保护（MaxKB 缺失）：节点 data.timeout 可覆盖，逾时按节点失败处理，
            # 避免外部/工具/模型节点异常挂起拖垮整条工作流。
            timeout = int(node.data.get("timeout") or self.node_timeout or NODE_TIMEOUT_DEFAULT)
            try:
                result = await asyncio.wait_for(executor.execute(self.ctx), timeout=timeout)
            except asyncio.TimeoutError:
                raise NodeExecutionError(
                    f"节点「{node.label}」执行超时（>{timeout}s）")
        except WorkflowCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            state.status = STATUS_FAILED
            state.error = str(e)
            state.duration = int((time.monotonic() - started) * 1000)
            state.output = None
            await self.emit("node.failed", nodeId=node.id, error=str(e))
            if self.node_persist_hook:
                try:
                    await self.node_persist_hook(self, node, state)
                except Exception:  # noqa: BLE001
                    pass
            # 异常分支：branch:exception 出边存在则继续，否则终止工作流
            if self.graph.get_out_edges(node.id, "exception"):
                self._completed_with_branch[node.id] = "exception"
                self.ctx.set_node_output(node.id, {"exception_message": str(e)})
                return NodeResult(output={"exception_message": str(e)}, branch_id="exception")
            self.error = f"节点「{node.label}」执行失败: {e}"
            self.status = STATUS_FAILED
            raise NodeExecutionError(self.error)

        # 成功
        state.status = STATUS_COMPLETED
        state.duration = int((time.monotonic() - started) * 1000)
        state.output = result.output
        self.ctx.set_node_output(node.id, result.output)
        if result.stream_text is not None:
            state.output = {**result.output, "_streamText": result.stream_text}
        self.ctx.executed.append(node.id)
        self._completed_with_branch[node.id] = result.branch_id  # None → output 端口
        # 返回内容开关(画布 data.emitOutput,默认开):开才把节点输出广播给客户端;
        # 关则跳过 emit(节点数据不推送给客户端),但数据持久化不受影响(下方照常落库)
        if node.data.get(EMIT_OUTPUT_KEY, True) is not False:
            await self.emit("node.completed", nodeId=node.id,
                            output=state.output, duration=state.duration)
        if self.node_persist_hook:
            try:
                await self.node_persist_hook(self, node, state)
            except Exception:  # noqa: BLE001
                pass
        return result

    def _node_input_view(self, node) -> dict:
        """节点输入视图（调试展示用）：该节点入边来源节点的输出摘要。"""
        view: dict = {}
        for e in self.graph.get_in_edges(node.id):
            src = self.graph.get_node(e.source)
            if src and e.source in self.node_states:
                view[src.label or src.id] = self.node_states[e.source].output
        return view

    async def _route_next(self, node, result: NodeResult) -> None:
        """路由出边（对照 MaxKB get_next_node_list）。

        1. result.branch_id 非空 → 匹配 branch:{id} 端口的出边（无匹配视为分支终止）
        2. 否则匹配默认 output 端口的出边
        3. 目标节点多入边（AND 汇聚）→ 所有入边就绪才调度
        4. 多目标并行（按 y 坐标排序，asyncio.gather）
        """
        next_pairs = self.graph.get_next_nodes(node.id, result.branch_id)
        ready = []
        for target, edge in next_pairs:
            if self._barrier_ready(target.id):
                ready.append(target)
        if not ready:
            return
        ready.sort(key=lambda n: n.y)
        if len(ready) == 1:
            task = asyncio.create_task(self._schedule(ready[0].id))
        else:
            if len(ready) > MAX_PARALLEL_BRANCHES:
                raise NodeExecutionError(f"节点「{node.label}」并行分支过多: {len(ready)}")
            task = asyncio.gather(*[self._schedule(t.id) for t in ready],
                                  return_exceptions=True)
            # gather 返回 Future，包装为 task 统一管理
            task = asyncio.ensure_future(task)
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    def _is_reachable(self, node_id: str, visiting: Optional[set] = None) -> bool:
        """节点在「已执行的决策」下是否仍可达（用于汇合门放行不可达分支）。

        可达 = START，或存在一条入边满足：
          - 源已执行且走了该边（活跃），或
          - 源尚未执行但源本身可达（将来会执行）。
        环（LOOP 回边）按保守策略视为可达，避免误阻塞；visiting 防无限递归。
        """
        if visiting is None:
            visiting = set()
        if node_id in visiting:
            return True
        visiting.add(node_id)
        try:
            start = self.graph.find_start_node()
            if start is not None and start.id == node_id:
                return True
            for e in self.graph.get_in_edges(node_id):
                s = e.source
                s_state = self.node_states.get(s)
                if s_state is not None and s_state.status in (
                        STATUS_COMPLETED, STATUS_FAILED):
                    # 与 _barrier_ready 一致：源已终结按活跃端口判断（FAILED+exception 视为活跃）
                    active = self._completed_with_branch.get(s)
                    expected = e.source_handle or OUTPUT_HANDLE
                    actual = f"branch:{active}" if active else OUTPUT_HANDLE
                    edge_active = (expected == actual)
                else:
                    edge_active = self._is_reachable(s, visiting)
                if edge_active:
                    return True
            return False
        finally:
            visiting.discard(node_id)

    def _barrier_ready(self, node_id: str) -> bool:
        """AND 汇聚判断（对照 MaxKB dependent_node_been_executed）。

        目标节点的每条入边需满足：
        - 源已执行完成且活跃端口 == 该边 sourceHandle → 该边就绪
        - 源已完成但未走该边 → 该边不激活，跳过
        - 源尚未执行：仍可达（将来执行）→ 等待；不可达（所在分支未激活）→ 跳过
        """
        for e in self.graph.get_in_edges(node_id):
            src_state = self.node_states.get(e.source)
            if src_state is not None and src_state.status in (
                    STATUS_COMPLETED, STATUS_FAILED):
                # 源已终结（完成或失败）：按 _completed_with_branch 记录的活跃端口判断。
                # FAILED + exception 分支（_execute_node 已记录 branch）时下游异常
                # 分支节点必须放行，否则失败降级路径会被 AND 闸门永久卡死，
                # 执行静默 COMPLETED 丢失后续节点。
                expected = e.source_handle or OUTPUT_HANDLE
                active = self._completed_with_branch.get(e.source)
                actual = f"branch:{active}" if active else OUTPUT_HANDLE
                if expected != actual:
                    continue  # 已终结但走的不是这条边 → 边不激活，跳过
                continue  # 已终结且走的就是这条边 → 就绪
            # 源尚未执行：可达则等待，不可达则跳过（如未激活分支上的前置节点）
            if self._is_reachable(e.source):
                return False
            continue
        return True

    # ==================== 子图执行（LOOP/ITERATION/PARALLEL 用） ====================

    async def run_subgraph(self, entry_node_id: str, exit_node_id: Optional[str] = None,
                           scope_vars: Optional[dict] = None,
                           wait_all: bool = True) -> dict:
        """执行 entry 起始的子图，遇到 exit_node_id（不执行它）或无出边即止。

        返回子图内所有已完成节点的输出聚合 {node_id: output}。
        scope_vars 压入局部作用域（ITERATION 的 item/index）。
        复合节点（LOOP/ITERATION）每轮都会重复调用本方法，因此进入前重置子图节点
        的完成状态，避免 _schedule 的「已完成则跳过」守卫导致循环体只跑一次。
        """
        if scope_vars:
            self.ctx.push_scope(scope_vars)
        executed_before = set(self.ctx.executed)
        self._reset_subgraph_nodes(entry_node_id, exit_node_id)
        # 快照进入前已存在的任务（含调用方——复合节点的调度 task）。
        # 复合节点以 asyncio.create_task 形式被调度，其 task 就在 _running_tasks 中，
        # 而本方法内部被 asyncio.wait_for 包裹，asyncio.current_task() 拿到的是
        # wait_for 的内部任务而非该祖先 task，若仅凭「非自身」过滤会漏掉祖先 task，
        # 导致内层一直等待正阻塞在 run_subgraph 返回上的祖先 → 自死锁。
        # 故用进入时刻的快照排除所有祖先/pre-existing 任务，仅等待本子图「衍生」的任务。
        preexisting = set(self._running_tasks)
        try:
            await self._checkpoint()
            await self._schedule(entry_node_id)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                pending = [t for t in self._running_tasks if t not in preexisting]
                if not pending:
                    break
                await asyncio.wait(pending, timeout=0.2,
                                   return_when=asyncio.FIRST_COMPLETED)
            result = {nid: self.ctx.get_node_output(nid)
                      for nid in self.ctx.executed if nid not in executed_before}
            return result
        finally:
            if scope_vars:
                self.ctx.pop_scope()

    def _reset_subgraph_nodes(self, entry_node_id: str, exit_node_id: Optional[str]) -> None:
        """重置 entry 可达子图（不含 exit）的完成状态，使复合节点每轮能重跑子图。"""
        seen: set[str] = set()
        stack = [entry_node_id]
        while stack:
            nid = stack.pop()
            if nid in seen or nid is None:
                continue
            seen.add(nid)
            self.node_states.pop(nid, None)
            self._completed_with_branch.pop(nid, None)
            if nid == exit_node_id:
                continue  # exit 节点不被执行，不继续向下传播
            for e in self.graph.get_out_edges(nid):
                stack.append(e.target)


    async def run_branches(self, node, branch_ids: list[str],
                           wait_strategy: str = "ALL",
                           timeout_ms: int = 0) -> dict[str, dict]:
        """并行执行节点的多个 branch:{id} 分支子图，返回 {branch_id: 输出聚合}。

        wait_strategy: ALL 全部等待 / ANY 任一完成 / FIRST 第一个完成（语义同 ANY，按 y 序）
        """
        results: dict[str, dict] = {}

        async def _run_branch(bid: str) -> str:
            pairs = self.graph.get_next_nodes(node.id, bid)
            if not pairs:
                results[bid] = {}
                return bid
            entry = pairs[0][0]
            # 分支子图终点 = node 的所有其他入边来源或无出边；简化：执行到自然结束
            # 等待方式与 run_subgraph 相同：快照进入前已存在的任务，只等本分支衍生的任务。
            # 不可用 exclude={当前task}：_execute_node 会把 executor 包成 wait_for 内层
            # task，current_task 拿到的是内层 task，而 _running_tasks 里是外层 _schedule
            # task，排除不完全 → 分支等外层、外层等分支 → 自死锁。
            preexisting = set(self._running_tasks)
            executed_before = set(self.ctx.executed)
            await self._schedule(entry.id)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                pending = [t for t in self._running_tasks if t not in preexisting]
                if not pending:
                    break
                await asyncio.wait(pending, timeout=0.2,
                                   return_when=asyncio.FIRST_COMPLETED)
            results[bid] = {nid: self.ctx.get_node_output(nid)
                            for nid in self.ctx.executed if nid not in executed_before}
            return bid

        tasks = [asyncio.create_task(_run_branch(b)) for b in branch_ids]
        try:
            if wait_strategy in ("ANY", "FIRST"):
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED,
                    timeout=timeout_ms / 1000 if timeout_ms else None)
                for p in pending:
                    p.cancel()
            elif timeout_ms:
                done, pending = await asyncio.wait(tasks, timeout=timeout_ms / 1000)
                for p in pending:
                    p.cancel()
            else:
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                # 分支内节点失败不可静默吞掉（否则并行节点会「假成功」）
                for g in gathered:
                    if isinstance(g, BaseException) and not isinstance(g, asyncio.CancelledError):
                        raise g
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        return results

    # ==================== 状态检查(需求 5:DB 状态驱动控制) ====================

    async def _checkpoint(self) -> None:
        """节点状态检查点:每个节点执行前调用一次(经注入的 hook 查 DB 状态)。

        - DB 状态为 RUNNING(或 hook 缺失/查询失败)→ 放行,继续执行;
        - DB 状态为 CANCELLED → 抛 WorkflowCancelled,run() 走取消分支统一收尾;
        - DB 状态为 PAUSED → 进入暂停态(落库快照 + 广播 workflow.paused)。
        """
        if self.status == STATUS_PAUSED:
            # 已处于暂停态(断点暂停或本检查点刚进入):后续调度不再放行
            return
        status = STATUS_RUNNING
        if self.status_check_hook is not None:
            try:
                status = (await self.status_check_hook(self.execution_id)) or STATUS_RUNNING
            except Exception as e:  # noqa: BLE001
                # DB 瞬时不可用按 RUNNING 放行(宁可慢执行也不误杀任务)
                log.warning("status check hook failed exec={}: {}", self.execution_id, e)
                status = STATUS_RUNNING
        if status == STATUS_CANCELLED:
            raise WorkflowCancelled()
        if status == STATUS_PAUSED:
            await self._enter_paused()

    async def _enter_paused(self) -> None:
        """进入暂停态:改内存状态 → 落库暂停快照(含 variables)→ 广播事件。

        与旧信号方案不同:这里不阻塞 run(),run() 会正常结束;
        恢复由 resume 接口改 DB 状态为 RUNNING 并重新投递任务驱动。
        """
        self.status = STATUS_PAUSED
        current = self.ctx.executed[-1] if self.ctx.executed else None
        await self._persist_state(paused=True)
        await self.emit("workflow.paused", nodeId=current,
                        variables=self.ctx.global_vars)

    def _breakpoint_condition_met(self, node_id: str) -> bool:
        """条件断点求值：无条件 → 恒真；求值失败（语法错/变量不存在）
        视为不满足并告警放行，不阻塞执行（调试器惯例：坏条件不 break）。"""
        cond = self.breakpoint_conditions.get(node_id)
        if not cond or not cond.strip():
            return True
        try:
            return evaluate_expression(cond, self.ctx)
        except ValueError as e:
            log.warning("breakpoint condition eval failed, node={}: {}", node_id, e)
            return False

    async def _persist_state(self, paused: bool = False) -> None:
        """执行状态落库钩子(service 层注入,引擎不直接依赖 ORM)。"""
        if self.state_persist_hook:
            try:
                await self.state_persist_hook(self, paused)
            except Exception:  # noqa: BLE001
                pass

    def _collect_outputs(self) -> dict:
        """收集 END 节点输出（多 END 合并；无 END 则空）。"""
        outputs = {}
        for end_node in self.graph.find_end_nodes():
            outputs.update(self.ctx.get_node_output(end_node.id))
        return outputs

    def node_states_dict(self) -> dict:
        return {nid: s.to_dict() for nid, s in self.node_states.items()}

    def snapshot(self) -> dict:
        """执行快照（variables API / resume-from-snapshot 用）。"""
        return {
            "executionId": self.execution_id,
            "status": self.status,
            "context": self.ctx.to_dict(),
            "nodeStates": self.node_states_dict(),
            "completedWithBranch": dict(self._completed_with_branch),
            "pendingNodes": list(self._pending_nodes),
            # 剩余断点及其条件（resume-from-snapshot 重建运行时后仍可命中）
            "breakpoints": list(self.breakpoints),
            "breakpointConditions": dict(self.breakpoint_conditions),
            "global": self.ctx.global_vars,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "llmCallCount": self.llm_call_count,
        }

    def restore(self, snapshot: dict) -> None:
        self.ctx.restore(snapshot.get("context") or {})
        # global 变量在快照中存在两份冗余（顶层 "global" 与 context.global），
        # 顶层为权威（与 _persist_state 落库语义一致），context 内为兼容兜底。
        merged = dict((snapshot.get("context") or {}).get("global") or {})
        merged.update(snapshot.get("global") or {})
        self.ctx.global_vars = merged
        self._completed_with_branch = dict(snapshot.get("completedWithBranch") or {})
        self._pending_nodes = list(snapshot.get("pendingNodes") or [])
        # 剩余断点与条件（快照带则恢复，不带则保持构造时传入值——向后兼容旧快照）
        if snapshot.get("breakpoints") is not None:
            self.breakpoints = set(snapshot["breakpoints"])
            self.breakpoint_conditions = dict(snapshot.get("breakpointConditions") or {})
        for nid, s in (snapshot.get("nodeStates") or {}).items():
            state = NodeState()
            state.order = s.get("order", 0)
            state.status = s.get("status", STATUS_COMPLETED)
            state.input = s.get("input")
            state.output = s.get("output")
            state.error = s.get("error")
            state.duration = s.get("duration", 0)
            self.node_states[nid] = state
        self._order_counter = max((s.order for s in self.node_states.values()), default=0)
        self.input_tokens = int(snapshot.get("inputTokens", 0))
        self.output_tokens = int(snapshot.get("outputTokens", 0))
        self.llm_call_count = int(snapshot.get("llmCallCount", 0))
