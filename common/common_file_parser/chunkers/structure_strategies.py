# -*- coding: utf-8 -*-
"""结构类分块策略：title（标题层级）/ page（每页）/ excel（表头）/ semantic（语义）。

这四种策略的输入都是「解析块的结构信息」（level / page / sheet / 句向量），
所以它们只在结构信息存在时才有意义——缺了就在策略内部降级到文本类策略，并把原因写进
cfg.warnings（chunk_document 会把它并进解析告警，页面能看到「为什么我的切片不是按页切的」）。
"""
from __future__ import annotations

import math
from typing import Awaitable, Callable, Optional, Sequence

from common.common_file_parser import constants as C
from common.common_file_parser.chunkers.config import ChunkConfig
from common.common_file_parser.chunkers.pieces import Piece, Unit, table_header, text_window
from common.common_file_parser.chunkers.text_strategies import (chunk_fixed,
                                                                 split_units)
from common.common_file_parser.utils.text_utils import split_sentences

# 语义分块的向量入口：async (一批文本) -> 一批等长向量（由 service_rag 侧注入模型调用）
EmbedFn = Callable[[Sequence[str]], Awaitable[list[list[float]]]]
# 一次送向量模型的最大句子数：句子级请求太碎，整篇一次请求又容易超模型批量上限
EMBED_BATCH = 32


def _ranges_by_key(units: Sequence[Unit], key) -> list[tuple[int, int]]:
    """按分组键（页码 / 工作表名）把连续单元划成区间（返回 [(start, end), ...]）。"""
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(units) + 1):
        if i == len(units) or key(units[i]) != key(units[start]):
            ranges.append((start, i))
            start = i
    return ranges


def _join(units: Sequence[Unit], a: int, b: int) -> str:
    return "\n".join(u.text for u in units[a:b] if u.text)


async def chunk_title(units: list[Unit], cfg: ChunkConfig, *,
                      embed_fn: Optional[EmbedFn] = None) -> list[Piece]:
    """标题层级分块：以 ≤title_level 的标题为界切段，过短的段向上合并、超长的段递归拆。

    「超长自动合并截断」在这里有两半：
    * 短段合并——一个三级标题下只有两行字，单独成块没有语义，并进上一段；
    * 超长截断——一章几千字没有小标题时按分隔符优先级继续切（不是硬截），保证句子完整。
    """
    level = cfg.title_level
    # 1. 划出以标题为界的段（每次遇到"够高的标题"就开一段）
    sections: list[tuple[int, int]] = []
    start = 0
    for i, u in enumerate(units):
        if i and 0 < u.block.level <= level:
            sections.append((start, i))
            start = i
    sections.append((start, len(units)))
    sections = [(a, b) for a, b in sections if b > a]
    if len(sections) <= 1 and not any(u.block.level > 0 for u in units):
        cfg.warnings.append("文档里没有可用标题，已按分隔符优先级逐层拆分")
        return await split_units(units, cfg)

    # 2. 过短的段并入前一段（含图片的段不并：图片要能被单独召回）
    merged: list[tuple[int, int]] = []
    for a, b in sections:
        text = _join(units, a, b)
        prev = merged[-1] if merged else None
        if prev and len(text) < cfg.min_chars and C.BLOCK_IMAGE not in \
                {u.kind for u in units[a:b]} and len(_join(units, prev[0], b)) <= cfg.size:
            merged[-1] = (prev[0], b)
            continue
        merged.append((a, b))

    # 3. 超长的段递归拆（切片仍归在本段的单元区间里）
    pieces: list[Piece] = []
    for a, b in merged:
        if len(_join(units, a, b)) <= cfg.size:
            pieces.append(Piece(start=a, end=b))
            continue
        for piece in await split_units(units[a:b], cfg):
            pieces.append(Piece(start=a + piece.start, end=a + piece.end, parts=piece.parts))
    return pieces


async def chunk_page(units: list[Unit], cfg: ChunkConfig, *,
                     embed_fn: Optional[EmbedFn] = None) -> list[Piece]:
    """每页单独分块（PDF/PPT 专属）：一页一块，超目标长度自动拆分（SPEC §7.5）。"""
    if not any(u.block.page > 0 for u in units):
        cfg.warnings.append("文档没有页码信息（引擎未给出 page），已按标题层级分块")
        return await chunk_title(units, cfg, embed_fn=embed_fn)
    pieces: list[Piece] = []
    for a, b in _ranges_by_key(units, lambda u: u.block.page):
        text = _join(units, a, b)
        if not text.strip():
            continue
        if len(text) <= cfg.size:
            pieces.append(Piece(start=a, end=b))
            continue
        # 一页超长（文字密集的报告页）：按页内文本滑窗切，页码与单元区间保持不变
        for slot in text_window(text, cfg.size, cfg.overlap):
            pieces.append(Piece(start=a, end=b, parts=[slot]))
    return pieces


