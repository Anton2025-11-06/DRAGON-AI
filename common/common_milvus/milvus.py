from typing import Optional

from pymilvus import DataType, AsyncMilvusClient

from common.common_log.log_init import log


class AsyncMilvus:
    """
    Milvus 向量库封装：
    - 知识库隔离：整型 kb_id → collection 名 kb_{kb_id}
    - 通用集合：字符串集合名直接使用（如记忆集合 agent_memory）
    - 线程池执行同步 pymilvus 调用，统一 schema: chunk_id(主键)/doc_id/kb_id/embedding
    """

    _instance: Optional["AsyncMilvus"] = None
    _client: Optional[AsyncMilvusClient] = None
    _dim: int = 0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init(self, host: str, port: str, user: str, password: str, db: str, dim: int = 1024):
        self._client = AsyncMilvusClient(uri=f"http://{host}:{port}", user=user, password=password, db_name=db)
        self._dim = dim
        log.info(f"Async Milvus connect success: {host}:{port} dim={dim}")

    async def insert(self):
        self._client.insert()
        pass


milvus_client = AsyncMilvus()
