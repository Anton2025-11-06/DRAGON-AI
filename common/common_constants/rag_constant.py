# -*- coding: utf-8 -*-
"""知识库（RAG）常量单一来源：类型、状态机、解析引擎、分块策略、检索默认值、任务名与 Redis 键。

为什么单独成模块（而不是塞进 constant.py）：
- 这些取值同时落在 MySQL 的 VARCHAR 列、ES 的 keyword、Neo4j 的属性与前端下拉里，
  任何一处手写一份字面量就会与其它处分叉；本模块是**唯一**定义点，业务代码禁止出现字面量。
- 与 tb_* 表 DDL 的对应关系写在 sql/v2_init.sql 的列注释里，改这里必须同时改 DDL 注释。

口径来源：test/RAG知识库模块开发SPEC.md（§6 类型体系、§7 文档型、§8 图片型、§9 音视频型、
§10 存储与向量规则、§4 ARQ 双流水线）。
"""
from __future__ import annotations

from common.common_constants.model_constant import (
    MT_AUDIO_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND,
)

# =====================================================================================
# 一、知识库类型（SPEC §6：创建后永久锁定，不可修改）
# =====================================================================================
KB_TYPE_DOC = "doc"                  # 文档问答：解析→分块→文本向量→（可选）知识图谱
KB_TYPE_IMAGE = "image"              # 图片搜索：文搜图 / 图搜图（多模态向量，无图谱）
KB_TYPE_AUDIO_VIDEO = "audio_video"  # 音视频搜索：文搜音视频（全局一条向量 + 摘要）

KB_TYPES_ALL = [KB_TYPE_DOC, KB_TYPE_IMAGE, KB_TYPE_AUDIO_VIDEO]

KB_TYPE_LABELS = {
    KB_TYPE_DOC: "文档问答",
    KB_TYPE_IMAGE: "图片搜索",
    KB_TYPE_AUDIO_VIDEO: "音视频搜索",
}

# 媒体扩展名按「要用哪个模型去读它」分三组，单独列出来是因为 doc 型库也要收这些文件：
# 图片交给图片理解模型、音频交给音频转文字模型、视频交给视频理解模型，转成文字之后
# 与普通文档走同一条分块/向量/图谱流水线（见 parse_service 的媒体转写分支）。
RAG_IMAGE_EXTS = ["jpg", "jpeg", "png", "webp", "bmp", "gif"]
RAG_AUDIO_EXTS = ["mp3", "wav", "m4a", "aac", "flac"]
RAG_VIDEO_EXTS = ["mp4", "mov", "avi", "mkv", "webm"]

# doc 型库的文档格式：只有这些进解析引擎（native/docling/mineru）
DOC_TEXT_EXTS = [
    "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "csv",
    "md", "markdown", "html", "htm", "json", "txt",
]
# doc 型库额外接受的媒体文件：不进解析引擎，由大模型先转成文字
DOC_MEDIA_EXTS = RAG_IMAGE_EXTS + RAG_AUDIO_EXTS + RAG_VIDEO_EXTS

# 各类型的可上传扩展名（小写、不含点；SPEC §7.1/§8.1/§9.1）。
# 上传接口按这份白名单卡格式：选了图片库却传 PDF，要等 worker 解析时才炸，
# 而那时文件已经落了存储、任务已经排队，回滚成本比上传时直接拒高一个量级。
KB_TYPE_ALLOWED_EXTS = {
    # 文档问答型 = 文档格式 + 媒体文件（媒体按解析配置里的模型转文字）
    KB_TYPE_DOC: DOC_TEXT_EXTS + DOC_MEDIA_EXTS,
    KB_TYPE_IMAGE: list(RAG_IMAGE_EXTS),
    KB_TYPE_AUDIO_VIDEO: RAG_AUDIO_EXTS + RAG_VIDEO_EXTS,
}

# 各类型是否支持知识图谱（SPEC §6 表格：只有文档问答型支持）
KB_TYPE_GRAPH_SUPPORTED = {
    KB_TYPE_DOC: True,
    KB_TYPE_IMAGE: False,
    KB_TYPE_AUDIO_VIDEO: False,
}

# 各类型的向量模型能力要求（建库时校验，选错模型等于写入一个检索不出来的向量空间）
#   embed_any：满足其一即可作为向量模型
#   chat_required：是否必须有问答/抽取用大模型（图谱抽取、图片描述、媒体摘要）
#   understand_category：媒体理解模型的类型（image 型不做描述故为 None，audio_video 型必需）
KB_TYPE_MODEL_REQ = {
    KB_TYPE_DOC: {
        "embed_any": ["text_embedding", "multimodal_embedding"],
        "chat_required": False,          # 不开图谱与图片增强时可以为空
        "understand_category": None,
    },
    KB_TYPE_IMAGE: {
        # 文搜图要求文本与图片落在同一向量空间，故只能多模态向量
        "embed_any": ["multimodal_embedding"],
        "chat_required": False,
        "understand_category": None,      # SPEC §8：不做图片自动描述
    },
    KB_TYPE_AUDIO_VIDEO: {
        "embed_any": ["multimodal_embedding"],
        "chat_required": True,
        "understand_category": "video_understand",  # SPEC §9：整条媒体的综合描述
    },
}

