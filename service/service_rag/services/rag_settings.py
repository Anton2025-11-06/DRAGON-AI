# -*- coding: utf-8
"""知识库运行参数与配置归一（Nacos ``service_rag`` 的 mineru/es 段 + 三份 JSON 配置）。

为什么这一层单独存在
--------------------
1. **配置只有一个入口**：解析层（common_file_parser）与存储层（common_es/common_neo4j）
   都不自己去读配置中心，参数由调用方传进来；传之前谁都得经过这里拿同一份缺省值，
   否则 API 进程用一套默认、rag worker 用另一套默认，同一个库的切片形态会莫名其妙地飘。
2. **写前归一、读后也归一**（SPEC §13.2 的另一面）：页面能填就能填错。
   落库前只认白名单键、越界夹到区间；库里可能是 NULL 老行或用户手写的残缺配置，
   读出来再归一一次补齐缺键——下游（引擎/分块器/检索）拿到的永远是字段齐全的字典。
3. **量级参数不留配置口子**：分批粒度、并发、提示词全部只取
   common_constants.rag_constant 里的常量；除开放 API 的单次文件数外，代码里
   不再设任何人为的数据上限（预览字数、重排条数、抽取规模都已删掉）。
   要调就改常量并提交——同一个版本的代码
   在不同环境跑出不同的分块/向量形态，比参数不可调难查得多。

进程内装载方式
--------------
``bootstrap()`` 在 API 服务的启动钩子与 rag worker 的 bootstrap 里各调一次
（两处都已经在读 Nacos，不额外发起请求），只负责装 mineru/es 两段；上面那些量级参数
不依赖它，没装载也是同一套常量，单元测试与脚本里没有 Nacos 不会炸。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_file_parser.chunkers import ChunkConfig
from common.common_file_parser.entry import merge_parse_options
from common.common_log.log_init import log

# Nacos 里的段落名（与 sql/init_nacos.yaml 一致；本模块只认这两段后端地址）
SECTION_MINERU = "mineru"
SECTION_ES = "es"

# ==================== 内置提示词（唯一取值，SPEC §7.4/§9） ====================
DEFAULT_IMAGE_DESC_PROMPT = (
    "请用一到两句话客观描述这张图片：主体内容、包含的文字/图表信息、以及可用于检索的关键词。"
    "不要推测图中没有的信息。")
DEFAULT_MEDIA_SUMMARY_PROMPT = (
    "请总结这段音视频的内容：主题、关键事件或论点、出现的文字信息，"
    "输出三句以内的中文，用于后续按语义检索这条媒体。")
# 文档型知识库收到视频文件时的描述口径：与上面的「一条媒体摘要」不同目的——
# 这里要的是一段能当正文分块、能被检索引用的详细文字，三句话远远不够一段视频。
DEFAULT_VIDEO_DESC_PROMPT = (
    "请按时间顺序详述这段视频的内容：场景与人物、讲述或演示的要点、"
    "出现的字幕与画面文字、涉及的数字与名称。"
    "只写画面与声音里真实出现的信息，不要推测。")
# 图谱抽取模板（SPEC §7.6）：三个占位符按名字替掉，不用 str.format——
# 改这段常量时只要混进一个孤立大括号，format 就会抛 KeyError 把整次抽取带倒
DEFAULT_GRAPH_EXTRACT_PROMPT = (
    "你是知识图谱抽取助手。阅读下面的文档片段，抽取出实体与实体之间的关系。\n"
    "实体类型只能从这些里选：{entity_types}；归类不出的统一写成「其他」。\n"
    "宁缺毋滥——原文里没有的关系不要推测，出现了多少组关系就输出多少组。\n"
    "只输出 JSON，不要任何解释文字，结构严格为：\n"
    '{"entities": [{"name": "实体名", "type": "实体类型", '
    '"summary": "一句话说明", "chunk_indexes": [0]}],\n'
    ' "relations": [{"head": "头实体名", "tail": "尾实体名", '
    '"relation": "关系描述"}]}\n'
    "chunk_indexes 填该实体真正出现过的切片编号（与下面的 [切片N] 对齐）。\n\n"
    "文档片段：\n{content}")

# 已装载的配置（进程级；空字典表示还没 bootstrap 过，只影响 mineru/es 两段）
_SECTION: dict[str, Any] = {}


# ==================== 装载 ====================

def _flatten(config: Optional[dict]) -> dict:
    """取本模块关心的两段，合并成一个扁平快照（worker 传的是 arq_ragflow 那一份 yml）。"""
    if not isinstance(config, dict):
        return {}
    return {
        SECTION_MINERU: config.get(SECTION_MINERU) or {},
        SECTION_ES: config.get(SECTION_ES) or {},
    }


async def bootstrap(config: Optional[dict] = None) -> dict:
    """装载运行参数（幂等，可重复调用刷新）。

    :param config: 已读好的整份 yml；留空则自行向 Nacos 取 ``service_rag`` 段。
        worker 侧传的是 ``arq_ragflow`` 那一份——两段的 mineru/es 字段同源（见 init_nacos.yaml），
        不一致时以显式传入的为准，避免同一进程里读到两份口径。
    :return: 已装载的段落快照（调用方一般用不到，返回是为了给启动日志与自检用）
    """
    global _SECTION
    if config is None:
        config = await _pull_from_nacos()
    _SECTION = _flatten(config)
    log.info("rag settings loaded: mineru={} es_index={}",
             "on" if mineru_options()["base_url"] else "off", chunk_index())
    return _SECTION


async def _pull_from_nacos() -> dict:
    """没有显式传入时回落到配置中心（未初始化或读失败都只告警，让服务带着默认值起来）。"""
    try:
        from common.common_constants.constant import SERVICE_RAG
        from common.common_nacos.nacos_client import nacos_client

        return await nacos_client.get_config_content(SERVICE_RAG) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("rag settings 读取 Nacos 失败，本轮全部使用代码默认值: {}", e)
        return {}


def is_loaded() -> bool:
    """mineru/es 两段是否装载过（配置面板的 configLoaded，与量级参数无关）。"""
    return bool(_SECTION)


# ==================== 量级参数（只取 rag_constant，不再读配置中心） ============
#
# 这里只留「调用协议约束」与「默认展示规模」两类数值：一次请求塞多少条、并发几个、
# 一页默认画多少个点。人为的数据上限（预览截断字数、重排候选条数、抽取三元组条数、
# 单文档只抽前 N 片）已全部删掉——代码不再静默丢数据，要加限制自己回来加。

def _as_int(value: Any, low: int, high: int, default: int) -> int:
    """取整数并夹到 [low, high]；非数字/越界回落默认（填错不该让解析整批失败）。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return num if low <= num <= high else default


