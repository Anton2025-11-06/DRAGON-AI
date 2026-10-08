# -*- coding: utf-8 -*-
"""解析 + 分块的统一入口（common_file_parser 对业务侧只暴露这一层）。

完整流水线（service_rag 的解析服务按这个顺序调）：

    doc = await parse_document(raw, name, engine=kb.parser_engine,
                               parse_config=kb.parse_config, image_hook=hook)
    sidecar = render_sidecar(doc)            # 落存储，重跑分块不再重解析
    chunks = await chunk_parsed_document(doc, kb.chunk_config, parse_config=kb.parse_config)

三个入口函数的职责边界
----------------------
* parse_document：选引擎 → 解析 → 预处理（开关在这里生效）→ 图片传存储并把地址回填到
  正文原位（hook 不分开关地跑，开了「图片智能解析」才再补一段描述）；
* chunk_parsed_document / chunk_from_sidecar：只做分块，不碰引擎也不碰存储；
* render_sidecar / load_sidecar：解析产物的往返，带版本自检——结构变了旧产物一律作废。

拆得开的原因：改分块配置重跑（最常见操作）只需要 sidecar，几百页的 PDF 不必再解析一遍；
而扫描件走 minerU 一次要几分钟，重跑一次分块就重解析一次是不可接受的浪费。
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C
from common.common_file_parser.chunkers import (STRATEGIES_ALL, ChunkConfig,
                                                chunk_document, suggest_strategy)
from common.common_file_parser.engines import (EngineNotAvailable, ParseError,
                                               get_engine)
from common.common_file_parser.models import (Chunk, ImageRef, ParsedBlock,
                                              ParsedDocument)
from common.common_file_parser.utils.heading_utils import normalize_title
from common.common_file_parser.utils.text_utils import (drop_headers_footers,
                                                        drop_toc, normalize_space)
from common.common_log.log_init import log

# 图片增强钩子：async (image: ImageRef, doc: ParsedDocument) -> None
# 职责是「把字节传存储并把 object_key/url/description 填回 ImageRef」，实现方在 service_rag
ImageHook = Callable[[ImageRef, ParsedDocument], Awaitable[None]]

SIDECAR_HEADER_KEY = "__doc__"


def merge_parse_options(parse_config: Optional[dict]) -> dict:
    """解析配置归一：缺键补 SPEC §7.3 默认值，字符串布尔值也认（前端 checkbox 会传 "true"）。"""
    options = dict(RC.PARSE_OPTION_DEFAULTS)
    for key, value in (parse_config or {}).items():
        if value is None:
            continue
        options[key] = value
    for flag in ("remove_toc", "remove_header_footer", "image_understand",
                 "audio_understand", "video_understand", "table_as_text",
                 "keep_page_break"):
        options[flag] = _as_bool(options.get(flag))
    return options


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def preprocess(doc: ParsedDocument, parse_config: Optional[dict] = None,
               options: Optional[dict] = None) -> ParsedDocument:
    """预处理开关在这里落地（就地改 blocks）：目录、页眉页脚、表格形态、分页标记。

    放在引擎之外而不是每个引擎内部：三个引擎对「同一篇文档」必须产出同样的切片，
    否则用户换引擎时检索效果的变化无法归因（到底是版面分析变好了，还是清洗规则变了？）。
    """
    opts = options or merge_parse_options(parse_config)
    if not doc.blocks:
        return doc

    if opts.get("remove_header_footer"):
        kept, dropped = drop_headers_footers(doc.blocks)
        if dropped:
            doc.warnings.append(f"已移除 {dropped} 个疑似页眉页脚块")
        doc.blocks = kept
    if opts.get("remove_toc"):
        kept, dropped = drop_toc(doc.blocks)
        if dropped:
            doc.warnings.append(f"已移除 {dropped} 行目录")
        doc.blocks = kept
    if not opts.get("table_as_text", True):
        doc.blocks = [_table_as_kv(b) for b in doc.blocks]
    if opts.get("keep_page_break"):
        doc.blocks = _insert_page_marks(doc.blocks)

    doc.meta["blocks"] = len(doc.blocks)
    doc.meta["chars"] = doc.char_count()
    doc.meta["images"] = sum(1 for b in doc.blocks if b.is_media)
    return doc


def _table_as_kv(block: ParsedBlock) -> ParsedBlock:
    """表格块 → 「列名: 值」逐行文本（table_as_text=False 时的形态，不丢数据、只丢结构）。

    保留行结构而不做丢弃：关掉表格渲染的动机是「表格里的竖线干扰 BM25」，
    把表删掉会直接丢内容，用户真正想要的从来不是这个。
    """
    if block.type != C.BLOCK_TABLE:
        return block
    lines = [ln.strip() for ln in (block.text or "").split("\n") if ln.strip()]
    rows = [ln for ln in lines if ln.startswith("|")]
    if len(rows) < 2:
        return block
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip("|").split("|")]
    header = cells(rows[0])
    out: list[str] = []
    for line in rows[2:]:
        values = cells(line)
        pairs = [f"{h}={v}" for h, v in zip(header, values) if v]
        if pairs:
            out.append("；".join(pairs))
    if not out:
        return block
    block.text = normalize_space("\n".join(out))
    block.meta["table_as_kv"] = True
    return block


def _insert_page_marks(blocks: Sequence[ParsedBlock]) -> list[ParsedBlock]:
    """分页标记：每页第一个块之前插一个「第 N 页」的一级标题块。

    这样任何策略都会在页边界断开（标题分块把它当节边界，其它策略把它当普通文字带走），
    面包屑里也会多出「第 3 页」这一层——引用片段因此天然带页码出处。
    """
    out: list[ParsedBlock] = []
    seen: set[int] = set()
    for b in blocks:
        page = int(b.page or 0)
        if page > 0 and page not in seen:
            seen.add(page)
            out.append(ParsedBlock(type=C.BLOCK_TITLE, text=f"第 {page} 页", level=1,
                                   page=page, meta={"page_mark": True}))
        out.append(b)
    return out


async def apply_image_hook(doc: ParsedDocument, hook: Optional[ImageHook]) -> int:
    """把文档里的内嵌媒体交给业务钩子（传存储 + 回填地址，开了对应的增强才补描述），返回处理数。

    跑这一趟与三个「解析增强」开关无关：开关只决定要不要调大模型，
    而“把多媒体原件存起来并把地址回填到正文原位”是硬需求。
    字节在钩子成功后立即释放：一篇扫描式 PPT 可能有几百张原图，全留在内存里
    worker 会被撑爆，而后续只需要 object_key 与 description。
    """
    if hook is None:
        return 0
    count = 0
    for block in doc.blocks:
        ref = block.image
        if ref is None or (ref.data is None and not ref.url):
            continue        # 既没字节也没外链的图片引用没有可上传的东西
        if not ref.block_id:
            ref.block_id = block.block_id or f"img{count + 1}"
        await hook(ref, doc)
        ref.data = None
        count += 1
    if count:
        doc.meta["images_hooked"] = count
        log.info(f"image hook done: {count} images of {doc.name}")
    return count


async def parse_document(raw: bytes, filename: str, *,
                         engine: Optional[str] = None,
                         parse_config: Optional[dict] = None,
                         ext: str = "",
                         image_hook: Optional[ImageHook] = None,
                         engine_options: Optional[dict] = None) -> ParsedDocument:
    """统一解析入口：引擎路由（含 auto）→ 解析 → 预处理 → 图片增强。

    :param engine: 显式引擎名，留空取 parse_config["engine"]，再留空用 native
    :param engine_options: 引擎专有环境（如 ``{"mineru": {base_url, api_key, ...}}``），
           由调用方从 Nacos 配置段传入，解析层不自己去读配置中心
    :raises EngineNotAvailable: 引擎依赖缺失或未部署（配置问题，不是文档问题）
    :raises ParseError: 文档本身解析不了
    """
    options = merge_parse_options(parse_config)
    options.update(engine_options or {})
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = (ext or name.rsplit(".", 1)[-1] if "." in name else "").lower().lstrip(".")
    chosen = str(engine or options.get("engine") or options.get("parser_engine")
                 or RC.PARSE_ENGINE_NATIVE).strip().lower()
    options["image_understand"] = bool(options.get("image_understand"))

    engine_obj = get_engine(chosen, options=options)
    doc = await engine_obj.parse(raw, name, ext=ext)
    doc = preprocess(doc, options=options)
    if image_hook is not None:
        # 钩子不分开关地跑：内嵌媒体一律传存储并把地址回填到正文原位；
        # 三种媒体各自的 *_understand 开关只决定钩子内部要不要再调一次理解模型
        await apply_image_hook(doc, image_hook)
    elif doc.image_count:
        doc.warnings.append("解析层未收到媒体处理钩子，文档内多媒体只保留图注文字（无原位地址）")
    return doc


# ==================== sidecar 往返 ====================

def render_sidecar(doc: ParsedDocument) -> str:
    """解析产物 → jsonl 文本（首行是文档头，其后每行一个结构块）。

    按行存而不是整个 JSON：一篇大文档几万个块，整份 JSON 有几十 MB，
    按行存可以流式读、坏一行只丢一块，diff 与日志里也好比对。
    """
    head = {"version": C.SIDECAR_VERSION, "name": doc.name, "ext": doc.ext,
            "engine": doc.engine, "meta": doc.meta, "warnings": doc.warnings}
    lines = [json.dumps({SIDECAR_HEADER_KEY: head}, ensure_ascii=False)]
    for block in doc.blocks:
        lines.append(json.dumps(block.to_dict(), ensure_ascii=False))
    return "\n".join(lines)


def load_sidecar(text: str) -> ParsedDocument:
    """jsonl 文本 → ParsedDocument（版本不匹配抛 ParseError，调用方据此重解析）。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        raise ParseError("解析产物为空，需要重新解析")
    try:
        head_payload = json.loads(lines[0])
    except json.JSONDecodeError as e:
        raise ParseError(f"解析产物首行不是合法 JSON: {e}") from e
    head = head_payload.get(SIDECAR_HEADER_KEY) if isinstance(head_payload, dict) else None
    if not isinstance(head, dict):
        raise ParseError("解析产物缺少文档头，需要重新解析")
    probe = ParsedDocument()
    if not probe.require_version(head):
        raise ParseError(
            f"解析产物版本已过期（产物 {head.get('version')}，当前 {C.SIDECAR_VERSION}），"
            f"需要重新解析")
    blocks: list[ParsedBlock] = []
    broken = 0
    for line in lines[1:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            broken += 1
            continue
        if not isinstance(payload, dict) or SIDECAR_HEADER_KEY in payload:
            continue
        blocks.append(ParsedBlock.from_dict(payload))
    doc = ParsedDocument(name=str(head.get("name") or ""), ext=str(head.get("ext") or ""),
                         engine=str(head.get("engine") or RC.PARSE_ENGINE_NATIVE),
                         blocks=blocks, meta=dict(head.get("meta") or {}),
                         warnings=[str(w) for w in (head.get("warnings") or [])])
    if broken:
        doc.warnings.append(f"解析产物里有 {broken} 行损坏，已跳过")
    if not blocks:
        raise ParseError("解析产物没有可恢复的结构块，需要重新解析")
    return doc


def sidecar_bytes(doc: ParsedDocument) -> bytes:
    return render_sidecar(doc).encode("utf-8")


def sidecar_from_bytes(raw: bytes) -> ParsedDocument:
    from common.common_file_parser.utils.text_utils import decode_bytes

    return load_sidecar(decode_bytes(raw))


# ==================== 分块入口 ====================

async def chunk_parsed_document(doc: ParsedDocument, chunk_config: Optional[dict] = None,
                                *, parse_config: Optional[dict] = None,
                                embed_fn: Optional[Callable] = None,
                                warnings: Optional[list[str]] = None) -> list[Chunk]:
    """对已解析的文档分块（SPEC §7.5 七种策略由 chunk_config.strategy 路由）。"""
    cfg = ChunkConfig.from_dict(chunk_config, ext=doc.ext)
    if parse_config and _as_bool((parse_config or {}).get("keep_page_break")) \
            and cfg.strategy not in (RC.CHUNK_PAGE,):
        # 分页标记已被 preprocess 插进块里，这里只补一句说明（免得用户以为开关没生效）
        cfg.warnings.append("已按分页标记切分（预处理开关 keep_page_break）")
    return await chunk_document(doc, cfg, embed_fn=embed_fn, warnings=warnings)


async def chunk_from_sidecar(sidecar_text: str, chunk_config: Optional[dict] = None,
                             *, embed_fn: Optional[Callable] = None,
                             parse_config: Optional[dict] = None,
                             warnings: Optional[list[str]] = None) -> list[Chunk]:
    """从 sidecar 直接分块（改分块配置重跑时的主路径：不再调用任何解析引擎）。"""
    doc = load_sidecar(sidecar_text)
    return await chunk_parsed_document(doc, chunk_config, parse_config=parse_config,
                                       embed_fn=embed_fn, warnings=warnings)


def config_summary(parse_config: Optional[dict] = None,
                   chunk_config: Optional[dict] = None, *,
                   ext: str = "", engine: Optional[str] = None) -> dict:
    """配置面板的「当前生效配置」回显：归一结果 + 引擎可用性 + 建议策略（SPEC §11.1-3）。

    回显归一结果而不是用户填的原值：越界值被夹到哪儿、独占策略被降级成了什么，
    页面上必须看得见，否则「我明明设了 800，怎么切片还是 512」只能靠猜。
    """
    options = merge_parse_options(parse_config)
    cfg = ChunkConfig.from_dict(chunk_config, ext=ext)
    engines = {}
    for name in RC.PARSE_ENGINES_ALL:
        try:
            get_engine(name, options=options)
            engines[name] = {"available": True, "reason": ""}
        except (EngineNotAvailable, ParseError) as e:
            engines[name] = {"available": False, "reason": str(e)}
    return {
        "parse_config": options,
        "chunk_config": cfg.to_dict(),
        "chunk_warnings": cfg.warnings,
        "engines": engines,
        "engine_effective": str(engine or options.get("engine") or RC.PARSE_ENGINE_NATIVE),
        "strategies": STRATEGIES_ALL,
        "suggest_strategy": suggest_strategy(ext),
        "max_chunk_chars": cfg.hard_limit,
        "title_path_enabled": cfg.title_path,
    }


def doc_title(doc: ParsedDocument) -> str:
    """文档标题（列表页与图谱节点显示用）：第一个一级标题，退化成首块前若干字。"""
    for block in doc.blocks:
        if block.level == 1 and not block.meta.get("page_mark"):
            title = normalize_title(block.text)
            if title:
                return title
    for block in doc.blocks:
        title = normalize_title(block.body())
        if title:
            return title[:80]
    return doc.name