# =====================================================================================
# 二、向量与存储规则（SPEC §10.1/§10.2：固定不可改）
# =====================================================================================
# 全模态统一维度：文本 / 图片 / 视频共用同一个多模态向量空间，天然支持跨模态检索
RAG_VECTOR_DIM = 1024
# ES 单索引：不按知识库类型拆索引，靠 kb_id 前缀隔离（建索引即固定 dense_vector 维度）
RAG_CHUNK_INDEX = "rag_knowledge_chunk"

# ES 文档 _id 拼法：{kb_id}_{doc_id}_{chunk_index}
#   与 tb_document_chunk 的 uk_doc_chunk(doc_id, chunk_index) 一一对应，
#   重跑同一文档时按 _id 幂等覆盖，不会出现「重跑一次切片翻倍」。
ES_ID_SEP = "_"

# ES mapping 字段（SPEC §10.2 定稿的字段清单，业务代码按名引用，不写字面量）
ES_FIELD_ORG = "org_id"
ES_FIELD_KB = "kb_id"
ES_FIELD_DOC = "doc_id"
ES_FIELD_CHUNK = "chunk_id"
ES_FIELD_INDEX = "chunk_index"
ES_FIELD_TYPE = "chunk_type"
ES_FIELD_CONTENT = "content"
ES_FIELD_EMBED = "embedding"
ES_FIELD_MEDIA_URL = "media_url"
ES_FIELD_TITLE_PATH = "title_path"
ES_FIELD_PAGE = "page_num"
ES_FIELD_SHEET = "sheet_name"
ES_FIELD_AVAILABLE = "available"
ES_FIELD_DELETED = "is_deleted"
ES_FIELD_CREATE_TIME = "create_time"
ES_FIELD_SOURCE = "source"

# =====================================================================================
# 三、切片 / 媒体的模态标记（MySQL chunk_type、ES chunk_type 同值）
# =====================================================================================
CHUNK_TYPE_TEXT = "text"
CHUNK_TYPE_IMAGE = "image"
CHUNK_TYPE_AUDIO_VIDEO = "audio_video"
CHUNK_TYPES_ALL = [CHUNK_TYPE_TEXT, CHUNK_TYPE_IMAGE, CHUNK_TYPE_AUDIO_VIDEO]
CHUNK_TYPE_LABELS = {
    CHUNK_TYPE_TEXT: "文本",
    CHUNK_TYPE_IMAGE: "图片",
    CHUNK_TYPE_AUDIO_VIDEO: "音视频",
}

# 文档行的 media_type（与库类型同族，决定列表页预览形态）
MEDIA_TYPE_TEXT = "text"
MEDIA_TYPE_IMAGE = "image"
MEDIA_TYPE_AUDIO_VIDEO = "audio_video"

# 库类型 → 文档的 media_type（上传时写这一位，页面据此选卡片/图片网格/媒体播放器）
KB_TYPE_MEDIA_TYPE = {
    KB_TYPE_DOC: MEDIA_TYPE_TEXT,
    KB_TYPE_IMAGE: MEDIA_TYPE_IMAGE,
    KB_TYPE_AUDIO_VIDEO: MEDIA_TYPE_AUDIO_VIDEO,
}

# doc 型库收到的媒体文件按这一位分派模型（三种媒体读的端点不是一个，不能一把梭）
MEDIA_KIND_IMAGE = "image"
MEDIA_KIND_AUDIO = "audio"
MEDIA_KIND_VIDEO = "video"
MEDIA_KIND_LABELS = {
    MEDIA_KIND_IMAGE: "图片",
    MEDIA_KIND_AUDIO: "音频",
    MEDIA_KIND_VIDEO: "视频",
}
# 媒体种类 → 该用哪个能力类型的模型去读它（audio→音频转文字，video→视频理解）
MEDIA_KIND_CATEGORY = {
    MEDIA_KIND_IMAGE: MT_IMAGE_UNDERSTAND,
    MEDIA_KIND_AUDIO: MT_AUDIO_TO_TEXT,
    MEDIA_KIND_VIDEO: MT_VIDEO_UNDERSTAND,
}
# 媒体种类 → 文档行/切片行的展示形态（audio_video 是对外只有「音、视」两模态合流的一位，
# ES mapping、前端分桶、静态对账三方都锁着 CHUNK_TYPES_ALL，内嵌音视频不许新增模态位）
MEDIA_KIND_MEDIA_TYPE = {
    MEDIA_KIND_IMAGE: MEDIA_TYPE_IMAGE,
    MEDIA_KIND_AUDIO: MEDIA_TYPE_AUDIO_VIDEO,
    MEDIA_KIND_VIDEO: MEDIA_TYPE_AUDIO_VIDEO,
}
_MEDIA_KIND_BY_EXT = {
    **{e: MEDIA_KIND_IMAGE for e in RAG_IMAGE_EXTS},
    **{e: MEDIA_KIND_AUDIO for e in RAG_AUDIO_EXTS},
    **{e: MEDIA_KIND_VIDEO for e in RAG_VIDEO_EXTS},
}


