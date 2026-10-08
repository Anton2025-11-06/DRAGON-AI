# -*- coding: utf-8 -*-
"""知识库（RAG）API Schema：请求入参与响应契约的唯一定义点。

命名口径（与前端 types.ts 对齐，SPEC §11）
------------------------------------------
- 代码内一律 snake_case：service 层直接 ``req.kb_type`` / ``row.doc_name`` 取值；
- HTTP 边界一律 camelCase：请求体靠 ``alias_generator`` 收 ``kbType``（同时
  ``populate_by_name`` 允许 ``kb_type`` 进来，便于脚本与内部服务互调）；
  响应统一走 ``dump()``，它把**字段名**转 camelCase。

``dump()`` 为什么不用 ``model_dump(by_alias=True)``
--------------------------------------------------
配置三件套（parse_config / chunk_config / retrieve_config）是**原样透传的 JSON**，
它们的键名与 rag_constant 里的默认值字典一一对齐（chunk_size、score_threshold…），
按 by_alias 递归会把这层契约改名，前端表单与后端常量就对不上了。
所以这里只转换模型字段名，普通 dict 的键保持不动——嵌套的 CamelModel 仍会递归转换。

字段可空性
----------
响应模型的可选字段一律给默认值，不强制必填：service 层从 ORM 行拼字典时
少传一个键应该只让页面少显示一项，而不是抛 ValidationError 把整个列表接口打挂。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# ==================== 基类 ====================


def _to_camel(name: str) -> str:
    """snake_case → camelCase（首段小写，其余段首字母大写）。

    只按 ``_`` 拆分：``top_k`` → ``topK``、``created_by`` → ``createdBy``；
    已经是驼峰的键原样返回，避免出现 ``ChunkSize`` 这种首字母大写的怪名字。
    """
    parts = [p for p in str(name).split("_") if p]
    if not parts:
        return str(name)
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _camelize_value(value: Any) -> Any:
    """递归转换嵌套模型；普通 dict 的键名保持原样（配置 JSON 的契约见模块头注释）。"""
    if isinstance(value, CamelModel):
        return value.dump()
    if isinstance(value, BaseModel):                      # 非 CamelModel 的嵌套模型兜底
        return {_to_camel(k): _camelize_value(v) for k, v in value.model_dump().items()}
    if isinstance(value, dict):
        return {k: _camelize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_camelize_value(v) for v in value]
    return value


class CamelModel(BaseModel):
    """出入参公共基类：入参收 camelCase，出参由 ``dump()`` 吐 camelCase。"""

    model_config = ConfigDict(
        alias_generator=_to_camel,        # 校验时接受前端字段名
        populate_by_name=True,            # 代码内仍可用 snake_case 关键字构造
        arbitrary_types_allowed=True,
        extra="ignore",                   # 前端多带的字段（表格选中态等）直接丢弃，不入库也不报错
    )

    def dump(self) -> dict[str, Any]:
        """→ 前端契约字典（字段名 camelCase，配置 JSON 的键原样保留）。"""
        return {_to_camel(key): _camelize_value(value) for key, value in self}


# ==================== 通用分页 ====================


class PageReq(CamelModel):
    """分页入参基类（与工作流模块同名同类，口径一致：current 从 1 开始）。"""

    current: int = Field(1, ge=1, description="当前页码")
    size: int = Field(10, ge=1, le=200, description="每页大小")


class IdsReq(CamelModel):
    """批量动作的通用入参（删除/重试都只给一组 id）。"""

    ids: list[int] = Field(default_factory=list, description="资源 id 列表")


# ==================== 知识库 ====================


class KbPageReq(PageReq):
    """知识库分页查询（POST /api/rag/knowledge-bases/page）。"""

    name: Optional[str] = Field(None, max_length=128, description="名称模糊匹配")
    description: Optional[str] = Field(None, max_length=500, description="描述模糊匹配")
    kb_type: Optional[str] = Field(None, description="doc/image/audio_video，空或 all=不做类型筛选")


class KbSaveReq(CamelModel):
    """新建/修改知识库的入参（同一个模型：修改时 kb_type 与向量模型必须与库里一致）。

    chunk_size / chunk_overlap / top_k / score_threshold 是**扁平快捷字段**：
    页面配置面板上它们是分块、检索分组里的项，后端把它们并进对应的 JSON 配置，
    扁平键与分组键同时给时以扁平键为准（用户刚改过的那一项）。
    """

    kb_type: str = Field("doc", description="知识库类型，创建后锁定")
    name: str = Field(..., min_length=1, max_length=128, description="知识库名称")
    description: Optional[str] = Field(None, max_length=500)

    embed_model_id: int = Field(..., ge=1, description="向量模型 id（创建后锁定）")
    rerank_model_id: Optional[int] = Field(0, ge=0, description="重排模型 id，0=不启用（配置面板已撤，仅接口透传）")
    chat_model_id: Optional[int] = Field(0, ge=0, description="音视频型的媒体理解模型 id（doc 型不再用这一列）")
    extract_model_id: Optional[int] = Field(
        0, ge=0, description="图谱实体抽取模型 id：doc 型且开图谱时必填，0=沿用 chat_model_id")
    image_model_id: Optional[int] = Field(
        0, ge=0, description="图片理解模型 id：doc 型且解析开关 image_understand 打开时必填")

    parser_engine: Optional[str] = Field(None, description="native/docling/mineru/auto")
    parse_config: Optional[dict] = Field(None, description="预处理开关 + 引擎专有参数")
    chunk_config: Optional[dict] = Field(None, description="分块策略与参数")
    retrieve_config: Optional[dict] = Field(None, description="检索参数")

    chunk_size: Optional[int] = Field(None, ge=50, le=20000, description="单块目标长度")
    chunk_overlap: Optional[int] = Field(None, ge=0, le=20000, description="重叠长度")
    top_k: Optional[int] = Field(None, ge=1, le=100, description="召回条数")
    score_threshold: Optional[float] = Field(None, ge=0, le=1, description="相似度阈值")

    enable_graph: Optional[bool] = Field(None, description="知识图谱开关（仅 doc 型有意义，配置面板归到解析配置页）")
    metadata: Optional[dict] = Field(None, description="扩展元数据，前端透传")


class KnowledgeBaseResp(CamelModel):
    """知识库行（列表与详情共用；配置三份 JSON 均为归一后的完整形态）。

    tenant_id / collection_name 是历史契约字段：ES 单索引 + org_id 组织隔离的口径下
    它们恒定为 rag_constant.RAG_CHUNK_INDEX 与库的 org_id，保留是为了不打挂既有前端。
    """

    id: int
    name: str
    description: Optional[str] = None
    kb_type: str
    kb_type_label: Optional[str] = None
    tenant_id: int = 0
    collection_name: Optional[str] = None
    embed_model_id: int = 0
    embed_model_name: Optional[str] = None
    embedding_dim: int = 0
    rerank_model_id: int = 0
    chat_model_id: int = 0
    extract_model_id: int = 0
    image_model_id: int = 0
    top_k: int = 5
    score_threshold: float = 0.0
    chunk_size: int = 0
    chunk_overlap: int = 0
    chunk_strategy: Optional[str] = None
    enable_graph: bool = False
    parser_engine: Optional[str] = None
    parse_config: dict = Field(default_factory=dict)
    chunk_config: dict = Field(default_factory=dict)
    retrieve_config: dict = Field(default_factory=dict)
    version: int = 1
    doc_count: int = 0
    chunk_count: int = 0
    metadata: Optional[dict] = None
    created_by: int = 0
    # 归属部门 id：列表页竖向栏要在行上显示「创建人 + 归属部门」，
    # attach_creator / attach_dept 按这两个键回填 creatorName / deptName（dump 后是驼峰）
    owner_dept_id: int = 0
    create_time: Optional[str] = None
    update_time: Optional[str] = None


# ==================== 文档 ====================


class DocPageReq(PageReq):
    """文档列表分页（POST /api/rag/knowledge-bases/{kb_id}/documents/page）。"""

    name: Optional[str] = Field(None, max_length=255, description="文档标题模糊匹配")
    status: Optional[str] = Field(None, description="解析状态，见 rag_constant.DOC_STATUS_ALL")
    graph_state: Optional[int] = Field(None, ge=0, le=3, description="图谱构建态 0/1/2/3")


class DocTitleReq(CamelModel):
    """改文档标题（列表页可改；只动展示名，不动原件与切片）。"""

    title: str = Field(..., min_length=1, max_length=255, description="文档标题")


class DocActionReq(CamelModel):
    """文档级异步动作（重试/构建向量/构建图谱）的公共入参。

    doc_ids 给多个即为批量；reparse=True 时构建向量会连解析一起重跑
    （默认只吃已有解析产物，改分块配置最常见的操作不必重解析几百页 PDF）。
    """

    doc_ids: list[int] = Field(default_factory=list, description="文档 id 列表（页面必须勾选）")
    reparse: bool = Field(False, description="构建向量时是否连解析一起重跑")


class BuildVectorReq(DocActionReq):
    """构建向量（页面按钮，原名「重新分块」）：先清掉这些文档的旧向量再重新向量化。

    分块配置缺省沿用知识库当前配置；doc_ids 必填——这个动作会重写整篇文档的索引，
    不给「不传=整库」的兜底，误点一次的代价是整库重跑。
    """

    chunk_config: Optional[dict] = Field(None, description="临时覆盖的分块配置")
    chunk_size: Optional[int] = Field(None, ge=50, le=20000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=20000)


class DocumentResp(CamelModel):
    """文档行（三类知识库共用；预览形态由 media_type 决定，SPEC §11.1-4）。"""

    id: int
    kb_id: int
    title: Optional[str] = None
    file_name: Optional[str] = None
    file_ext: Optional[str] = None
    file_size: int = 0
    content_type: Optional[str] = None
    media_type: Optional[str] = None
    parser_engine: Optional[str] = None
    parse_version: int = 0
    status: Optional[str] = None
    status_label: Optional[str] = None
    vectorized: bool = False
    graph_state: int = 0
    graph_state_label: Optional[str] = None
    chunk_count: int = 0
    page_count: int = 0
    media_duration: int = 0
    media_summary: Optional[str] = None
    media_url: Optional[str] = None
    task_id: Optional[str] = None
    error_msg: Optional[str] = None
    created_by: int = 0
    create_time: Optional[str] = None
    update_time: Optional[str] = None


class DocProgressResp(CamelModel):
    """一条文档的两条分段进度（Redis 两个进度键原样透出，页面轮询一口取全）。

    向量化与图谱各自一个 Redis key（rag_constant.progress_key 的 scope 维度），
    互不覆盖：文档正在向量化时也能同时看到图谱跑到第几段。
    stages / graphStages 是**按阶段数量切分的分段数组**，元素 {code,label,done}，
    前端据此把进度条画成 N 格并按 done 上色（已完成绿、未完成蓝），段数即格数。
    """

    doc_id: int
    # ---- 向量化进度 ----
    stage: str = Field("", description="当前阶段码，取值见 rag_constant.RAG_STAGES")
    stage_label: Optional[str] = None
    percent: int = Field(0, ge=0, le=100)
    total: int = 0
    done: int = 0
    message: Optional[str] = None
    stages: list[dict] = Field(default_factory=list, description="向量化分段进度数组")
    update_time: Optional[str] = None
    status: Optional[str] = None
    status_label: Optional[str] = None
    # ---- 图谱进度 ----
    graph_stage: str = Field("", description="图谱当前阶段码，取值见 rag_constant.RAG_GRAPH_STAGES")
    graph_stage_label: Optional[str] = None
    graph_percent: int = Field(0, ge=0, le=100)
    graph_total: int = 0
    graph_done: int = 0
    graph_message: Optional[str] = None
    graph_stages: list[dict] = Field(default_factory=list, description="图谱分段进度数组")
    graph_state: int = 0
    graph_state_label: Optional[str] = None


class UploadItemResp(CamelModel):
    """单个文件的上传结果（批量上传逐条回执，不整批回滚：成 10 个失败 2 个是常态）。"""

    file_name: str
    doc_id: int = 0
    task_id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None


class UploadResp(CamelModel):
    """批量上传回执（页面上传弹窗与开放 API 共用）。"""

    kb_id: int
    accepted: int = 0
    rejected: int = 0
    items: list[UploadItemResp] = Field(default_factory=list)


# ==================== 切片 ====================


class ChunkPageReq(PageReq):
    """切片列表分页（POST /api/rag/documents/{doc_id}/chunks/page）。"""

    kb_id: Optional[int] = Field(None, description="冗余过滤，跨文档核对权限时用")
    chunk_type: Optional[str] = Field(None, description="text/image/audio_video")
    keyword: Optional[str] = Field(None, max_length=200, description="正文包含匹配")
    available: Optional[int] = Field(None, ge=0, le=1, description="0-人工停用 1-可用")


class ChunkUpdateReq(CamelModel):
    """编辑切片：正文与可用性开关。

    改正文只影响展示与 BM25，向量仍是旧文本的向量——所以后端会顺带把该切片标记为
    「未向量化」并投一次单片向量化，避免页面显示的内容与检索命中的语义长期不一致。
    """

    content: Optional[str] = Field(None, min_length=1, description="切片正文")
    available: Optional[int] = Field(None, ge=0, le=1, description="0-停用（检索排除）1-可用")


class ChunkResp(CamelModel):
    """切片行（tb_document_chunk 是完整正文的唯一存放处）。"""

    id: int
    kb_id: int
    doc_id: int
    chunk_index: int = 0
    chunk_type: str = "text"
    chunk_type_label: Optional[str] = None
    content: str = ""
    embed_text: Optional[str] = None
    title_path: Optional[str] = None
    sheet_name: Optional[str] = None
    page_num: int = 0
    block_id: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    token_count: int = 0
    available: bool = True
    vectorized: bool = False
    extra: Optional[dict] = None
    create_time: Optional[str] = None


# ==================== 检索 ====================


class RetrieveReq(CamelModel):
    """多模态检索入参（SPEC §11.2-5：按知识库类型自动切换召回逻辑）。

    三类检索共用一个口：
    - 文本 query：``query``；
    - 图搜图：``image_url``（或 ``image_base64`` 由前端先传存储得到 url，保持接口只吃句柄），
      此时 ``query`` 可留空；
    - 跨库检索：``kb_ids`` 为空则覆盖当前用户全部可用库，非空则与白名单取交集。
    单库参数（top_k/阈值/模式）默认取各知识库 retrieve_config，显式传了才覆盖。
    """

    kb_ids: Optional[list[int]] = Field(None, description="限定检索的知识库，空=全部可用库")
    query: Optional[str] = Field(None, max_length=2000, description="文本查询")
    image_url: Optional[str] = Field(None, description="图搜图的查询图片地址")
    chunk_type: Optional[str] = Field(None, description="只召回某一模态：text/image/audio_video")
    mode: Optional[str] = Field(None, description="VECTOR/KEYWORD/HYBRID，空=按库配置")
    top_k: Optional[int] = Field(None, ge=1, le=100, description="每个库的召回条数")
    score_threshold: Optional[float] = Field(None, ge=0, le=1, description="相似度阈值")
    rerank: Optional[bool] = Field(None, description="是否重排（库未配重排模型时忽略）")
    vector_weight: Optional[float] = Field(None, ge=0, le=1, description="混合检索里向量路权重")
    with_graph: bool = Field(False, description="是否附带图谱实体（仅 doc 型生效）")


class RetrieveHit(CamelModel):
    """单条命中（文档给正文、媒体给可播/可看句柄，页面按 chunk_type 渲染卡片）。"""

    kb_id: int
    kb_name: Optional[str] = None
    doc_id: int = 0
    doc_name: Optional[str] = None
    chunk_id: int = 0
    chunk_index: int = 0
    chunk_type: str = "text"
    content: str = ""
    score: float = 0.0
    vector_score: float = 0.0
    keyword_score: float = 0.0
    title_path: Optional[str] = None
    page_num: int = 0
    sheet_name: Optional[str] = None
    media_url: Optional[str] = None
    media_duration: int = 0
    block_id: Optional[str] = None


class RetrieveResp(CamelModel):
    """检索回执（took_ms 与 warnings 是排查召回质量的必需项，不是装饰）。"""

    query: Optional[str] = None
    mode: str = Field("HYBRID", description="本次生效模式；多库模式不一致时为 MIXED")
    total: int = 0
    took_ms: int = 0
    kb_ids: list[int] = Field(default_factory=list, description="实际参与检索的库")
    hits: list[RetrieveHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    graph: dict = Field(default_factory=dict,
                        description="withGraph=true 时的图谱实体（{entities: [...]}）")


# ==================== 知识图谱 ====================


class GraphQueryReq(CamelModel):
    """图谱可视化查询（SPEC §10.3：底层裸查，kb_id 白名单由上层算好后强制带上）。

    doc_ids 与 keyword 都只用来定**起点**（中心实体），起点之外的邻域仍按知识库
    展开全部——把图硬卡在几篇文档里会让关系链断开，看上去像图谱没数据。
    entity_type 同样只筛起点：限定中心实体类型，不限制展开出来的邻居。
    """

    kb_ids: Optional[list[int]] = Field(None, description="空=全部可用 doc 型库")
    doc_ids: Optional[list[int]] = Field(None, description="限定文档，可看单文档子图")
    keyword: Optional[str] = Field(None, max_length=100, description="实体名模糊匹配/定位起点")
    entity_type: Optional[str] = Field(None, description="实体类型过滤，取值见 rag_constant.KG_ENTITY_TYPES")
    depth: int = Field(1, ge=1, le=3, description="从起点外扩几跳")
    limit: int = Field(0, ge=0, le=1000, description="返回节点上限，0=用配置默认值")


class GraphBuildReq(CamelModel):
    """构建图谱入参（页面按钮「构建图谱」）：按文档触发，先清掉这些文档上一轮的图数据。

    doc_ids 必填——「不传=整库」的兜底已经去掉：整库重抽等于按篇数烧一遍大模型调用，
    误点一次的代价比构建向量大得多。
    """

    doc_ids: list[int] = Field(default_factory=list, description="待构建文档（页面必须勾选）")
    force: bool = Field(True, description="已构建的文档是否重抽（先清旧子图再写，保证幂等）")
    resume: bool = Field(True, description="断点续抽：跳过上一轮已抽成功的批（关掉=从第一批重抽）")


class GraphNodeResp(CamelModel):
    """图谱节点（id 是 ``kb_id:类型:实体名`` 的稳定键，与 Neo4j 里的实体主键同一个拼法）。"""

    id: str = ""
    name: str = ""
    label: str = Field("Entity", description="节点标签：Entity/RagChunk/RagDocument（可视化只给实体）")
    entity_type: Optional[str] = None
    kb_id: int = 0
    summary: str = Field("", description="一句话实体画像（抽取时模型写的）")
    aliases: list[str] = Field(default_factory=list, description="别名（模型给出的同义写法）")
    weight: int = Field(0, description="出现频次，页面按它调节点大小")


class GraphEdgeResp(CamelModel):
    """图谱关系边。"""

    source: str = ""
    target: str = ""
    relation: str = Field("", description="边类型：RELATED_TO/MENTIONED_IN/EXTRACTED_FROM")
    name: Optional[str] = Field(None, description="RELATED_TO 上模型写出的具体关系描述")
    kb_id: int = 0
    doc_ids: list[int] = Field(default_factory=list, description="这条关系出自哪些文档（溯源用）")
    weight: int = 0


class GraphResp(CamelModel):
    """图谱子图回执。"""

    nodes: list[GraphNodeResp] = Field(default_factory=list)
    edges: list[GraphEdgeResp] = Field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = Field(False, description="命中数超过 limit，图被截断（页面提示用户缩小范围）")


# ==================== 配置回显 ====================


class RuntimeConfigResp(CamelModel):
    """运行参数与能力探测（配置面板据此置灰不可选项，SPEC §11.1-3）。

    只回显非敏感的键：mineru 段只给 base_url 与是否已配置，api_key 绝不外泄。
    """

    engines: dict = Field(default_factory=dict, description="{engine: {available, reason}}")
    strategies: list[str] = Field(default_factory=list)
    parse_defaults: dict = Field(default_factory=dict)
    chunk_defaults: dict = Field(default_factory=dict)
    retrieve_defaults: dict = Field(default_factory=dict)
    vector_dim: int = 0
    max_chunk_chars: int = 0
    mineru_configured: bool = False
    es_index: Optional[str] = None
    embedding_batch_size: int = 0
    open_api_max_files: int = 0


__all__ = [
    "CamelModel", "PageReq", "IdsReq",
    "KbPageReq", "KbSaveReq", "KnowledgeBaseResp",
    "DocPageReq", "DocTitleReq", "DocActionReq", "BuildVectorReq",
    "DocumentResp", "DocProgressResp", "UploadItemResp", "UploadResp",
    "ChunkPageReq", "ChunkUpdateReq", "ChunkResp",
    "RetrieveReq", "RetrieveHit", "RetrieveResp",
    "GraphQueryReq", "GraphBuildReq", "GraphNodeResp", "GraphEdgeResp", "GraphResp",
    "RuntimeConfigResp",
]
