# -*- coding: utf-8 -*-
"""解析层数据结构：ParsedBlock / ImageRef / ParsedDocument / Chunk。

为什么是 dataclass 而不是 Pydantic
----------------------------------
这些对象只在 worker 进程内部与 sidecar 文件之间流转，不进 HTTP 契约（对外契约在
service_rag/schemas/rag_schema.py）。dataclass 的构造与序列化开销小得多——一篇 500 页
文档切出几万个块，每块都过一次 Pydantic 校验是没必要的税。

sidecar 往返（render_sidecar / load_sidecar）要求 to_dict / from_dict 严格可逆：
解析一次的产物落存储，重试与重新分块都直接从 sidecar 读，不再重跑解析引擎。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C


@dataclass
class ImageRef:
    """文档内嵌媒体的引用（图片/音频/视频共用这一类）。

    字节只在解析过程中存在：image_hook 把它们传存储并把 object_key/url 回填到正文原位置。
    src 是正文里的那个引用串（同样不进 sidecar）：minerU 把图片数据单独放在 images 字典里，
    全靠这个路径才能把字节配回正文位置，对不上就只剩一个传不上去的空引用。

    类名里写的是 image，但它同样承载 docx/pptx 包里嵌的音频与视频（native 取 related part
    的字节时不看类型）：种类由 mime 派生（见 kind），不另开字段，也就不必升 sidecar 版本。
    """

    block_id: str = ""              # 所属媒体块的 id（同时是存储对象名的依据）
    page: int = 0                   # 所在页（0 表示无页概念，如 docx）
    alt: str = ""                   # 原始替代文本 / 图注
    width: int = 0
    height: int = 0
    mime: str = "image/png"
    object_key: str = ""            # 存储 fileName（上传后回填）
    url: str = ""                   # 带签名可直接打开的访问地址（检索时现签，不长期落库）
    description: str = ""           # 解析增强结果（对应媒体的 *_understand 开关开启时）
    src: str = ""                   # 正文里的原始引用串（仅内存态，不进 sidecar）
    data: Optional[bytes] = None    # 原始字节（仅内存态，不进 sidecar）

    @property
    def kind(self) -> str:
        """这条引用是图片、音频还是视频（按 mime 认，认不出按图片）。

        块类型体系里没有 audio/video 位（BLOCK_TYPES_ALL 被分块策略与 sidecar 共用），
        所以内嵌媒体的种类只能从 mime 读——mime 已经进 sidecar，往返不丢。
        """
        return RC.media_kind_of_mime(self.mime) or RC.MEDIA_KIND_IMAGE

    @property
    def ready(self) -> bool:
        """是否已经可以被切片引用（要么有存储对象，要么在内存里待上传）。"""
        return bool(self.object_key or self.data)

    def to_dict(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "page": self.page, "alt": self.alt,
                "width": self.width, "height": self.height, "mime": self.mime,
                "object_key": self.object_key, "url": self.url,
                "description": self.description}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> Optional["ImageRef"]:
        if not payload:
            return None
        return cls(block_id=str(payload.get("block_id") or ""),
                   page=int(payload.get("page") or 0), alt=str(payload.get("alt") or ""),
                   width=int(payload.get("width") or 0), height=int(payload.get("height") or 0),
                   mime=str(payload.get("mime") or "image/png"),
                   object_key=str(payload.get("object_key") or ""),
                   url=str(payload.get("url") or ""),
                   description=str(payload.get("description") or ""))

    def caption(self) -> str:
        """给切片用的媒体文字：描述优先，其次原始 alt，都没有就一句占位。

        占位句不能省：媒体块完全不落文字时，切片正文可能变成空串，
        空正文既进不了 BM25 也没有可展示的引用片段。
        占位词按媒体种类取：内嵌一段音频却写着「[图片]」，检索的人会被误导。
        """
        return (self.description.strip() or self.alt.strip()
                or RC.MEDIA_KIND_PLACEHOLDERS.get(self.kind,
                                                  RC.MEDIA_KIND_PLACEHOLDER_DEFAULT))


@dataclass
class ParsedBlock:
    """一个结构块：解析引擎把文档拆成的最小结构单元（段落/标题/表格/图片/图注）。"""

    block_id: str = ""
    type: str = C.BLOCK_TEXT
    text: str = ""
    level: int = 0                  # 标题层级（1~6），非标题为 0
    page: int = 0                   # 起始页（0 表示无页概念）
    page_end: int = 0               # 结束页（跨页表格/长段落用）
    sheet: str = ""                 # Excel 工作表名
    image: Optional[ImageRef] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_title(self) -> bool:
        return self.type == C.BLOCK_TITLE and self.level > 0

    @property
    def is_media(self) -> bool:
        """是否是承载图片的块（图片增强与上下文补齐的判定入口）。"""
        return self.type in (C.BLOCK_IMAGE, C.BLOCK_FIGURE) or self.image is not None

    def body(self) -> str:
        """可检索正文：图片块用「描述/图注」代替像素内容。"""
        if self.type == C.BLOCK_IMAGE and self.image is not None:
            return self.image.caption()
        return self.text or ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"block_id": self.block_id, "type": self.type,
                               "text": self.text, "level": self.level, "page": self.page,
                               "page_end": self.page_end, "sheet": self.sheet,
                               "meta": self.meta}
        if self.image is not None:
            out["image"] = self.image.to_dict()
        return out

    @classmethod
    def from_dict(cls, payload: dict) -> "ParsedBlock":
        return cls(block_id=str(payload.get("block_id") or ""),
                   type=str(payload.get("type") or C.BLOCK_TEXT),
                   text=str(payload.get("text") or ""), level=int(payload.get("level") or 0),
                   page=int(payload.get("page") or 0), page_end=int(payload.get("page_end") or 0),
                   sheet=str(payload.get("sheet") or ""),
                   image=ImageRef.from_dict(payload.get("image")),
                   meta=dict(payload.get("meta") or {}))


@dataclass
class ParsedDocument:
    """一篇文档的解析结果。"""

    name: str = ""                       # 原始文件名
    ext: str = ""                        # 小写扩展名（不含点）
    engine: str = C.ENGINE_NATIVE        # 实际使用的引擎（auto 解析后回填真值）
    blocks: list[ParsedBlock] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)   # pages/images/chars/scanned
    warnings: list[str] = field(default_factory=list)    # 解析过程中的降级说明（页面可见）

    @property
    def page_count(self) -> int:
        return int(self.meta.get("pages") or 0)

    @property
    def image_count(self) -> int:
        return sum(1 for b in self.blocks if b.is_media)

    def plain_text(self, *, sep: str = "\n") -> str:
        """全部正文拼一节文本（预处理与调试用；分块不走这里）。"""
        return sep.join(b.body() for b in self.blocks if b.body())

    def char_count(self) -> int:
        return sum(len(b.body()) for b in self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {"version": C.SIDECAR_VERSION, "name": self.name, "ext": self.ext,
                "engine": self.engine, "meta": self.meta, "warnings": self.warnings,
                "blocks": [b.to_dict() for b in self.blocks]}

    @classmethod
    def from_dict(cls, payload: dict) -> "ParsedDocument":
        blocks = [ParsedBlock.from_dict(b) for b in (payload.get("blocks") or [])
                  if isinstance(b, dict)]
        return cls(name=str(payload.get("name") or ""), ext=str(payload.get("ext") or ""),
                   engine=str(payload.get("engine") or C.ENGINE_NATIVE), blocks=blocks,
                   meta=dict(payload.get("meta") or {}),
                   warnings=[str(w) for w in (payload.get("warnings") or [])])

    def require_version(self, payload: dict) -> bool:
        """sidecar 版本自检：结构变更（version 不匹配）时旧产物一律作废重解析。"""
        return int(payload.get("version") or 0) == C.SIDECAR_VERSION


@dataclass
class Chunk:
    """一个待向量化的切片（业务侧写 MySQL/ES 的行就来自它）。"""

    index: int = 0                       # chunk_index（同一文档内连续、从 0 开始）
    content: str = ""                    # 展示正文（不含面包屑前缀）
    embed_text: str = ""                 # 送向量模型的文本（含 title_path 前缀）
    chunk_type: str = RC.CHUNK_TYPE_TEXT
    title_path: str = ""                 # 面包屑：一级 > 二级 > 三级
    page: int = 0
    page_end: int = 0
    sheet: str = ""
    block_ids: list[str] = field(default_factory=list)
    image: Optional[ImageRef] = None     # 图片块的引用（object_key/url/description）
    tokens: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        """向量文本：缺 embed_text 时回落到 content（手工构造 Chunk 的单测也能跑）。"""
        return self.embed_text or self.content

    def to_dict(self) -> dict[str, Any]:
        out = {"index": self.index, "content": self.content, "embed_text": self.embed_text,
               "chunk_type": self.chunk_type, "title_path": self.title_path,
               "page": self.page, "page_end": self.page_end, "sheet": self.sheet,
               "block_ids": self.block_ids, "tokens": self.tokens, "meta": self.meta}
        if self.image is not None:
            out["image"] = self.image.to_dict()
        return out

    @classmethod
    def from_dict(cls, payload: dict) -> "Chunk":
        return cls(index=int(payload.get("index") or 0), content=str(payload.get("content") or ""),
                   embed_text=str(payload.get("embed_text") or ""),
                   chunk_type=str(payload.get("chunk_type") or RC.CHUNK_TYPE_TEXT),
                   title_path=str(payload.get("title_path") or ""),
                   page=int(payload.get("page") or 0), page_end=int(payload.get("page_end") or 0),
                   sheet=str(payload.get("sheet") or ""),
                   block_ids=[str(b) for b in (payload.get("block_ids") or [])],
                   image=ImageRef.from_dict(payload.get("image")),
                   tokens=int(payload.get("tokens") or 0),
                   meta=dict(payload.get("meta") or {}))
