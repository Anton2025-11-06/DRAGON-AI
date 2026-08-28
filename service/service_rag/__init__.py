from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_RAG, SERVICE_RAG_PORT
from common.common_milvus.milvus import milvus_client
from common.common_rag.embed_client import EmbeddingClient
from service.service_rag.routers.kb_router import router as kb_router


async def _init_milvus(app):
    """启动后创建 Milvus 连接（配置来自 Nacos service_rag 配置 yml 的 milvus 段落/环境变量兜底）"""
    cfg = getattr(app.state, "config", {}).get("milvus", {})
    milvus_client.init(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", "19530"),
        dim=int(cfg.get("dim", EmbeddingClient.dim)))

app = create_app(
    service_name=SERVICE_RAG,
    default_port=SERVICE_RAG_PORT,
    routers=[kb_router],
    enable_token_check=True,

)