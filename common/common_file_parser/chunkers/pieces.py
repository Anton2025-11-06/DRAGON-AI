# -*- coding: utf-8 -*-
"""分块的公共机器：Unit/Piece 两个中间结构 + 收尾装配（合并碎块、硬截上限、上下文补齐）。

为什么中间要隔一层 Piece
------------------------
八种策略的差别只在「在哪儿断开」，其余事情完全一样：块超长要截断、碎块要合并、图片与表格要
补上下文、切片要编号、面包屑要拼进向量文本。把后半段收在 `assemble` 里，策略代码就只剩
「产出断开点」，八份实现不会跑出八种收尾行为（页面配置项的语义也就还能叫一致）。

Piece 只记「单元区间 + 文本片段」：单元区间来自解析块的顺序，是上下文补齐与图谱溯源的坐标；
文本片段允许是区间内某个长块的切片（窗口分块的产物），所以两者要分开存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C
from common.common_file_parser.chunkers.config import ChunkConfig
from common.common_file_parser.models import Chunk, ParsedBlock
from common.common_file_parser.utils.heading_utils import title_paths
from common.common_file_parser.utils.token_utils import count_tokens

# 句子优先、其次空白：截断时尽量落在边界上（把一个词切成 "还款方"/"式" 会同时伤 BM25 和向量）
_CUT_MARKS = ("。", "！", "？", "；", ". ", "! ", "? ", "; ", "\n", " ", "")
# 向后找边界最多外扩多少：超了就放弃边界直接硬切（_cut_at 的向前分支只会往回，不会越界）
_CUT_AHEAD = 40
# split_to_limit 要靠它预留出这段余量，否则「硬上限」会被句末外扩撞超（实测 20013 > 20000）
_CUT_SLACK = _CUT_AHEAD + max(len(m) for m in _CUT_MARKS)


@dataclass
class Unit:
    """一个解析块在分块视角下的样子（文本 + 它所属的面包屑 + 结构类别）。"""

    text: str
    block: ParsedBlock
    path: str = ""
    index: int = 0

    @property
    def kind(self) -> str:
        return self.block.type

    @property
    def is_text(self) -> bool:
        """是否能当「上下文」给别人补（孤立表格/图片补的就是它）。"""
        return self.block.type in (C.BLOCK_TEXT, C.BLOCK_TITLE, C.BLOCK_FIGURE)


@dataclass
class Piece:
    """一块切片的雏形：覆盖了哪些单元、由哪些文本片段组成。"""

    start: int
    end: int
    parts: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def covered(self, units: Sequence[Unit]) -> list[tuple[int, Unit]]:
        """本块覆盖到的单元（下标 + 内容）。

        端点越界一律夹回区间内：策略在末尾会把 start 推到 len(units)（最后一个缓冲块的
        起点记在下一个单元上），这里兜住，不让组装阶段抛 IndexError。
        """
        if not units:
            return []
        start = min(max(0, int(self.start)), len(units) - 1)
        end = max(start + 1, min(int(self.end), len(units)))
        return [(i, units[i]) for i in range(start, end)]

    def text(self, units: Sequence[Unit]) -> str:
        if self.parts:
            return "\n".join(p for p in self.parts if p)
        return "\n".join(u.text for _, u in self.covered(units) if u.text)


def _unit_text(block: ParsedBlock) -> str:
    """单元的正文：媒体块在图注/描述后面补上它在正文原位置的**访问地址**。

    「我今天画了个画[图片]，挺不错的」这类切片只留一句「[图片]」：media_url 是检索
    出口自己用的字段，问答上下文、工作流、导出拿到的都是正文这一份，正文里没地址就等于
    这些下游全都看不到图。

    地址只在这一层拼，不进 `ParsedBlock.body()`：body() 还被 doc_title 兜底与 plain_text
    用着，那些场合要的是纯文字（标题里冒出 URL 是灾难），而分块才是「写进数据」的那一步。
    """
    text = (block.body() or "").strip()
    if block.type != C.BLOCK_IMAGE or block.image is None:
        return text
    return " ".join(x for x in (text, block.image.address()) if x)


def build_units(blocks: Sequence[ParsedBlock]) -> list[Unit]:
    """解析块 → 分块单元（顺带算好每块的面包屑路径，标题层级在此被用起来）。"""
    blocks = list(blocks or [])
    paths = title_paths(blocks)
    units: list[Unit] = []
    for i, b in enumerate(blocks):
        text = _unit_text(b)
        if not text and b.image is None:
            continue
        units.append(Unit(text=text, block=b, path=paths[i] if i < len(paths) else "",
                          index=len(units)))
    return units


def _cut_at(text: str, target: int) -> int:
    """在 target 附近找最近的句子/词边界（向后找，找不到就硬切）。"""
    if target <= 0:
        return 0
    if target >= len(text):
        return len(text)
    for mark in _CUT_MARKS:
        if not mark:
            break
        pos = text.find(mark, target)
        if pos != -1 and pos - target <= _CUT_AHEAD:
            return pos + len(mark)
    # 往后找不到边界就往前找（往前至少要有半截文本，否则退回硬切）
    for mark in _CUT_MARKS:
        if not mark:
            continue
        pos = text.rfind(mark, max(0, target - 200), target)
        if pos != -1 and pos > target // 2:
            return pos + len(mark)
    return target


def text_window(text: str, size: int, overlap: int = 0) -> list[str]:
    """长文本按「目标长度 + 重叠」滑窗切开，切点尽量落在句子边界。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    size = max(1, int(size))
    overlap = max(0, min(int(overlap), size - 1))
    out: list[str] = []
    pos = 0
    total = len(text)
    while pos < total:
        cut = _cut_at(text, min(total, pos + size))
        if cut <= pos:                        # 边界函数没往前推进（极端文本）：强制推进
            cut = min(total, pos + size)
        piece = text[pos:cut].strip()
        if piece:
            out.append(piece)
        if cut >= total:
            break
        pos = max(cut - overlap, pos + 1)      # 重叠回退，但不允许原地不动
    return out


