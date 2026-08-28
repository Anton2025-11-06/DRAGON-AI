from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_WORKFLOW, SERVICE_WORKFLOW_PORT
from service.service_workflow.routers.mcp_router import router as mcp_router
from service.service_workflow.routers.model_router import router as model_router
from service.service_workflow.routers.sandbox_router import router as sandbox_router
from service.service_workflow.routers.tool_router import router as tool_router

# service_workflow：智能体编排域（MCP 连接管理 / 动态 Python 函数工具 / 模型广场 / 沙箱）
# - enable_token_check：开启 JWT/Token 鉴权中间件
# - enable_operate_log：写操作落库 tb_operate_log 审计（需 MySQL）
app = create_app(
    service_name=SERVICE_WORKFLOW,
    default_port=SERVICE_WORKFLOW_PORT,
    routers=[mcp_router, tool_router, model_router, sandbox_router],
)