def media_kind_of_ext(ext) -> str:
    """扩展名 → 媒体种类；不是媒体就返回空串（doc 型据此决定要不要跳过解析引擎）。

    带点、大写都认：上传与文档行里存的是去点小写，但调用方常常拿到手写的 ``.MP4``。
    """
    return _MEDIA_KIND_BY_EXT.get(str(ext or "").strip().lower().lstrip("."), "")


# MIME 前缀 → 媒体种类。文档**内嵌**的媒体没有扩展名可看（块类型体系里也没有 audio/video 位），
# 而 docx/pptx 的媒体部件自带 content_type，靠它分派就不必给 sidecar 加字段、不必升版本号。
_MEDIA_KIND_BY_MIME_PREFIX = {
    "image/": MEDIA_KIND_IMAGE,
    "audio/": MEDIA_KIND_AUDIO,
    "video/": MEDIA_KIND_VIDEO,
}


def media_kind_of_mime(mime) -> str:
    """MIME → 媒体种类；认不出返回空串（调用方按图片处理，与历史产物一律是图的现状一致）。"""
    value = str(mime or "").strip().lower()
    for prefix, kind in _MEDIA_KIND_BY_MIME_PREFIX.items():
        if value.startswith(prefix):
            return kind
    return ""


def doc_media_type_of(ext) -> str:
    """媒体扩展名 → 文档行的 media_type：图片算 image，音频与视频都归 audio_video。

    只有 doc 型库需要它：另外两型的 media_type 由库类型直接决定（KB_TYPE_MEDIA_TYPE）。
    """
    return doc_media_type_of_kind(media_kind_of_ext(ext))


def doc_media_type_of_kind(kind) -> str:
    """媒体种类 → media_type/chunk_type：扩展名与 MIME 两条来路最终都汇到这一颗。"""
    return MEDIA_KIND_MEDIA_TYPE.get(str(kind or "").strip().lower(), "")


# 没有描述时给媒体块占位的文字：媒体块必须有文字，空正文既进不了 BM25 也没有可展示的引用片段
MEDIA_KIND_PLACEHOLDERS = {
    MEDIA_KIND_IMAGE: "[图片]",
    MEDIA_KIND_AUDIO: "[音频]",
    MEDIA_KIND_VIDEO: "[视频]",
}
MEDIA_KIND_PLACEHOLDER_DEFAULT = MEDIA_KIND_PLACEHOLDERS[MEDIA_KIND_IMAGE]

# 媒体种类 → 「解析增强」开关的键名：钩子按这一位决定要不要为一条内嵌媒体付一次模型调用
MEDIA_KIND_UNDERSTAND_OPTION = {
    MEDIA_KIND_IMAGE: "image_understand",
    MEDIA_KIND_AUDIO: "audio_understand",
    MEDIA_KIND_VIDEO: "video_understand",
}


# =====================================================================================
# 四、解析引擎（SPEC §7.2 三选一 + 自动）
# =====================================================================================
PARSE_ENGINE_NATIVE = "native"    # 默认：pypdf/python-docx/openpyxl 等轻量库，无模型依赖
PARSE_ENGINE_DOCLING = "docling"  # pip 依赖，CPU 版面分析与表格还原
PARSE_ENGINE_MINERU = "mineru"    # 私有化 HTTP API，扫描件/公文/双栏教材高精度
PARSE_ENGINE_AUTO = "auto"        # 由文档复杂度自动选引擎（解析时回填实际使用的引擎）

PARSE_ENGINES_ALL = [PARSE_ENGINE_NATIVE, PARSE_ENGINE_DOCLING, PARSE_ENGINE_MINERU,
                     PARSE_ENGINE_AUTO]

