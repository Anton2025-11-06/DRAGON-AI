import os
import sys
import uvicorn
import asyncio

from common.common_constants.constant import SERVICE_SYSTEM_PORT, SERVICE_SYSTEM
from service.service_system import app

if __name__ == '__main__':
    if sys.platform.__contains__("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run("service.service_system.system:app",
                port=int(os.environ.get(SERVICE_SYSTEM + "_port", SERVICE_SYSTEM_PORT)),
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