async def chunk_excel(units: list[Unit], cfg: ChunkConfig, *,
                      embed_fn: Optional[EmbedFn] = None) -> list[Piece]:
    """Excel 专属分块：每一块强制附带原始表头（SPEC §7.5），保证表格切片语义完整。

    keep_table_header=False 时只首块带表头（页面把这个开关关掉就是为了省字数，必须尊重）；
    表格前后的说明段落不是表格，按固定长度切。
    """
    pieces: list[Piece] = []
    text_ranges: list[tuple[int, int]] = []
    start: Optional[int] = None
    for i, u in enumerate(units):
        if u.kind != C.BLOCK_TABLE:
            if start is None:
                start = i
            continue
        if start is not None:
            text_ranges.append((start, i))
            start = None
        header, rows = table_header(u.text)
        if not rows:
            # D6：table_header 只认 markdown 的 `|...|` 行。table_as_text=False 时表格已被
            # preprocess 退成「列名=值；…」逐行文本（无竖线），这里拿不到 rows——旧写法把
            # 整块塞成一块、chunk_size 形同虚设（实测 300 却出 9758 字一块）。按行回退：
            # kv 文本每行自带列名，无需重贴表头，交给下面的累计逻辑逐行切；只有一行（或空）
            # 没什么可切的，保持整块。
            lines = [ln.strip() for ln in (u.text or "").split("\n") if ln.strip()]
            if len(lines) <= 1:
                pieces.append(Piece(start=i, end=i + 1))
                continue
            header, rows = "", lines
        size = max(200, cfg.size - len(header) - 1)
        chunk: list[str] = []
        first = True

        def flush() -> None:
            nonlocal chunk, first
            if not chunk:
                return
            body = "\n".join(chunk)
            if cfg.keep_table_header or first:
                body = f"{header}\n{body}" if header else body
            pieces.append(Piece(start=i, end=i + 1, parts=[body],
                                meta={"sheet": u.block.sheet, "rows": len(chunk),
                                      "continued": not first}))
            first = False
            chunk = []

        for row in rows:
            # D7：改「先判断后落笔」——现存行再加这一行会超 size 就先 flush，避免一块比
            # size 多出一整行。单行本身超长的不拆行（保留原文完整，交给 assemble 硬上限兜底）。
            if chunk and len("\n".join(chunk)) + len(row) + 1 > size:
                flush()
            chunk.append(row)
        flush()
    if start is not None:
        text_ranges.append((start, len(units)))
    for a, b in text_ranges:
        for piece in await chunk_fixed(units[a:b], cfg):
            pieces.append(Piece(start=a + piece.start, end=a + piece.end, parts=piece.parts))
    pieces.sort(key=lambda p: (p.start, p.end))
    return pieces


def _cosine(v1: Sequence[float], v2: Sequence[float]) -> float:
    """余弦相似度（语义分块的断开判据；长度不齐按短的算，避免越界）。"""
    n = min(len(v1), len(v2))
    if n == 0:
        return 0.0
    dot = sum(v1[i] * v2[i] for i in range(n))
    n1 = math.sqrt(sum(v1[i] * v1[i] for i in range(n)))
    n2 = math.sqrt(sum(v2[i] * v2[i] for i in range(n)))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


async def chunk_semantic(units: list[Unit], cfg: ChunkConfig, *,
                         embed_fn: Optional[EmbedFn] = None) -> list[Piece]:
    """语义分块：相邻句相似度低于阈值即断开（SPEC §7.5「基于句子语义相似度聚类」）。

    没给向量入口（知识库没配向量模型）或向量调用失败时**降级为固定长度分块并留警告**，
    而不是让整篇文档解析失败：语义分块是质量增强，不是必要条件。
    """
    if embed_fn is None:
        cfg.warnings.append("语义分块需要向量模型（embed_fn 未提供），已按固定长度分块")
        return await chunk_fixed(units, cfg)

    sentences: list[tuple[int, str]] = []       # (所属单元下标, 句子)
    for i, u in enumerate(units):
        parts = [u.text] if u.kind == C.BLOCK_IMAGE else (split_sentences(u.text) or [u.text])
        for one in parts:
            if one and one.strip():
                sentences.append((i, one.strip()))
    if not sentences:
        return []

    try:
        vectors: list[list[float]] = []
        for i in range(0, len(sentences), EMBED_BATCH):
            batch = [s for _, s in sentences[i:i + EMBED_BATCH]]
            got = await embed_fn(batch)
            if not got or len(got) != len(batch):
                raise ValueError("向量返回数量与请求条数不一致")
            vectors.extend(got)
    except Exception as e:                      # noqa: BLE001 - 模型不可用不能阻断整篇解析
        cfg.warnings.append(f"语义分块的向量调用失败（{e}），已按固定长度分块")
        return await chunk_fixed(units, cfg)

    pieces: list[Piece] = []
    buf: list[str] = []
    buf_units: list[int] = []
    size = cfg.size
    threshold = cfg.semantic_threshold

    def flush() -> None:
        text = "\n".join(p for p in buf if p)
        if text.strip() and buf_units:
            pieces.append(Piece(start=buf_units[0], end=buf_units[-1] + 1, parts=[text]))
        buf.clear()
        buf_units.clear()

    for idx, (unit_index, sentence) in enumerate(sentences):
        if buf and len("\n".join(buf)) + len(sentence) > size:
            flush()
        if buf and idx > 0 and _cosine(vectors[idx - 1], vectors[idx]) < threshold:
            flush()                              # 语义跳变：断开
        buf.append(sentence)
        buf_units.append(unit_index)
    flush()
    return pieces