# 解析与预处理可配置项默认值（SPEC §7.3 预处理开关 + §7.4 图片智能解析，默认全关）
PARSE_OPTION_DEFAULTS = {
    "remove_toc": False,             # 移除目录标题
    "remove_header_footer": False,   # 移除页眉页脚
    # 三种媒体的「解析增强」开关（需求：不限解析引擎，页面常显；开着才调模型补描述，
    # 关掉只把媒体地址回填到正文原位——上传那一步永远不看开关，正文里的媒体必须看得见）。
    # 内嵌音频/视频只有 native 能从 docx/pptx 包里取出字节，另两引擎拿不到，
    # 开关对它们不是「不给增强」而是「没有可增强的对象」，页面 hint 要照这句写。
    "image_understand": False,       # 图片解析增强：关=图片只传存储并回填正文位置，开=再用图片理解模型补一段描述
    "audio_understand": False,       # 音频解析增强：开=文档内嵌音频走音频解析模型转成文字
    "video_understand": False,       # 视频解析增强：开=文档内嵌视频走视频解析模型描述成文字
    "image_desc_prompt": "",         # 图片描述提示词，留空用内置默认
    # 文档型知识库收到图片/音频/视频**文件**时的转写模型（tb_model.id，0=未配置）：
    #   图片复用库上的 image_model_id 一位（它与文档内嵌图片增强是同一个模型，公用配置）；
    #   音频与视频没有现成的列，且既服务独立文件转写也服务文档内嵌媒体的解析增强。
    "audio_model_id": 0,             # 音频解析模型（audio_to_text）：整条音频转写成正文
    "video_model_id": 0,             # 视频解析模型（video_understand）：整条视频描述成正文
    "video_desc_prompt": "",         # 视频内容描述提示词，留空用内置默认
    "table_as_text": True,           # 表格渲染成 markdown 文本块（关掉则退成「列名: 值」逐行文本，不丢数据只丢结构）
    "keep_page_break": False,        # 保留分页标记（供「每页分块」以外的策略也能按页切）
    # 引擎专有参数（native 无；docling/mineru 各自的开关在 engines 内读取）
    "docling_table_format": "markdown",    # docling 表格导出形态：markdown | html
    "mineru_backend": "pipeline",        # minerU 私有化服务的 backend 取值
    "mineru_timeout": 1800,              # 单文档等待上限（秒），与 Nacos mineru.timeout 同源
}

# auto 引擎的复杂度判据：页数/是否扫描件超过阈值就走增强解析
AUTO_ENGINE_MIN_PAGES = 30          # 页数达到即认为复杂
AUTO_ENGINE_MIN_IMAGES = 10         # 内嵌图片数达到即认为复杂
AUTO_ENGINE_SCAN_TEXT_RATIO = 0.1   # 首页可抽字符数/图片数偏低 → 判定扫描件倾向

# =====================================================================================
# 五、分块策略（SPEC §7.5：七种全前端可配，统一单块上限 20000 字符）
#   原「LangChain 递归分块」与「自定义分隔符分块」断开点同源（都按分隔符优先级切、再按目标
#   长度合并），唯一差别是 overlap，已并给 delimiter，故不再作为可选策略对外提供。
# =====================================================================================
CHUNK_FIXED = "fixed"              # 固定文本长度分块（长度 + 重叠）
CHUNK_DELIMITER = "delimiter"      # 自定义分隔符分块（多分隔符优先级 + 重叠）
CHUNK_TITLE = "title"              # 标题层级分块（一/二/三级，超长合并截断）
CHUNK_PAGE = "page"                # 每页单独分块（PDF/PPT 专属）
CHUNK_SEMANTIC = "semantic"        # 语义分块（句向量相似度聚类）
CHUNK_EXCEL = "excel"              # Excel 专属：每块强制附带原始表头
CHUNK_REGEX = "regex"              # 正则表达式分块（自定义章节/条款切割）

CHUNK_STRATEGIES_ALL = [CHUNK_FIXED, CHUNK_DELIMITER, CHUNK_TITLE, CHUNK_PAGE,
                        CHUNK_SEMANTIC, CHUNK_EXCEL, CHUNK_REGEX]
CHUNK_STRATEGY_LABELS = {
    CHUNK_FIXED: "固定长度分块",
    CHUNK_DELIMITER: "自定义分隔符分块",
    CHUNK_TITLE: "标题层级分块",
    CHUNK_PAGE: "按页分块",
    CHUNK_SEMANTIC: "语义分块",
    CHUNK_EXCEL: "Excel 表头分块",
    CHUNK_REGEX: "正则分块",
}
# 只对该扩展名有意义的策略（页面据此置灰，后端在归一时回落 fixed）
CHUNK_STRATEGY_EXCLUSIVE_EXT = {
    CHUNK_PAGE: {"pdf", "ppt", "pptx"},
    CHUNK_EXCEL: {"xls", "xlsx", "csv"},
}
# 已下线策略的兼容映射：存量 tb_knowledge_base.chunk_config.strategy 里存的还是旧值，
# 不改库而是在归一时映射过去——否则历史库会被当成「未知策略」降级成固定长度，
# 用户什么都没改，切片形态却悄悄变了。值里带旧中文名，降级警告要能说出被合并掉了什么。
CHUNK_RECURSIVE = "recursive"      # 旧策略名，只作为 CHUNK_DELIMITER 的别名存在
CHUNK_STRATEGY_LEGACY = {CHUNK_RECURSIVE: (CHUNK_DELIMITER, "LangChain 递归分块")}

