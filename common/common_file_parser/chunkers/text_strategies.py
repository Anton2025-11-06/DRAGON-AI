# -*- coding: utf-8 -*-
"""文本类分块策略：fixed / delimiter / regex，外加一个供标题分块复用的内部拆分器。

三种策略的共性：只决定「断开点」，产出 Piece（覆盖的单元区间 + 文本片段）；
截断上限、碎块合并、面包屑前缀、上下文补齐全部交给 pieces.assemble。

重叠（overlap）的实现说明
------------------------
重叠文字来自上一块的尾部，它不属于当前区间的单元，所以 Piece 的 block_ids 里**不含**这些
重叠来源块。这是刻意的：切片溯源必须指回它正文真正来自哪些块，把重叠带来的块也算进去，
图谱的 MENTIONED_IN 就会凭空多出一堆弱关联。
"""
from __future__ import annotations

import bisect
import re
from typing import Optional, Sequence

from common.common_file_parser import constants as C
from common.common_file_parser.chunkers.config import ChunkConfig
from common.common_file_parser.chunkers.pieces import Piece, Unit, text_window


def _emit(pieces: list[Piece], start: int, end: int, parts: Sequence[str]) -> None:
    """缓冲落一块（全空白就不落，避免出现 content="" 的切片）。"""
    text = "\n".join(p for p in parts if p and p.strip())
    if text.strip():
        pieces.append(Piece(start=start, end=end, parts=[text]))


def _full_text(units: Sequence[Unit]) -> tuple[str, list[int]]:
    """全文 + 每个单元的起始偏移（正则分块要把字符位置映射回单元区间）。"""
    offsets: list[int] = []
    pos = 0
    for u in units:
        offsets.append(pos)
        pos += len(u.text) + 1                 # +1 是 "\n".join 的分隔符
    return "\n".join(u.text for u in units), offsets


async def chunk_fixed(units: list[Unit], cfg: ChunkConfig, *,
                      embed_fn: Optional[object] = None) -> list[Piece]:
    """固定长度分块：按目标长度累积正文，超长块滑窗切，块间带重叠。

    重叠的存放约定：结算时把上一块尾部 overlap 个字直接当成新缓冲的第一个元素，之后所有
    _emit 都只交出 buf——carry 不再另拼一遍，否则每块开头会把同样的字复制两次。
    """
    size, overlap = cfg.size, cfg.overlap
    pieces: list[Piece] = []
    buf: list[str] = []
    buf_len = 0
    start = 0

    for i, u in enumerate(units):
        if u.kind == C.BLOCK_IMAGE:            # 图片永远单独成块（不能被文字淹没）
            _emit(pieces, start, i, buf)
            buf, buf_len, start = [], 0, i + 1
            pieces.append(Piece(start=i, end=i + 1))
            continue
        text = u.text
        if len(text) > size:                   # 单块超长：先结算缓冲，再滑窗切它
            _emit(pieces, start, i, buf)
            buf, buf_len = [], 0
            for slot in text_window(text, size, overlap):
                pieces.append(Piece(start=i, end=i + 1, parts=[slot]))
            start = i + 1
            continue
        if buf_len + len(text) + 1 > size and buf:
            carry = "\n".join(buf)[-overlap:] if overlap else ""
            _emit(pieces, start, i, buf)
            buf, buf_len, start = ([carry] if carry else []), len(carry), i
        buf.append(text)
        buf_len += len(text) + 1
    _emit(pieces, start, len(units), buf)
    return pieces


async def chunk_delimiter(units: list[Unit], cfg: ChunkConfig, *,
                          embed_fn: Optional[object] = None) -> list[Piece]:
    """自定义分隔符分块：按用户给的分隔符优先级切开，再按目标长度合并相邻段。

    超长段走滑窗而不是硬截，结算缓冲时还带走上一块尾部 `chunk_overlap` 个字——这两件都是
    原「LangChain 递归分块」独有的能力，两个策略合并后必须都留在这里，否则删策略就等于删能力。
    重叠文字不进 block_ids（理由见本文件顶说明），与 chunk_fixed 同一口径：结算时它已经被
    当成新缓冲的第一个元素放好了，不再另拼一遍。
    """
    seps = [s for s in cfg.separators if s]
    # 长的分隔符排前面：交替匹配是"先出现先赢"，"。"排在"。\n"前会把换行留给下一段
    pattern = "|".join(re.escape(s) for s in sorted(seps, key=len, reverse=True))
    if not pattern:
        return await chunk_fixed(units, cfg)
    size, overlap = cfg.size, cfg.overlap
    pieces: list[Piece] = []
    buf: list[str] = []
    buf_len = 0
    start = 0
    for i, u in enumerate(units):
        if u.kind == C.BLOCK_IMAGE:
            _emit(pieces, start, i, buf)
            buf, buf_len, start = [], 0, i + 1
            pieces.append(Piece(start=i, end=i + 1))
            continue
        segs = [s.strip() for s in re.split(pattern, u.text) if s and s.strip()]
        if not segs:
            continue
        for seg in segs:
            if len(seg) > size:              # 段本身超长：先结算缓冲，再滑窗切它
                _emit(pieces, start, i + 1, buf)
                buf, buf_len, start = [], 0, i + 1
                for slot in text_window(seg, size, overlap):
                    pieces.append(Piece(start=i, end=i + 1, parts=[slot]))
                continue
            if buf_len + len(seg) + 1 > size and buf:
                carry = "\n".join(buf)[-overlap:] if overlap else ""
                _emit(pieces, start, i + 1, buf)
                buf, buf_len, start = ([carry] if carry else []), len(carry), i + 1
            buf.append(seg)
            buf_len += len(seg) + 1
    _emit(pieces, start, len(units), buf)
    return pieces


