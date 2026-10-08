# -*- coding: utf-8 -*-
"""解析引擎基类：引擎名/依赖自检/CPU 让路/产物收口 四件事都在这里。

三条硬规则
----------
1. **解析是 CPU 密集活，绝不占事件循环**。native/docling 全部在 thread_pool 里跑
   （worker 与 API 共用同一个进程内线程池），minerU 是纯 HTTP 等待，天然异步。
   SPEC §12 明确 ragflow 队列纯 CPU：单文档解析几秒到几十秒，直接 await 会把同进程
   的其它协程（进度回写、心跳）全部拖住。
2. **「依赖没有」和「文档坏了」是两种错**。前者抛 EngineNotAvailable（配置/部署问题，
   换引擎或补部署就能好，文档本身没动过），后者抛 ParseError（文档进 FAILED，页面
   显示 error_msg）。混成一个异常，页面就只能告诉用户「解析失败」，运维无从下手。
3. **引擎只产结构块，不做分块、不做清洗**。页眉页脚/目录/标题层级属于「预处理」，
   受知识库配置开关控制，且对三个引擎应当表现一致——所以统一放在 finalize/preprocess
   里做，任何引擎私自做都会导致「换个引擎切片内容变了」。
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C
from common.common_file_parser.models import ParsedBlock, ParsedDocument
from common.common_threadpool.pool import thread_pool

# 引擎专有参数的缺省值：直接转引 rag_constant，页面配置与解析代码共用一份默认
RC_DEFAULTS: dict[str, Any] = dict(RC.PARSE_OPTION_DEFAULTS)


class ParseError(Exception):
    """文档解析失败：格式损坏、内容为空、引擎返回读不懂的东西。"""


class EngineNotAvailable(Exception):
    """引擎不可用：pip 依赖缺失（docling）或私有化服务未配置（minerU 的 base_url）。"""


def normalize_ext(filename: str, ext: str = "") -> str:
    """文件名/显式扩展名 → 小写无点扩展名（显式 ext 优先，上传接口已按知识库类型校验过）。"""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name and not ext:
        return ""
    return str(ext or name.rsplit(".", 1)[-1]).lower().lstrip(".")


def guard_legacy_office(ext: str) -> None:
    """老版 Office 二进制格式（.doc/.ppt）的统一拦截：三个引擎只留一份文案。

    python-docx / python-pptx 与 docling 都只认 Office Open XML（zip 包），读不动 OLE2
    复合文档。原本只有 native 拦了，docling 漏拦：它的 formats 并了 EXT_WORD（含 doc），
    于是 .doc 一路放行进 docling，最后从它内部冒出一句
    ``An unexpected error occurred while opening the document xxx.doc`` ——
    用户看不出那是格式问题，也不知道该改哪一处（文档没坏，是格式读不了）。

    拦截必须在「引擎不支持该格式」那道闸门**之前**：只有这句带得上「转成什么」。
    """
    key = normalize_ext("", ext)
    if key in C.LEGACY_OFFICE:
        raise ParseError(
            f".{key} 是老版 Office 二进制格式，native/docling 都读不了；"
            f"请另存为 .{'docx' if key == 'doc' else 'pptx'}，"
            f"或把解析引擎换成 minerU（这一类能不能吃取决于部署的 minerU 版本）")


def mime_of(ext: str) -> str:
    """图片扩展名 → MIME（内嵌图片上传存储时的 content_type）。"""
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "webp": "image/webp", "bmp": "image/bmp", "gif": "image/gif",
    }.get((ext or "").lower().lstrip("."), "application/octet-stream")


class BaseEngine(ABC):
    """解析引擎抽象：子类只需实现 ``_parse``，其余（依赖自检、线程让路、产物收口）复用基类。"""

    name: str = C.ENGINE_NATIVE
    # 该引擎能吃的扩展名；空集合表示「全部格式自己判断」（auto 引擎）
    formats: set[str] = set()

    def __init__(self, options: Optional[dict] = None) -> None:
        self.options: dict[str, Any] = dict(options or {})

    # ---------- 子类实现 ----------

    async def parse(self, raw: bytes, filename: str, *, ext: str = "") -> ParsedDocument:
        """字节 → ParsedDocument（子类实现，入参已经过 supports 校验）。"""
        doc = await self._parse(raw or b"", filename or "", ext or normalize_ext(filename))
        return self.finalize(doc)

    @abstractmethod
    async def _parse(self, raw: bytes, filename: str, ext: str) -> ParsedDocument:
        """子类实现：原生解析并返回未收尾的 ParsedDocument（四个内置引擎都实现了）"""
        raise NotImplementedError

    # ---------- 通用能力 ----------

    def supports(self, ext: str) -> bool:
        return not self.formats or normalize_ext("", ext) in self.formats

    def ensure_available(self) -> None:
        """依赖自检（默认无外部依赖）；子类缺依赖时重写并抛 EngineNotAvailable。"""

    @property
    def image_understand(self) -> bool:
        """「图片智能解析」开关（SPEC §7.4）：只决定要不要调大模型补描述。

        图片提取与上传存储不再受这一位控制——需求要求正文里的图片一律得把地址回填到原位。
        """
        return bool(self.options.get("image_understand", False))

    def option(self, key: str, default: Any = None) -> Any:
        """取引擎专有参数（缺失回落 SPEC §7.3 的默认值，不回落成「代码里的另一个默认值」）。"""
        value = self.options.get(key)
        if value in (None, ""):
            return RC_DEFAULTS.get(key, default)
        return value

    @staticmethod
    async def run_cpu(fn: Callable[..., Any], *args: Any,
                      executor: Optional[ThreadPoolExecutor] = None, **kwargs: Any) -> Any:
        """把同步/阻塞的解析动作丢进线程池（CPU 密集解析不许在事件循环里跑）。"""
        loop = asyncio.get_running_loop()
        call: Callable[[], Any] = lambda: fn(*args, **kwargs)  # noqa: E731
        return await loop.run_in_executor(executor or thread_pool, call)

    def new_document(self, filename: str, ext: str) -> ParsedDocument:
        return ParsedDocument(name=(filename or "").rsplit("/", 1)[-1], ext=ext,
                              engine=self.name)

    def finalize(self, doc: ParsedDocument) -> ParsedDocument:
        """产物收口：补 block_id、规范页码、标注标题层级、汇总 meta（所有引擎口径一致）。"""
        from common.common_file_parser.utils.heading_utils import mark_title_blocks

        blocks: list[ParsedBlock] = []
        for idx, b in enumerate(doc.blocks or []):
            ref = b.image
            if ref is not None and not ref.data and not ref.url:
                # 图片引用配不到字节也配不到地址（docling 导出的本地路径、md 里的相对引用）：
                # 留着一个既传不上去又显示不出来的块，只会产出一句 [图片] 空切片
                alt = (ref.alt or "").strip()
                if not alt:
                    continue
                b = ParsedBlock(type=C.BLOCK_FIGURE, text=alt, page=b.page, page_end=b.page_end)
            if not (b.body() or "").strip() and b.image is None:
                continue        # 空块进分块只会产出空切片
            b.block_id = b.block_id or f"b{idx + 1}"
            b.page = max(0, int(b.page or 0))
            b.page_end = max(b.page, int(b.page_end or 0))
            if b.image is not None and not b.image.block_id:
                b.image.block_id = b.block_id
            blocks.append(b)
        doc.blocks = blocks
        mark_title_blocks(blocks)
        pages = {b.page for b in blocks if b.page > 0}
        doc.meta.setdefault("pages", max(pages) if pages else 0)
        doc.meta.setdefault("blocks", len(blocks))
        doc.meta.setdefault("images", sum(1 for b in blocks if b.is_media))
        doc.meta.setdefault("chars", doc.char_count())
        if self.options.get("engine_requested"):
            doc.meta.setdefault("engine_requested", str(self.options["engine_requested"]))
        return doc


def join_text(parts: Sequence[str], *, sep: str = "\n") -> str:
    """把若干段文字拼成一块（过滤空白段，避免解析出「只有换行」的块）。"""
    return sep.join(p.strip() for p in parts if p and p.strip())


# ==================== 引擎注册表（延迟 import，避开 auto ↔ 具体引擎的循环） ====================

_ENGINE_PATHS: dict[str, tuple[str, str]] = {
    C.ENGINE_NATIVE: ("common.common_file_parser.engines.native", "NativeEngine"),
    C.ENGINE_DOCLING: ("common.common_file_parser.engines.docling_engine", "DoclingEngine"),
    C.ENGINE_MINERU: ("common.common_file_parser.engines.mineru", "MineruEngine"),
    C.ENGINE_AUTO: ("common.common_file_parser.engines.auto", "AutoEngine"),
}


def build_engine(name: Optional[str], options: Optional[dict] = None) -> BaseEngine:
    """引擎名 → 实例（未登记的名称直接报错，绝不静默退回默认引擎）。

    静默退回是最坏的行为：用户选了 docling 却因为依赖没装跑成 native，切片质量下降却
    页面上看不出来。缺依赖由 ``BaseEngine.ensure_available`` 抛 EngineNotAvailable。
    """
    key = (name or C.ENGINE_NATIVE).strip().lower()
    path = _ENGINE_PATHS.get(key)
    if path is None:
        raise ParseError(f"未知的解析引擎: {name}（可选 {sorted(_ENGINE_PATHS)}）")
    import importlib

    module = importlib.import_module(path[0])
    return getattr(module, path[1])(options)


def engine_cls(name: str) -> type[BaseEngine]:
    """引擎名 → 类（页面展示支持格式、单测构造实例时用）。"""
    path = _ENGINE_PATHS.get((name or C.ENGINE_NATIVE).strip().lower())
    if path is None:
        raise ParseError(f"未知的解析引擎: {name}")
    import importlib

    return getattr(importlib.import_module(path[0]), path[1])