MAX_CHUNK_CHARS = 20000        # 单块硬上限（SPEC §7.5：全部策略统一）
MIN_CHUNK_CHARS = 50           # 单块下限（再小就是碎屑，检索命中没有语义）
DEFAULT_CHUNK_SIZE = 512       # 默认单块长度（token）
DEFAULT_CHUNK_OVERLAP = 64     # 默认重叠长度（token）
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "]

# 分块配置默认值（tb_knowledge_base.chunk_config 的完整形态，写前/读后都按这份补齐缺键）
CHUNK_CONFIG_DEFAULTS = {
    "strategy": CHUNK_FIXED,
    "chunk_size": DEFAULT_CHUNK_SIZE,
    "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
    "max_chars": MAX_CHUNK_CHARS,
    "separators": DEFAULT_SEPARATORS,   # delimiter 策略：多分隔符列表
    "regex_pattern": "",                # regex 策略：用户自定义正则
    "title_level": 3,                   # title 策略：按 1~3 级标题切
    "keep_table_header": True,          # excel 策略：强制带表头
    "title_path": True,                 # 面包屑前缀写入 embed_text（不污染展示正文）
    "semantic_threshold": 0.5,          # semantic 策略：相邻句相似度低于此值断开
    # 通用增强（SPEC §7.5 末）：图表/表格/图片的前后文补齐
    "context_augment": {"enabled": False, "before": 120, "after": 120},
}

# 检索配置默认值（tb_knowledge_base.retrieve_config）
RETRIEVE_DEFAULTS = {
    "top_k": 5,
    "score_threshold": 0.2,
    "vector_similarity_weight": 0.7,   # 混合检索里向量路的权重，余下给关键词 BM25
    "rerank": False,                   # 有 rerank 模型时才生效
    "keyword_boost": 1.0,              # BM25 分值放大系数（不同库量纲差异用这个抹平）
    "recall_size": 50,                 # 融合前每路召回的候选数
}

# 检索模式（工作流知识检索节点的 retrievalMode，与 ES 混合检索的三档对应）
RETRIEVE_MODE_VECTOR = "VECTOR"
RETRIEVE_MODE_KEYWORD = "KEYWORD"
RETRIEVE_MODE_HYBRID = "HYBRID"
RETRIEVE_MODES_ALL = [RETRIEVE_MODE_VECTOR, RETRIEVE_MODE_KEYWORD, RETRIEVE_MODE_HYBRID]

# =====================================================================================
# 六、文档解析状态机（tb_document.status，大写字符串；进度明细在 Redis 见第八节）
# =====================================================================================
DOC_STATUS_PENDING = "PENDING"        # 已入队，等待 worker 取走
DOC_STATUS_PARSING = "PARSING"        # 解析中（native/docling/mineru）
DOC_STATUS_ANALYZING = "ANALYZING"    # 分块 + 图片增强/媒体理解中
DOC_STATUS_PROCESSING = "PROCESSING"  # 向量化与写 ES 中
DOC_STATUS_PROCESSED = "PROCESSED"    # 终态：可检索
DOC_STATUS_FAILED = "FAILED"          # 终态：失败，error_msg 给到页面

DOC_STATUS_ALL = [DOC_STATUS_PENDING, DOC_STATUS_PARSING, DOC_STATUS_ANALYZING,
                  DOC_STATUS_PROCESSING, DOC_STATUS_PROCESSED, DOC_STATUS_FAILED]
DOC_STATUS_LABELS = {
    DOC_STATUS_PENDING: "排队中",
    DOC_STATUS_PARSING: "解析中",
    DOC_STATUS_ANALYZING: "分块中",
    DOC_STATUS_PROCESSING: "向量化中",
    DOC_STATUS_PROCESSED: "已完成",
    DOC_STATUS_FAILED: "失败",
}
#   合法流转表：重试/重新解析一律回到 PENDING 重新走一遍，不从 FAILED 直接跳回中间态
#   （中间态带着「上一跑到了哪一步」的隐含语义，跳回去会读到上一跑的残留产物）
DOC_STATUS_TRANSITIONS = {
    DOC_STATUS_PENDING: [DOC_STATUS_PARSING, DOC_STATUS_ANALYZING, DOC_STATUS_PROCESSING,
                         DOC_STATUS_FAILED],
    DOC_STATUS_PARSING: [DOC_STATUS_ANALYZING, DOC_STATUS_PROCESSING,
                         DOC_STATUS_PENDING, DOC_STATUS_FAILED],
    DOC_STATUS_ANALYZING: [DOC_STATUS_PROCESSING, DOC_STATUS_PENDING, DOC_STATUS_FAILED],
    DOC_STATUS_PROCESSING: [DOC_STATUS_PROCESSED, DOC_STATUS_PENDING, DOC_STATUS_FAILED],
    DOC_STATUS_PROCESSED: [DOC_STATUS_PENDING],   # 重新解析/重新分块
    DOC_STATUS_FAILED: [DOC_STATUS_PENDING],      # 失败重试
}