def _merge_parts(parts: Sequence[str], sep: str, size: int, overlap: int) -> list[str]:
    """把切好的小段按目标长度重新粘起来（避免「一段一句话」的碎块）。"""
    out: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if buf and len(buf) + len(sep) + len(part) > size:
            out.append(buf)
            buf = (buf[-overlap:] + sep + part) if overlap and sep else part
        else:
            buf = f"{buf}{sep}{part}" if buf else part
    if buf.strip():
        out.append(buf)
    return out


def _recursive_split(text: str, separators: Sequence[str], size: int, overlap: int) -> list[str]:
    """内置递归字符分块：按分隔符优先级逐层下钻，直到每段都不超目标长度。

    先用最外层分隔符（段落），切到位就直接粘回来；
    切不动（还超长）才降级到句子、再降级到字符——顺序由 ChunkConfig.separators 决定。
    """
    text = (text or "").strip()
    if not text:
        return []
    seps = [s for s in separators if s] or ["\n\n", "\n", "。", ".", " "]
    for depth, sep in enumerate(seps):
        parts = [p.strip() for p in text.split(sep) if p and p.strip()]
        if len(parts) <= 1:
            continue
        if all(len(p) <= size for p in parts):
            return _merge_parts(parts, sep, size, overlap)
        out: list[str] = []
        for part in parts:
            if len(part) <= size:
                out.append(part)
                continue
            deeper = _recursive_split(part, seps[depth + 1:], size, overlap)
            out.extend(deeper or [part])
        return _merge_parts(out, "", size, overlap) if sep == "\n\n" else out
    return text_window(text, size, overlap)


async def split_units(units: list[Unit], cfg: ChunkConfig) -> list[Piece]:
    """逐单元按分隔符优先级拆超长块（**不是用户可选策略**，只给「标题层级分块」复用）。

    为什么不把它当一个策略开放给用户：它的断开点与 chunk_delimiter 完全同源（分隔符优先级 +
    按目标长度合并），而 overlap 也已经并给 delimiter——并存只会在下拉里多出一个选不出差别的选项。

    按「每个解析块」单独切而不是整篇切：整篇切会把段落与页码/标题的对齐关系打散，
    切片就没有 page_num 与 title_path，检索结果无法给出处。
    """
    pieces: list[Piece] = []
    for i, u in enumerate(units):
        if len(u.text) <= cfg.size:
            pieces.append(Piece(start=i, end=i + 1))
            continue
        slots = _recursive_split(u.text, cfg.separators, cfg.size, cfg.overlap) \
            or text_window(u.text, cfg.size, cfg.overlap)
        for slot in slots:
            pieces.append(Piece(start=i, end=i + 1, parts=[slot]))
    return pieces


async def chunk_regex(units: list[Unit], cfg: ChunkConfig, *,
                      embed_fn: Optional[object] = None) -> list[Piece]:
    """正则分块：用户规则命中处即章节/条款起点（SPEC §7.5「精准匹配章节/条款切割」）。

    在「全文」上找命中位置再映射回单元区间——用户的正则通常跨块（"第\\s*\\d+\\s*条"
    出现在某段开头，但它要切走的是它之前的整段内容）。正则写错不报错，回落固定长度并留警告。
    """
    pattern = cfg.regex_pattern or ""
    try:
        rx = re.compile(pattern, re.M) if pattern else None
    except re.error:
        rx = None
    if rx is None:
        return await chunk_fixed(units, cfg)

    full, offsets = _full_text(units)
    bounds = [0] + [m.start() for m in rx.finditer(full) if m.start() > 0] + [len(full)]
    pieces: list[Piece] = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        slot = full[a:b].strip()
        if not slot:
            continue
        i0 = max(0, min(bisect.bisect_right(offsets, a) - 1, len(units) - 1))
        i1 = max(i0 + 1, min(bisect.bisect_right(offsets, b - 1), len(units)))
        if len(slot) > cfg.size:
            for sub in text_window(slot, cfg.size, cfg.overlap):
                pieces.append(Piece(start=i0, end=i1, parts=[sub]))
            continue
        pieces.append(Piece(start=i0, end=i1, parts=[slot]))
    return pieces
