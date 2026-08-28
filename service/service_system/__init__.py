from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_SYSTEM, SERVICE_SYSTEM_PORT
from service.service_system.routers.user_router import router as user_router
from service.service_system.routers.rbac_router import router as rbac_router
from service.service_system.routers.log_router import router as log_router
from service.service_system.routers.online_router import router as online_router
from service.service_system.routers.rate_limit_router import router as rate_limit_router

# service_system：企业级 RBAC 权限中心（用户/角色/菜单/部门/数据权限/操作日志）
# - enable_token_check：开启 JWT/Token 鉴权中间件
# - enable_operate_log：开启操作日志中间件（写操作落库 tb_operate_log 审计）
app = create_app(
    service_name=SERVICE_SYSTEM,
    default_port=SERVICE_SYSTEM_PORT,
    routers=[user_router, rbac_router, log_router, online_router, rate_limit_router],
)