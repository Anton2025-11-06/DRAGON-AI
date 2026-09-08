import asyncio
import os
import sys
import uvicorn

from common.common_constants.constant import SERVICE_LOGIN, SERVICE_LOGIN_PORT
from service.service_login import app

if __name__ == '__main__':
    if sys.platform.__contains__("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run("service.service_login.login:app",
                port=int(os.environ.get(SERVICE_LOGIN + "_port", SERVICE_LOGIN_PORT)),
                host="0.0.0.0",
                workers=1,
                log_level="INFO",
                access_log=False,)
