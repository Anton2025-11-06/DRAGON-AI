from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_GATEWAY, SERVICE_GATEWAY_PORT
from service.service_gateway.routers.gateway_router import router as gateway_router
from service.service_gateway.routers.rate_limit_router import router as rate_limit_router

# AI 模型网关：动态服务转发 + /api/model OpenAI 兼容入口（api-key 鉴权）+ 限流策略加载
# - 网关不存业务数据：/api/model/sessions* 会话管理仅做 api-key → 内部用户令牌的
#   鉴权翻译，再转发 service_workflow（/internal/model-chat/...）
# - model_proxy_router：外部模型调用统一入口（鉴权/路由/限流/流式转发）
app = create_app(
    service_name=SERVICE_GATEWAY,
    default_port=SERVICE_GATEWAY_PORT,
    routers=[gateway_router, rate_limit_router],
    enable_token_check=True,
    enable_rate_limit=True,
    enable_operate_log=True
)