def split_to_limit(text: str, limit: int) -> list[str]:
    """把超过硬上限的文本按句拆开（上限是 20000 字符，几乎不可能触发，触发即必须截）。

    这里的上限是**硬的**：预留出 `_CUT_SLACK` 再滑窗，保证每一段都不越过 limit。
    目标长度（cfg.size）那边允许往句末外扩几十个字，是为了句子完整；而走到这颗的块
    本来就已经长到必须切，长度承诺（页面展示、单块上限的对外口径）比句子完整重要。
    """
    text = (text or "").strip()
    if not text or len(text) <= limit:
        return [text] if text else []
    return text_window(text, max(1, limit - _CUT_SLACK), 0)


def _chunk_type(units: Sequence[Unit], piece: Piece) -> str:
    """切片模态：整块都是媒体才算媒体（图文混排的块按 text，向量文本里有媒体描述）。

    内嵌音频/视频在解析层也是 BLOCK_IMAGE 块（块类型体系里没有 audio/video 位），
    种类由 ImageRef.kind 按 mime 认，对外一律归 audio_video 模态——
    CHUNK_TYPES_ALL 只有 text/image/audio_video 三位，ES mapping 与前端分桶都锁着它。
    """
    covered = [u for _, u in piece.covered(units)]
    if not covered or not all(u.kind == C.BLOCK_IMAGE for u in covered):
        return RC.CHUNK_TYPE_TEXT
    kinds = {u.block.image.kind for _, u in piece.covered(units) if u.block.image is not None}
    if kinds & {RC.MEDIA_KIND_AUDIO, RC.MEDIA_KIND_VIDEO}:
        return RC.CHUNK_TYPE_AUDIO_VIDEO
    return RC.CHUNK_TYPE_IMAGE


def _page_of(units: Sequence[Unit], piece: Piece) -> tuple[int, int]:
    pages = [u.block.page for _, u in piece.covered(units)]
    pages = [p for p in pages if p and p > 0]
    if not pages:
        return 0, 0
    return min(pages), max(pages)


def _augment_context(units: Sequence[Unit], piece: Piece,
                     cfg: ChunkConfig) -> tuple[str, str]:
    """孤立图表/图片的前后文补齐（SPEC §7.5 通用增强）：只从相邻的正文单元里取字。

    只喂向量、不改展示正文：引用片段必须是原文（用户在文档里要能一字不差地找到它），
    而补齐的价值在于让「只有三行数字的表格」也能被自然语言问句命中——那是向量召回的事。

    媒体/表格类切片一律补齐（不再受全局 context_augment.enabled 约束）：媒体块不必独立成块，
    但一张只有「[图片]」占位的切片正文里没有任何可召回的字，必须带上前后的正文才召得回；
    页面没开补齐或把窗口配成 0 时按 MEDIA_AUGMENT_DEFAULT 兜一段。纯正文块不补——它本身就是上下文。
    """
    kinds = {u.kind for _, u in piece.covered(units)}
    if kinds <= {C.BLOCK_TEXT, C.BLOCK_TITLE}:
        return "", ""                         # 纯正文块不补：它本身就是上下文
    before_n, after_n = cfg.augment_window()
    if before_n <= 0 and after_n <= 0:
        before_n = after_n = C.MEDIA_AUGMENT_DEFAULT   # 媒体切片没配窗口也兜默认长度补齐
    before = after = ""
    head = min(max(0, int(piece.start)), max(0, len(units) - 1))
    tail = max(head + 1, min(int(piece.end), len(units)))
    if before_n > 0:
        buf: list[str] = []
        need = before_n
        for i in range(head - 1, -1, -1):
            u = units[i]
            if not u.is_text or not u.text:
                continue
            buf.append(u.text[-need:] if len(u.text) > need else u.text)
            need -= len(u.text)
            if need <= 0:
                break
        before = "".join(reversed(buf)).strip()
    if after_n > 0:
        buf = []
        need = after_n
        for i in range(tail, len(units)):
            u = units[i]
            if not u.is_text or not u.text:
                continue
            buf.append(u.text[:need] if len(u.text) > need else u.text)
            need -= len(u.text)
            if need <= 0:
                break
        after = "".join(buf).strip()
    return before, after


