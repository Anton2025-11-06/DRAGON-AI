import asyncio
import os
import platform

import uvicorn

from common.common_constants.constant import SERVICE_HARNESS, SERVICE_HARNESS_PORT
from service.service_harness import app



if __name__ == '__main__':
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run("service.service_harness.harness:app",
                port=int(os.environ.get(SERVICE_HARNESS + "_port", SERVICE_HARNESS_PORT)),
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