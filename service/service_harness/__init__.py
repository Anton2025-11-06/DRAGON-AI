from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_HARNESS, SERVICE_HARNESS_PORT
from service.service_harness.routers.harness_router import (
    chat_router, models_router, options_router, router as session_router,
    sandbox_router,
)
from service.service_harness.services import harness_settings

# service_harness：深度探索智能体（AgentScope ReAct + OpenSandbox 云沙箱）
# - 会话/消息/轮次管理（tb_harness_session / tb_harness_message，session_service）
# - 对话轮次投 arq harness 流水线（PIPELINE_HARNESS），事件经 Redis Pub/Sub
#   （harness:evt:{turn_id}）推给 SSE 订阅者（harness_pubsub）
# - 对话轮次在独立 arq worker 进程组装执行；本进程只服务会话/文件浏览 HTTP 口，
#   但沙箱文件浏览需 attach 沙箱，故与 worker 各建一份进程级 manager 单例
#   （enable_workspace_manager），本进程因此会 import agentscope 的 workspace 相关
# - on_ready 装 opensandbox/agentscope 两段配置（worker 读同一段，两侧口径一致）
app = create_app(
    service_name=SERVICE_HARNESS,
    default_port=SERVICE_HARNESS_PORT,
    routers=[session_router, chat_router, models_router, options_router,
             sandbox_router],
    enable_arq_harness_redis=True,
    enable_workspace_manager=True,
    on_ready=harness_settings.bootstrap,
)
