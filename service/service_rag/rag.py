import os

import uvicorn

from common.common_constants.constant import SERVICE_RAG, SERVICE_RAG_PORT
from service.service_rag import app

if __name__ == '__main__':
    uvicorn.run("service.service_rag.rag:app",
                port=int(os.environ.get(SERVICE_RAG + "_port", SERVICE_RAG_PORT)),
                host="0.0.0.0",
                workers=1,
                log_level="INFO",
                access_log=False)