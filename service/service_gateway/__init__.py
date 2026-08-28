from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_GATEWAY, SERVICE_GATEWAY_PORT
from service.service_gateway.routers.gateway_router import router as gateway_router
from service.service_gateway.routers.rate_limit_router import router as rate_limit_router

app = create_app(
    service_name=SERVICE_GATEWAY,
    default_port=SERVICE_GATEWAY_PORT,
    routers=[gateway_router, rate_limit_router],
    enable_token_check=True,
    enable_rate_limit=True,
    enable_operate_log=True
)
