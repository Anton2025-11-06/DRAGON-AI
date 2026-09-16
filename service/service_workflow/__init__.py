from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_WORKFLOW, SERVICE_WORKFLOW_PORT
from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from fastapi import Depends

from service.service_workflow.routers.mcp_router import router as mcp_router
from service.service_workflow.routers.sandbox_router import router as sandbox_router
from service.service_workflow.routers.skill_router import router as skill_router
from service.service_workflow.routers.tool_router import router as tool_router
from service.service_workflow.routers.workflow_router import (
    router as workflow_router,
    support_router,
)
from service.service_workflow.routers.workflow_execution_router import (
    api_key_router,
    router as execution_router,
)
from service.service_workflow.routers.workflow_file_router import file_router
from service.service_workflow.routers.workflow_template_router import template_router
from service.service_workflow.services.workflow_service import ensure_initialized

# service_workflow：智能体编排域
# 路由分两组挂载：
# 1) MCP / Tool / 沙箱 / 技能 —— 既有路由，由 create_app 统一挂载
# 2) 工作流编排（workflow_* / template / execution / apikey / file / support）
#    —— 手动挂载并附加 ensure_initialized 依赖（幂等：内置模板种子 + 默认模型 Provider 注入）
app = create_app(
    service_name=SERVICE_WORKFLOW,
    default_port=SERVICE_WORKFLOW_PORT,
    routers=[mcp_router, tool_router, sandbox_router, skill_router],
    # workflow-files 上传经统一存储后端（local / oss），storage 段缺省即 local
    enable_storage=True,
    enbale_arq_workflow_redis=True
)

# 工作流相关路由：统一追加「懒加载初始化」依赖（DB 就绪前调用自愈，不阻断其他路由）
_wf_deps = [Depends(ensure_initialized)]
for _r in (workflow_router, template_router, support_router,
           execution_router, api_key_router, file_router):
    app.include_router(_r, dependencies=_wf_deps)
