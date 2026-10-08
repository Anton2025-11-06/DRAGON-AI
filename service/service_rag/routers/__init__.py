# -*- coding: utf-8 -*-
"""知识库路由聚合：按资源边界拆成六个模块，装配处一次挂全（见 service/service_rag/__init__.py）。

拆分口径（每个模块只讲一类主语）：
- kb_router         知识库配置 CRUD、运行参数回显，以及「往这个库里写东西」的入口（上传/删除/构建向量/构建图谱）
- doc_router        单份文档与它的切片（详情/进度/重试/预览原件/编辑/删切片）
- retrieval_router  多模态检索与检索侧就绪探测
- graph_router      图谱读取（子图/统计/实体检索/溯源）
- open_router       开放 API（脚本用的批量上传与检索）
- eval_router       知识评测（RAGAS：发起/历史/详情/删除/模板/导入）

各模块内部自带前缀（``/knowledge-bases``、``/documents``…），**都不带 /api/rag**：
网关按 ``/api/{service}/{path}`` 剥掉服务名再转发（见 service_gateway/routers/gateway_router.py）。
"""
from service.service_rag.routers.doc_router import router as doc_router
from service.service_rag.routers.eval_router import router as eval_router
from service.service_rag.routers.graph_router import router as graph_router
from service.service_rag.routers.kb_router import router as kb_router
from service.service_rag.routers.open_router import router as open_router
from service.service_rag.routers.retrieval_router import router as retrieval_router

# 挂载顺序只影响 OpenAPI 文档分组，路由匹配靠前缀不同互不干扰
ROUTERS = [kb_router, doc_router, retrieval_router, graph_router, open_router, eval_router]

__all__ = ["ROUTERS", "kb_router", "doc_router", "retrieval_router", "graph_router",
           "open_router", "eval_router"]
