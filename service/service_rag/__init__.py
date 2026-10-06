# -*- coding: utf-8 -*-
"""service_rag：知识库（RAG）服务装配。

四家存储各存什么（口径来自 SPEC §10/§12，表结构见 sql/v2_init.sql PART 2）：
    MySQL          资产与配置：知识库、文档原件元数据与状态机、切片完整正文
    公共存储(OSS)  所有原件、文档内嵌图片、音视频媒体文件（对象名带 kb_id 前缀做隔离）
    Elasticsearch  向量 + 检索用元数据 + 媒体 URL（单索引 rag_knowledge_chunk，靠 kb_id 隔离）
    Neo4j          实体与关系（只服务 kb_type=doc 且库级开关打开的知识库）
这三个后端都在 create_app 的 lifespan 里初始化成**全局异步单例**（SPEC §5.1），
本模块不再自己建连接池；鉴权一律在上层 service 完成，后端只按 kb_id 白名单裸查。
"""
from common.common_app.bootstrap import create_app
from common.common_constants.constant import SERVICE_RAG, SERVICE_RAG_PORT
from service.service_rag.routers import ROUTERS
from service.service_rag.services import rag_settings as settings

# enable_storage   原件/图片/媒体全落公共存储，storage 段缺省即本地后端
# enable_arq_rag_redis  解析任务投 arq_ragflow 队列：生产者与 rag worker 必须读同一段
#                       redis.url，否则任务投了没人消费
# enable_es / enable_neo4j  检索与图谱后端；连接失败即阻断启动（不做「起来了一个库都查不了」的服务）
# on_ready         全部依赖就绪后装配 rag 运行参数（Nacos 的 rag/mineru 段 → 全局单例）：
#                  放在每个请求里懒加载会让首个请求读到默认值，放在 import 期又拿不到配置
app = create_app(
    service_name=SERVICE_RAG,
    default_port=SERVICE_RAG_PORT,
    routers=ROUTERS,
    enable_storage=True,
    enable_arq_ragflow_redis=True,
    enable_es=True,
    enable_neo4j=True,
    on_ready=settings.bootstrap,
)