# 图谱构建态（tb_document.graph_state：文档级后置任务，与解析状态机互不干涉）
KG_STATE_NONE = 0
KG_STATE_BUILDING = 1
KG_STATE_DONE = 2
KG_STATE_FAILED = 3
KG_STATE_LABELS = {
    KG_STATE_NONE: "未构建",
    KG_STATE_BUILDING: "构建中",
    KG_STATE_DONE: "已构建",
    KG_STATE_FAILED: "失败",
}

# =====================================================================================
# 七、ARQ 任务名与入队约定（SPEC §4.1：文档解析走 ragflow，图谱构建走 graphflow）
# =====================================================================================
# 短名（业务侧引用）与 worker 注册路径（arq 侧引用）分开：
#   业务代码只写短名，rag_task_name() 统一展开成点路径，避免 enqueue 与 functions 两处拼字符串。
RAG_TASK_PARSE = "parse_document"            # doc 型：解析→分块→向量化
RAG_TASK_MEDIA = "parse_media_document"      # image / audio_video 型：媒体向量（+摘要）
RAG_TASK_GRAPH = "build_document_graph"      # 文档级图谱构建（库开关系 + 手动触发），独立 graphflow 队列
RAG_TASK_KB_PURGE = "purge_knowledge_base"   # 删库/删文档后的 ES/Neo4j/存储清理

RAG_TASKS_ALL = [RAG_TASK_PARSE, RAG_TASK_MEDIA, RAG_TASK_GRAPH, RAG_TASK_KB_PURGE]

# rag 流水线的任务函数模块（worker_settings_rag 注册与生产端投递共用这一处）
RAG_TASK_MODULE = "arq_tasks.tasks.ragflow"
# graphflow 流水线的任务函数模块（图谱构建拆出独立队列，与文档解析互不抢占 worker）
GRAPH_TASK_MODULE = "arq_tasks.tasks.graphflow"

# 任务名 → 所在函数模块：图谱构建已拆到 graphflow，其余仍在 ragflow。
# enqueue 与 worker 注册都经 rag_task_name() 取这一个映射，两处不可能拼出两个路径。
RAG_TASK_MODULES = {
    RAG_TASK_PARSE: RAG_TASK_MODULE,
    RAG_TASK_MEDIA: RAG_TASK_MODULE,
    RAG_TASK_KB_PURGE: RAG_TASK_MODULE,
    RAG_TASK_GRAPH: GRAPH_TASK_MODULE,
}


def rag_task_name(short_name: str) -> str:
    """短任务名 → arq 注册用的完整函数路径（未登记的任务名直接报错，防投了没人消费）。"""
    module = RAG_TASK_MODULES.get(short_name)
    if module is None:
        raise ValueError(f"未登记的 rag 任务名: {short_name}（合法值 {RAG_TASKS_ALL}）")
    return f"{module}.{short_name}"


# =====================================================================================
# 八、Redis 键（进度、幂等锁）
#   进度在 API 进程与 worker 进程之间传递，键的拼法只有这一处。
# =====================================================================================
# 向量进度 hash：rag:progress:{doc_id}，字段见 RAG_PROGRESS_FIELDS，TTL 见下
RAG_PROGRESS_PREFIX = "rag:progress:"
# 图谱进度 hash：rag:graph:progress:{doc_id}
#   与向量进度分键而不是一起挤在 rag:progress: 里：图谱是文档解析完成**之后**才跑的
#   后置任务，两者同时在推进。共用一份 stage/percent 时，图谱一开抽就把已经跑到 90%
#   的向量进度拉回 0%，页面表现为「进度条倒着走」；分键后两条进度条各自单调。
RAG_GRAPH_PROGRESS_PREFIX = "rag:graph:progress:"
# 文档级流水线锁：rag:lock:doc:{doc_id}（同一文档同时只跑一个任务，重复入队直接拒）
RAG_LOCK_DOC_PREFIX = "rag:lock:doc:"
# 知识库级清理锁：rag:lock:kb:{kb_id}
RAG_LOCK_KB_PREFIX = "rag:lock:kb:"
# 入队幂等 job_id 前缀（arq 侧同 job_id 重复投递会被拒绝，这里再落一份便于回查）
RAG_JOB_PREFIX = "rag:job:"
# 图谱抽取断点 hash：rag:kg:ckpt:{doc_id}
#   图谱抽取是「按批送大模型」的长任务，一次失败过去会把整篇打成 FAILED，前面所有批
#   已经烧掉的模型调用作废，下次点构建又从第一批重来。断点只存**已经抽成功的批的归一化
#   结果**，重跑时按批命中就跳过模型调用。它是省钱手段而不是正确性依赖：Redis 被清最多
#   退化成从第一批重抽，「这篇文档到底有没有图谱」的权威判断仍在 MySQL 的 graph_state。
RAG_KG_CKPT_PREFIX = "rag:kg:ckpt:"

