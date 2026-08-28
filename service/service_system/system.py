import os
import sys
import uvicorn

# Windows 下改用 SelectorEventLoop（默认 ProactorEventLoop 在客户端连接半途断开时
# 会抛 WinError 64 导致 accept 链断裂、服务假死：进程在但端口停止监听）
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from common.common_constants.constant import SERVICE_SYSTEM_PORT, SERVICE_SYSTEM
from service.service_system import app


if __name__ == '__main__':
    uvicorn.run("service.service_system.system:app",
                port=int(os.environ.get(SERVICE_SYSTEM + "_port", SERVICE_SYSTEM_PORT)),
                host="0.0.0.0",
                workers=1,
                log_level="INFO",
                access_log=False)