def _is_media_piece(units: Sequence[Unit], piece: "Piece") -> bool:
    """是否是「独立语义单元」：图片/表格/代码块——它们不参与碎块合并。"""
    if _chunk_type(units, piece) != RC.CHUNK_TYPE_TEXT:
        return True
    kinds = {u.kind for _, u in piece.covered(units)}
    return bool(kinds & {C.BLOCK_TABLE, C.BLOCK_IMAGE, C.BLOCK_FIGURE, C.BLOCK_CODE})


def assemble(units: Sequence[Unit], pieces: Sequence[Piece], cfg: ChunkConfig,
             *, warnings: Optional[list[str]] = None) -> list[Chunk]:
    """把策略产出的断开点装配成最终切片（顺序：截断 → 合并碎块 → 补上下文 → 编号）。"""
    units = list(units or [])
    limit = cfg.hard_limit
    size = cfg.size

    # 1. 超硬上限的先按句拆开（窗口类策略已按 size 切过，这里兜住标题/每页这类整块策略）
    expanded: list[Piece] = []
    for piece in pieces:
        text = piece.text(units)
        if len(text) <= limit:
            expanded.append(piece)
            continue
        for slot in split_to_limit(text, limit):
            new = Piece(start=piece.start, end=piece.end, parts=[slot],
                        meta=dict(piece.meta))
            expanded.append(new)
    pieces = expanded

    # 2. 碎块向后合并（图片/表格块既不主动往外并、也不当被并入的目标：
    #    把一小段文字塞进图片块，_chunk_type 一见混了文字就把整块退回 text 模态，
    #    那张图既丢了 image 分桶、block_ids 也指错——媒体不强制单独成块，但不能被文字淹没）
    merged: list[Piece] = []
    for piece in pieces:
        text = piece.text(units)
        if not text.strip():
            continue
        media = _is_media_piece(units, piece)
        last = merged[-1] if merged else None
        if (last is not None and not media and not _is_media_piece(units, last)
                and len(text) < cfg.min_chars):
            combined = last.text(units) + "\n" + text
            if len(combined) <= size:
                last.parts = [combined]
                last.end = max(last.end, piece.end)
                continue
        merged.append(piece)

    # 3. 逐块落切片
    out: list[Chunk] = []
    for piece in merged:
        content = piece.text(units).strip()
        if not content:
            continue
        span = piece.covered(units)
        path = span[0][1].path if span else ""
        before, after = _augment_context(units, piece, cfg)
        embed = content
        if cfg.title_path and path:
            embed = f"{path}\n{content}"
        if before or after:
            embed = "\n".join(x for x in (embed,
                                          f"[上文] {before}" if before else "",
                                          f"[下文] {after}" if after else "") if x)
        page, page_end = _page_of(units, piece)
        block_ids = [u.block.block_id for _, u in span if u.block.block_id]
        # 本块覆盖的每一个媒体都要记下，不是只取第一个：一页三张图只存下第一张，
        # 另两张就在 media_url 里彻底消失（正文里虽然有地址，但清理链路、页面预览
        # 与前端渲染靠的都是字段那一份，那个对象也从此没人能删）。media_type 与图片
        # 描述照旧只用第一张（见 Chunk.image）；模态由下面的 _chunk_type 按覆盖单元
        # 集合判，跟第几张无关，两者都不因这次改动而变。
        images = [u.block.image for _, u in span if u.block.image is not None]
        sheet = next((u.block.sheet for _, u in span if u.block.sheet), "")
        meta = dict(piece.meta)
        if before:
            meta["context_before"] = before
        if after:
            meta["context_after"] = after
        out.append(Chunk(index=len(out), content=content,
                         embed_text=embed,
                         chunk_type=_chunk_type(units, piece), title_path=path,
                         page=page, page_end=page_end, sheet=sheet, block_ids=block_ids,
                         images=images, tokens=count_tokens(embed), meta=meta))
    # 调用方传了列表就要拿得到 cfg 一路攒下的归一/降级告警：早期写成 `if warnings:`，
    # 空列表（刚 new 出来的收集器）为假直接短路，一句告警也传不出去，页面永远看不到降级
    if warnings is not None:
        warnings.extend(cfg.warnings)
    return out


_TABLE_ROW = re.compile(r"^\|.*\|$")


def table_header(md: str) -> tuple[str, list[str]]:
    """markdown 表格 → (表头两行[列名 + 分隔行], 数据行)。excel 策略靠它给每块重贴表头。"""
    lines = [ln.strip() for ln in (md or "").split("\n") if _TABLE_ROW.match(ln.strip())]
    if len(lines) < 2:
        return "", lines
    return "\n".join(lines[:2]), lines[2:]
