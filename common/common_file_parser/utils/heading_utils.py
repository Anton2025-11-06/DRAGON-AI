# -*- coding: utf-8 -*-
"""标题层级识别与面包屑路径（标题分块、title_path 前缀、图谱溯源的共同依据）。

为什么需要"推断"：只有 markdown/html/docx 这类自带层级的格式能直接给出标题级别，
pdf/txt 抽出来的就是一行行文字。而 SPEC §7.5 的「标题层级分块」与面包屑前缀是检索质量的
主要来源（一个「3.2 还款方式」的切片，没有路径时几乎无法回答"第 3 章讲的还款方式"）。
所以这里带一套中英文编号识别，把没有层级的文档也补齐出层级。
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from common.common_file_parser import constants as C

# 层级判定规则：(正则, 层级)。顺序敏感——先具体后一般。
# 中文公文/教材/合同里最常见的四套编号体系都覆盖到，英文按小数点编号递归降层。
BULLET_PATTERNS: list[tuple[re.Pattern, int]] = [
    # 第X篇/章/部分 → 1；第X节/小节/条/款 → 2/3
    (re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百零〇]+\s*[篇部])[\s、．.．]"), 1),
    (re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百零〇]+\s*章)([\s、．.．]|$)"), 1),
    (re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百零〇]+\s*节)[\s、．.．]"), 2),
    (re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百零〇]+\s*(?:条|款))[\s、．.．]"), 3),
    (re.compile(r"^\s*(附[件录录]\s*[0-9一二三四五六七八九十]*)[\s、．.．:：]"), 1),
    # 一、 / （一） / 1. / 1.1 / 1.1.1
    (re.compile(r"^\s*[一二三四五六七八九十百]+\s*[、．.．]\s*"), 2),
    (re.compile(r"^\s*[（(]\s*[一二三四五六七八九十]+\s*[)）]\s*"), 3),
    (re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*"), 4),
    (re.compile(r"^\s*((?:[0-9]{1,3}\.){2,3}[0-9]{1,3})[\s、．.．)）]\s*"), 4),
    (re.compile(r"^\s*([0-9]{1,3}\.[0-9]{1,3})[\s、．.．)）]\s*"), 3),
    (re.compile(r"^\s*([0-9]{1,3})[\s、．.．)）]\s*"), 2),
    # 英文章节名（Chapter 1 / Section 2.1 / Part III）
    (re.compile(r"^\s*(chapter|part)\s+([0-9]+|[IVXLCDM]+)\b", re.I), 1),
    (re.compile(r"^\s*(section|subsection)\s+[0-9.]+\b", re.I), 2),
    (re.compile(r"^\s*(appendix)\s+[A-Z0-9]+\b", re.I), 1),
]

# markdown ATX 标题（# 数量即层级）
_MD_HEADING = re.compile(r"^\s*(#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
# setext 下划线式标题（===== 一级、----- 二级）
_SETEXT_LEVEL = {"=": 1, "-": 2}
# 项目符号列表：不是标题（分块时要跟上一句合并，避免 "- " 开头的碎块）
_LIST_ONLY = re.compile(r"^\s*([-*+•·>]\s+.*)$")
# 句子结束的标点：以这些结尾的"看起来像标题"的行一律当正文
_SENTENCE_END = tuple("。！？；.!?;，,、：:")
# 标题长度上限：超过它基本是段落（中文标题很少长过这个数）
TITLE_MAX_CHARS = 80


def normalize_title(text: Optional[str]) -> str:
    """标题文本归一：去空白/序号后的引导点，压成一行（面包屑与图谱 title_path 都用它）。"""
    if not text:
        return ""
    one = re.sub(r"\s*\n\s*", " ", str(text)).strip()
    one = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", one)
    one = re.sub(r"[ \t]{2,}", " ", one)
    one = one.rstrip(" \t、.．·-—_")
    return one[:TITLE_MAX_CHARS]


def infer_heading(text: Optional[str], *, default_level: int = 0,
                  cjk_hint: bool = True) -> int:
    """推断一行的标题层级；不是标题返回 0。

    :param default_level: 引擎已知的层级（markdown/docx 样式）优先，直接返回
    :param cjk_hint: 关掉时跳过中文编号规则（英文文档里 "1. " 之类仍按阿拉伯编号识别）
    :return: 1~6 的层级，或 0（正文）
    """
    if default_level and default_level > 0:
        return int(default_level)
    raw = (text or "").strip()
    if not raw or len(raw) > TITLE_MAX_CHARS:
        return 0
    md = _MD_HEADING.match(raw)
    if md:
        return min(6, len(md.group(1)))
    if _LIST_ONLY.match(raw):
        return 0
    # 以句末标点收尾的行是段落，不是标题（"第一章 总则。" 这种写法极少，误判代价可接受）
    if raw.endswith(_SENTENCE_END):
        return 0
    for pattern, level in BULLET_PATTERNS:
        if pattern.match(raw):
            return level
    if not cjk_hint:
        return 0
    # 无编号但很短且居左顶格的短语：只有前面已经有同级标题时才认定（避免把每段首行都当标题）
    return 0


def infer_setext(prev_line: Optional[str], line: Optional[str]) -> int:
    """setext 风格（上一行是文字、本行只有 ==== 或 ----）：返回上一行的层级。"""
    marker = (line or "").strip()
    if not marker or len(set(marker)) != 1 or marker[0] not in _SETEXT_LEVEL:
        return 0
    if len(marker) < 2:
        return 0
    if not (prev_line or "").strip():
        return 0
    return _SETEXT_LEVEL[marker[0]]


def mark_title_blocks(blocks: Sequence, *, cjk_hint: bool = True) -> int:
    """就地把"长得像标题"的文本块标成 BLOCK_TITLE 并补 level（返回标注出的标题数）。

    引擎已经给了层级的块不动；只对无层级的正文块做推断。
    """
    marked = 0
    for b in blocks or []:
        if getattr(b, "type", "") == C.BLOCK_IMAGE:
            continue
        if int(getattr(b, "level", 0) or 0) > 0:
            marked += 1
            continue
        text = getattr(b, "text", "") or ""
        if "\n" in text.strip():        # 多行块是一整段，不是标题
            continue
        level = infer_heading(text, default_level=0, cjk_hint=cjk_hint)
        if level:
            b.type = C.BLOCK_TITLE
            b.level = level
            b.text = normalize_title(text)
            marked += 1
    return marked


def title_paths(blocks: Sequence, *, sep: str = " > ", max_level: int = 6) -> list[str]:
    """给每个块算出面包屑路径（含它自己就是标题的情况取到它自己）。

    层级不连续（1 级后面直接跟 3 级）时按"更深层级"处理，不报错也不补虚拟标题——
    文档本来就是残缺的，硬造一个中间层只会让面包屑里出现文档里没有的词。
    """
    stack: list[tuple[int, str]] = []
    out: list[str] = []
    for b in blocks or []:
        level = int(getattr(b, "level", 0) or 0)
        text = (getattr(b, "text", "") or "").strip()
        if level > 0:
            title = normalize_title(text) or "（空标题）"
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((min(level, max_level), title))
            out.append(sep.join(t for _, t in stack))
            continue
        out.append(sep.join(t for _, t in stack))
    return out


def title_of_first(blocks: Sequence) -> Optional[str]:
    """文档标题（第一个 1 级标题，没有就取第一块正文前若干字）：sidecar 与列表页展示用。"""
    for b in blocks or []:
        if int(getattr(b, "level", 0) or 0) == 1:
            return normalize_title(getattr(b, "text", ""))
    for b in blocks or []:
        text = normalize_title(getattr(b, "text", ""))
        if text:
            return text[:TITLE_MAX_CHARS]
    return None


def heading_stats(blocks: Sequence) -> dict[str, int]:
    """各级标题数量（auto 引擎的复杂度判据之一，也是排查"标题没识别出来"的第一眼数据）。"""
    stats: dict[str, int] = {}
    for b in blocks or []:
        level = int(getattr(b, "level", 0) or 0)
        if level > 0:
            key = f"level_{level}"
            stats[key] = stats.get(key, 0) + 1
    stats["total"] = sum(v for k, v in stats.items() if k != "total")
    return stats