RAG_PROGRESS_TTL = 24 * 3600   # 进度保留 24 小时（页面隔天回查还能看到最后一次结果）
RAG_LOCK_TTL = 6 * 3600        # 锁 TTL 必须大于单文档最长流水线（minerU 大文件 30 分钟量级）
# 断点保 7 天：比进度活得久（一篇大文档往往是隔几天想起来才重试一次），比文档活得短
#   （过期就当没抽过，重抽的代价远低于让一份半年前的抽取结果混进今天的图谱）
KG_CKPT_TTL = 7 * 24 * 3600

# 进度字段名（前端轮询接口原样透出，字段增删要同时改 rag_schema 的 DocProgressResp）
RAG_PROGRESS_FIELDS = ("stage", "percent", "total", "done", "message", "update_time")
# 向量化进度阶段（只含解析→向量→索引这一段；图谱已拆到 RAG_GRAPH_STAGES）
RAG_STAGES = [
    ("download", "读取文档"),
    ("parse", "文档解析"),
    ("preprocess", "内容处理"),
    ("chunk", "智能分块"),
    ("embedding", "构建向量"),
    ("index", "向量入库"),
]
# 图谱进度阶段（独立一条分段进度条，与向量进度同时存在、互不覆盖）
RAG_GRAPH_STAGES = [
    ("graph_load", "读取分块"),
    ("graph_extract", "实体抽取"),
    ("graph_write", "写入图谱"),
    ("graph_done", "构建完成"),
]
# 进度域（同一篇文档的两套进度靠它区分，调用方不拼字符串）
PROGRESS_SCOPE_VECTOR = "vector"
PROGRESS_SCOPE_GRAPH = "graph"


def progress_key(doc_id: int, scope: str = PROGRESS_SCOPE_VECTOR) -> str:
    prefix = RAG_GRAPH_PROGRESS_PREFIX if scope == PROGRESS_SCOPE_GRAPH \
        else RAG_PROGRESS_PREFIX
    return f"{prefix}{int(doc_id)}"


def doc_lock_key(doc_id: int) -> str:
    return f"{RAG_LOCK_DOC_PREFIX}{int(doc_id)}"


def kb_lock_key(kb_id: int) -> str:
    return f"{RAG_LOCK_KB_PREFIX}{int(kb_id)}"


def job_key(task: str, doc_id: int) -> str:
    """arq job_id：同一文档同一任务只允许一个在跑（重复投递被 arq 拒掉即幂等防重）。"""
    return f"{RAG_JOB_PREFIX}{task}:{int(doc_id)}"


def kg_ckpt_key(doc_id: int) -> str:
    """图谱抽取断点 hash：字段见 KG_CKPT_FIELD_META / KG_CKPT_BATCH_PREFIX，TTL 见 KG_CKPT_TTL。"""
    return f"{RAG_KG_CKPT_PREFIX}{int(doc_id)}"


# =====================================================================================
# 九、Neo4j 图谱（SPEC §10.3：仅服务 doc 型，节点/关系全部携带 org_id/kb_id）
# =====================================================================================
KG_LABEL_DOC = "RagDocument"     # 文档节点
KG_LABEL_CHUNK = "RagChunk"      # 切片节点（图谱溯源到具体 chunk）
KG_LABEL_ENTITY = "Entity"       # 实体节点（type 属性区分类别）
KG_REL_MENTIONED = "MENTIONED_IN"  # Entity -[:MENTIONED_IN]-> Chunk
KG_REL_EXTRACT = "EXTRACTED_FROM"     # Chunk -[:EXTRACTED_FROM]-> Document
KG_REL_RELATION = "RELATED_TO"        # Entity -[:RELATED_TO {relation}]-> Entity

# 实体类型（抽取提示词与前端图例共用这一份，避免模型自由发挥出十种类型）
KG_ENTITY_TYPES = ["人物", "组织", "地点", "时间", "产品", "技术", "事件", "指标", "其他"]

# 图谱规模（唯一取值点，配置中心不再覆盖；读取口见 rag_settings 同名函数）
#   KG_EXTRACT_BATCH_CHUNKS 只是**一次大模型调用喂多少片**的分批粒度，不再是总片数封顶：
#     文档的所有可用切片都会抽，批与批的结果在 Python 侧按实体名合并
#   KG_VIZ_DEFAULT_LIMIT 只是页面没传 limit 时的默认展示规模，不是拦截（传大就返回大）
KG_EXTRACT_BATCH_CHUNKS = 20
KG_VIZ_DEFAULT_LIMIT = 100

