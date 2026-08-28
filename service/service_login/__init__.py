from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_LOGIN, SERVICE_LOGIN_PORT
from service.service_login.routers.login_router import router as login_router

app = create_app(
    service_name=SERVICE_LOGIN,
    default_port=SERVICE_LOGIN_PORT,
    routers=[login_router],
)
