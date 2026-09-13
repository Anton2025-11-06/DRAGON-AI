from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_FILE, SERVICE_FILE_PORT
from service.service_file.routers.file_router import router as file_router

# service_file：文件微服务（上传/下载，暂不做鉴权，供大模型按可访问 URL 拉取）
# - enable_mysql/redis=False：仅依赖统一存储后端（bootstrap 默认初始化 local/s3）
# - enable_token_check=False：上传下载无需登录态
app = create_app(
    service_name=SERVICE_FILE,
    default_port=SERVICE_FILE_PORT,
    routers=[file_router],
    enable_mysql=False,
    enable_redis=False,
)