# 单批抽取失败时的**原地重试**：首次之外再试 KG_EXTRACT_BATCH_RETRIES 次，只重这一批，
# 第 n 次重试前等 KG_EXTRACT_RETRY_BACKOFF * 2**(n-1) 秒。
#   为什么在这层重试而不是交给 arq：graph worker 是 max_tries=1 / retry_jobs=False（一次
#   失败不自动重投，由页面按钮显式再投），而 arq 级的重投等于整篇重来。上游 429、网络
#   抖动、模型偶尔回一句非 JSON 是三类最常见失败，绝大多数第二次就好——代价只是一个批。
KG_EXTRACT_BATCH_RETRIES = 2
KG_EXTRACT_RETRY_BACKOFF = 2.0

# 断点 hash 的字段名（值一律是字符串，Redis 侧不认识 Python 结构）
KG_CKPT_FIELD_META = "meta"     # 批次划分签名：结构变了整份断点作废
KG_CKPT_BATCH_PREFIX = "b"      # 批次结果字段：b1/b2/...（值是这一批的 JSON）

# 图谱构建任务的断点策略（build 任务 options["ckpt"] 取值，与 RAG_SIDECAR_* 同构）
#   resume：默认，吃上一轮的断点，只抽没抽成功的批（页面那个勾选项）
#   restart：不认旧断点，从第一批重抽并逐批覆盖它（取消勾选，或切片内容被人工改过想全量重来）
RAG_KG_CKPT_RESUME = "resume"
RAG_KG_CKPT_RESTART = "restart"
RAG_KG_CKPT_MODES = [RAG_KG_CKPT_RESUME, RAG_KG_CKPT_RESTART]

# =====================================================================================
# 十、开放 API 与批量上传（SPEC §11.2）
# =====================================================================================
# OSS bucket 内的对象前缀：storage.path_prefix=rag/ 之下再按 kb_id 目录隔离
#   原件 rag/raw/{kb_id}/{doc_id}.{ext}
#   解析产物 rag/sidecar/{kb_id}/{doc_id}.jsonl
#   文档内嵌图片 rag/image/{kb_id}/{doc_id}/{block_id}.{ext}
RAG_OBJ_DIR_RAW = "raw"
RAG_OBJ_DIR_SIDECAR = "sidecar"
RAG_OBJ_DIR_IMAGE = "image"

# 单次批量上传的文件数上限（只卡开放 API，页面上传由前端自己控制）；
# 预览、重排候选、图谱抽取规模等人为上限已全部取消，代码不再悄悄丢数据
OPEN_API_MAX_FILES = 100

# 向量化批量与并发（唯一取值点，配置中心不再覆盖）：这两项是调用协议约束——
# 一次请求塞几百条会直接超时，并发不限流会打爆上游私有化服务，都不是「数据上限」
# 这里只管「一次调用送多少条」的业务粒度；厂商单次请求的条数/长度硬上限由
# common_model 各能力层自己拆批兜住，不在业务层拿上限剪数据：
#   text_embedding.max_batch_size       通义 v3/v4 = 10 条、智谱 embedding-3 = 64 条
#   text_rerank.max_documents 等         通义 gte-rerank-v2 = 500 条 / 30,000 token、智谱 = 128 条
#   multimodal/image 向量 的 contents 配额  通义 multimodal-embedding-v1 图片一次只 1 张
# 比上限大的批量会被分成几次请求，不会把整篇文档的解析打成 400
EMBEDDING_BATCH_SIZE = 16
EMBEDDING_CONCURRENCY = 2

# 侧车产物（解析结果 blocks.jsonl）的格式版本：结构变更即 +1，旧产物读到不匹配就重解析
# 2：内嵌图片改为「一律传存储并回填正文地址」，并丢弃 <!-- image --> 这类注释占位——
#    版本 1 的产物里图片位置全是无效占位文字，只能重解析才能救回来
SIDECAR_VERSION = 2

# 解析任务的解析产物复用策略（parse 任务的 options["sidecar"] 取值）
#   auto：默认，解析产物版本与知识库当前 version 相同才复用（重试、新上传都走这一档）
#   reuse：只重分块——用户明确说了不必重解析，忽略版本差强制吃 sidecar
#   reparse：强制重解析（换了引擎、预处理开关后的重新分块）
RAG_SIDECAR_AUTO = "auto"
RAG_SIDECAR_REUSE = "reuse"
RAG_SIDECAR_REPARSE = "reparse"
RAG_SIDECAR_MODES = [RAG_SIDECAR_AUTO, RAG_SIDECAR_REUSE, RAG_SIDECAR_REPARSE]