def _as_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if low <= num <= high else default


def embedding_batch_size() -> int:
    """一次送向量模型的切片数（太小打不满 HTTP 批量、太大单请求超时）。"""
    return RC.EMBEDDING_BATCH_SIZE


def embedding_concurrency() -> int:
    """批量向量化时的并发请求数（对上游私有化服务的压力上限）。"""
    return RC.EMBEDDING_CONCURRENCY


def graph_extract_batch_chunks() -> int:
    """一次大模型调用喂多少条切片（分批粒度，不是总片数封顶）。"""
    return RC.KG_EXTRACT_BATCH_CHUNKS


def graph_viz_default_limit() -> int:
    """图谱可视化默认返回节点数（页面没传 limit 时的展示规模）。"""
    return RC.KG_VIZ_DEFAULT_LIMIT


def open_api_max_files() -> int:
    """开放 API 单次批量上传的文件数上限（页面上传不受此限，但仍受 size 约束）。"""
    return RC.OPEN_API_MAX_FILES


def image_desc_prompt(parse_config: Optional[dict]) -> str:
    """图片描述提示词（SPEC §7.4）：库里填了用库里的，留空用内置默认。"""
    return str((parse_config or {}).get("image_desc_prompt") or "").strip() \
        or DEFAULT_IMAGE_DESC_PROMPT


