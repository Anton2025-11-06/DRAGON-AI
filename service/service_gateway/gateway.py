import asyncio
import os
import sys
import uvicorn

from common.common_constants.constant import SERVICE_GATEWAY_PORT, SERVICE_GATEWAY
from service.service_gateway import app

if __name__ == '__main__':
    if sys.platform.__contains__("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 网关入口：统一经 create_app 引导（Nacos 注册/发现 + Redis 限流 + JWT 鉴权 + 动态转发）
    uvicorn.run("service.service_gateway.gateway:app",
                port=int(os.environ.get(SERVICE_GATEWAY + "_port", SERVICE_GATEWAY_PORT)),
                host="0.0.0.0",
                workers=1,
                log_level="INFO",
                access_log=False,
                proxy_headers=True,
                forwarded_allow_ips="*",
                timeout_keep_alive=30,
                backlog=4096,
                http="httptools",  # 吞吐提升(SSE 兼容,可选)
                limit_max_requests=50000,
                )