def media_summary_prompt() -> str:
    """音视频综合描述提示词（SPEC §9-2）。"""
    return DEFAULT_MEDIA_SUMMARY_PROMPT


def video_desc_prompt(parse_config: Optional[dict]) -> str:
    """文档型库里视频文件的描述提示词：库里填了用库里的，留空用内置默认。

    不与 image_desc_prompt 合用一个键：图片要的是「一到两句客观描述」，
    视频要的是一段能当正文分块的详述，同一个提示词服务不了两边。
    """
    return str((parse_config or {}).get("video_desc_prompt") or "").strip() \
        or DEFAULT_VIDEO_DESC_PROMPT


def graph_extract_prompt() -> str:
    """图谱抽取提示词模板（SPEC §7.6）。"""
    return DEFAULT_GRAPH_EXTRACT_PROMPT


def build_extract_prompt(rows: Sequence[Sequence[Any]], *,
                         entity_types: Optional[Sequence[str]] = None) -> str:
    """拼一次图谱抽取的完整提示词。

    :param rows: ``[(chunk_index, content), ..]``——编号用切片在本文档内的 chunk_index，
        与 ES/Neo4j 里那个列同一个口径；换成批次内序号会让模型返回的 chunk_indexes
        指向错的切片，页面上就是「点实体跳到不相干的一段」。
    """
    body = "\n\n".join(f"[切片{int(idx)}] {str(text or '').strip()}"
                       for idx, text in rows if str(text or "").strip())
    return (graph_extract_prompt()
            .replace("{entity_types}", "、".join(entity_types or RC.KG_ENTITY_TYPES))
            .replace("{content}", body))


# ==================== 其它后端段落 ====================

def mineru_options() -> dict:
    """minerU 私有化服务配置（原样交给解析层的 engine_options，解析层不碰配置中心）。

    这里只补 code 侧默认（轮询间隔与超时上限），地址与密钥不给默认值：
    没部署就是没部署，硬造一个 base_url 只会让报错变成「连不上某个陌生地址」。
    """
    cfg = dict(_SECTION.get(SECTION_MINERU) or {})
    return {
        "base_url": str(cfg.get("base_url") or "").strip(),
        "api_key": str(cfg.get("api_key") or "").strip(),
        "poll_interval": _as_int(cfg.get("poll_interval"), 1, 60, 3),
        "timeout": _as_int(cfg.get("timeout"), 60, 7200,
                           int(RC.PARSE_OPTION_DEFAULTS["mineru_timeout"])),
        "path_prefix": str(cfg.get("path_prefix") or "").strip(),
        "backend": str(cfg.get("backend") or RC.PARSE_OPTION_DEFAULTS["mineru_backend"]),
    }


def mineru_configured() -> bool:
    """是否部署了 minerU（配置面板据此把 mineru 引擎置灰而不是让用户选了报错）。"""
    return bool(mineru_options()["base_url"])


def chunk_index() -> str:
    """ES 切片索引名：Nacos 的 es.index 给了才覆盖常量（多环境隔离用，默认单索引）。"""
    return str((_SECTION.get(SECTION_ES) or {}).get("index") or RC.RAG_CHUNK_INDEX)


# ==================== 类型与开关 ====================

def check_kb_type(kb_type: Optional[str]) -> str:
    """知识库类型白名单校验（未登记类型一律报错，绝不静默按 doc 处理）。

    类型决定解析链路、向量空间与图谱可用性，猜错类型的代价是整库切片作废，
    所以这里只有「合法」与「抛错」两种结果。
    """
    value = str(kb_type or "").strip().lower()
    if value not in RC.KB_TYPES_ALL:
        raise ValueError(f"不支持的知识库类型：{kb_type}"
                         f"（可选 {'/'.join(RC.KB_TYPES_ALL)}）")
    return value


def check_parser_engine(kb_type: str, parser_engine: Optional[str]) -> str:
    """解析引擎归一：只认白名单；非 doc 型不存在解析，恒回 native。

    未知引擎名抛错而不是回落默认：回落会让用户以为「minerU 精度提升了」，
    实际跑的却是 native，这类静默降级比报错更难排查。
    """
    if kb_type != RC.KB_TYPE_DOC:
        return RC.PARSE_ENGINE_NATIVE
    value = str(parser_engine or "").strip().lower()
    if not value:
        return RC.PARSE_ENGINE_NATIVE
    if value not in RC.PARSE_ENGINES_ALL:
        raise ValueError(f"不支持的解析引擎：{parser_engine}"
                         f"（可选 {'/'.join(RC.PARSE_ENGINES_ALL)}）")
    return value


def normalize_graph_enabled(kb_type: Optional[str], enable_graph: Any) -> int:
    """图谱开关归一为 0/1：只有 doc 型支持图谱（SPEC §6 表格），其余类型强制 0。"""
    if not RC.KB_TYPE_GRAPH_SUPPORTED.get(str(kb_type or "").strip().lower(), False):
        return 0
    return 1 if _as_bool(enable_graph) else 0


def _as_bool(value: Any) -> bool:
    """字符串布尔也认（前端 checkbox 与 curl 都会传 "true"/"1"）。"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ==================== 三份 JSON 配置归一 ====================

def normalize_parse_config(kb_type: Optional[str], parser_engine: Optional[str],
                           parse_config: Optional[dict]) -> tuple[str, dict]:
    """解析配置归一 → (实际引擎名, 完整配置字典)。

    只认 RC.PARSE_OPTION_DEFAULTS 的白名单键（多带的键直接丢），缺键与布尔串交给
    解析层的 ``merge_parse_options`` 补齐——两处用同一份默认值，不会出现
    「面板上写的默认和引擎实际用的默认不一样」。
    引擎名同时写进 ``engine`` 键：解析层在没显式传 engine 参数时按 options["engine"] 取，
    这样配置面板里看到的与解析时生效的是同一个值。
    """
    engine = check_parser_engine(str(kb_type or "").strip().lower(), parser_engine)
    raw = dict(parse_config or {})
    kept = {k: v for k, v in raw.items()
            if k in RC.PARSE_OPTION_DEFAULTS and v is not None}
    options = merge_parse_options(kept)
    # minerU 的单文档等待上限以部署配置为准：面板回显的数值必须与实际轮询超时一致，
    # 否则用户按面板上的 1800 等，服务却按另一份配置提前判超时。
    options["mineru_timeout"] = mineru_options()["timeout"]
    # 媒体转写模型 id 归一成非负整数：面板清空时前端会传 null（已被上面的
    # ``v is not None`` 过滤）或空串，留成字符串会让解析层拿它去查 tb_model 时类型不符。
    for key in ("audio_model_id", "video_model_id"):
        options[key] = _as_int(options.get(key), 0, 2 ** 31 - 1, 0)
    options["engine"] = engine
    if engine != RC.PARSE_ENGINE_MINERU:
        # 本进程不往解析层递 minerU 地址：选了 native/docling 却还带着 mineru 段，
        # 出问题时日志里会同时出现两个引擎的痕迹
        options.pop("mineru", None)
    return engine, options


def normalize_chunk_config(chunk_config: Optional[dict], *,
                           chunk_size: Optional[int] = None,
                           chunk_overlap: Optional[int] = None) -> dict:
    """分块配置归一（复用 ChunkConfig：缺键补默认、越界夹区间、未知策略回落 fixed）。

    库级配置不带 ext，所以「每页分块/Excel 分块」这类独占策略在此**不降级**——
    降级判定发生在按文档分块时（那时才知道文件格式）。
    扁平的 chunk_size/chunk_overlap 是面板快捷项，给了就覆盖分组配置里的同名键。
    """
    raw = dict(chunk_config or {})
    if chunk_size is not None:
        raw["chunk_size"] = chunk_size
    if chunk_overlap is not None:
        raw["chunk_overlap"] = chunk_overlap
    return ChunkConfig.from_dict({k: v for k, v in raw.items() if v is not None}).to_dict()


def normalize_retrieve_config(retrieve_config: Optional[dict], *,
                              top_k: Optional[int] = None,
                              score_threshold: Optional[float] = None) -> dict:
    """检索配置归一：白名单键 + 区间夹取 + 一致性修正。

    一致性修正只做一件：``recall_size`` 不小于 ``top_k``。融合前每路召回的候选数
    比最终返回数还小的话，重排与阈值过滤之后必然凑不满用户要的条数，
    而页面只会显示「结果比预期少」，看不出是配置自相矛盾。
    """
    cfg = dict(RC.RETRIEVE_DEFAULTS)
    for key, value in (retrieve_config or {}).items():
        if key in RC.RETRIEVE_DEFAULTS and value is not None:
            cfg[key] = value
    if top_k is not None:
        cfg["top_k"] = top_k
    if score_threshold is not None:
        cfg["score_threshold"] = score_threshold

    cfg["top_k"] = _as_int(cfg["top_k"], 1, 100, RC.RETRIEVE_DEFAULTS["top_k"])
    cfg["score_threshold"] = _as_float(cfg["score_threshold"], 0.0, 1.0,
                                       RC.RETRIEVE_DEFAULTS["score_threshold"])
    cfg["vector_similarity_weight"] = _as_float(
        cfg["vector_similarity_weight"], 0.0, 1.0,
        RC.RETRIEVE_DEFAULTS["vector_similarity_weight"])
    cfg["keyword_boost"] = _as_float(cfg["keyword_boost"], 0.0, 10.0,
                                     RC.RETRIEVE_DEFAULTS["keyword_boost"])
    cfg["recall_size"] = _as_int(cfg["recall_size"], 1, 500, RC.RETRIEVE_DEFAULTS["recall_size"])
    cfg["recall_size"] = max(cfg["recall_size"], cfg["top_k"])
    cfg["rerank"] = _as_bool(cfg["rerank"])
    return cfg


def runtime_snapshot() -> dict:
    """生效参数快照（配置面板与运维自检用；只读，不含任何密钥、也不代表可配）。"""
    return {
        "embedding_batch_size": embedding_batch_size(),
        "embedding_concurrency": embedding_concurrency(),
        "graph_extract_batch_chunks": graph_extract_batch_chunks(),
        "graph_viz_default_limit": graph_viz_default_limit(),
        "open_api_max_files": open_api_max_files(),
        "mineru_configured": mineru_configured(),
        "es_index": chunk_index(),
        "vector_dim": RC.RAG_VECTOR_DIM,
        "max_chunk_chars": RC.MAX_CHUNK_CHARS,
    }


__all__ = [
    "bootstrap", "is_loaded", "runtime_snapshot",
    "embedding_batch_size", "embedding_concurrency",
    "graph_extract_batch_chunks", "graph_viz_default_limit",
    "open_api_max_files", "image_desc_prompt", "media_summary_prompt",
    "video_desc_prompt",
    "graph_extract_prompt", "build_extract_prompt",
    "mineru_options", "mineru_configured", "chunk_index",
    "check_kb_type", "check_parser_engine", "normalize_graph_enabled",
    "normalize_parse_config", "normalize_chunk_config", "normalize_retrieve_config",
]
