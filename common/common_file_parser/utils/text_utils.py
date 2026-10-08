# -*- coding: utf-8 -*-
"""文本处理工具：解码、空白归一、句子切分、页眉页脚/目录剔除。

这一层的每个函数都只做一件事，且**不改变语义**（改语义的是分块与预处理开关）。
判定阈值全部来自 constants.py，页面配置项能直接对号入座。
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from common.common_file_parser import constants as C

# 常见编码顺序：UTF-8 优先，中文老文档回落 GB18030（GBK 是它的子集，用超集更稳）
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")

# 零宽字符 / 软连字符 / 不间断空格 / 全角空格：这些会在 BM25 里把同一个词打成两个词
_INVISIBLE = {
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "", "\u00ad": "",
    "\u00a0": " ", "\u3000": " ", "\u2028": "\n", "\u2029": "\n",
}

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SENT_END = "。！？；!?;"
# 句子边界：结尾标点 + 可选引号/括号 + 空白（英文句点需要后面跟空格或行尾才算句末）
_SENT_SPLIT = re.compile(
    r"(?<=[。！？；])(?![。！？；])|(?<=[!?;])\s+|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])|\n+")

# 页码噪声：单独一行只有数字 / "第 3 页" / "- 3 -" / "Page 3 of 12" / 罗马数字页码
_PAGE_NOISE = (
    re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$"),
    re.compile(r"^\s*第\s*\d{1,4}\s*页(\s*[(/]\s*共\s*\d{1,4}\s*页\s*[)])?\s*$"),
    re.compile(r"^\s*[Pp]age\s+\d{1,4}(\s*of\s*\d{1,4})?\s*$"),
    re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫIVXLCM]{1,8}\.?\s*$"),
    re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$"),
)

# 目录行：标题 + 引导点 + 页码（也兼容中文书名号式目录与空格填充式）
_TOC_LINE = re.compile(r"^\s*(?P<title>.{1,120}?)\s*[.·。…⋯]{2,}\s*(?P<page>\d{1,4})\s*$")
_TOC_LINE_NO_DOTS = re.compile(r"^\s*(?P<title>[^\s].{0,80}?)\s{2,}(?P<page>\d{1,4})\s*$")
# 目录标题（出现这些字样的一段才按目录剔除，避免把正文里的点号行误删）
_TOC_HEADING = re.compile(r"^\s*(目录|目次|contents?|table\s+of\s+contents)\s*$", re.I)


def decode_bytes(raw: bytes) -> str:
    """字节 → 文本：按候选编码依次尝试，最后按 utf-8 容错（不再抛异常，残缺字符进日志）。"""
    if not raw:
        return ""
    head = raw[:4]
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            pass
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="ignore")


def normalize_space(text: Optional[str]) -> str:
    """空白归一：不可见字符清掉、行内多空格并一个、去行尾空格、三个以上连续换行压成两个。

    不做的事：不合并段落（换行仍是一个段落边界，分块要它）、不动缩进代码块内部结构。
    """
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    for bad, good in _INVISIBLE.items():
        out = out.replace(bad, good)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in out.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def is_mostly_cjk(text: Optional[str]) -> bool:
    """CJK 占比是否过半：决定分隔符选择与句子切分粒度。"""
    if not text:
        return False
    sample = text[:2000]
    return len(_CJK.findall(sample)) / max(1, len(sample)) >= C.CJK_RATIO_THRESHOLD


def split_sentences(text: Optional[str]) -> list[str]:
    """句子切分（语义分块与截断收口的最小单元）。"""
    if not text:
        return []
    parts = [p for p in re.split(_SENT_SPLIT, text) if p]
    out: list[str] = []
    for part in parts:
        one = part.strip()
        if not one:
            continue
        # 切分残留的空白行合并进上一句（否则语义分块会拿着一堆空串比相似度）
        if out and not out[-1].endswith(tuple(_SENT_END)) and len(out[-1]) < 40:
            out[-1] = f"{out[-1]} {one}".strip()
        else:
            out.append(one)
    return out


def is_page_noise(line: str) -> bool:
    """是否是页码类噪声行（页眉页脚剔除的最小判定单元）。"""
    if not line:
        return False
    stripped = line.strip()
    if not stripped:
        return False
    return any(p.match(stripped) for p in _PAGE_NOISE)


def is_toc_line(line: str) -> bool:
    """是否是目录行。"""
    stripped = (line or "").strip()
    if not stripped:
        return False
    return bool(_TOC_LINE.match(stripped) or _TOC_LINE_NO_DOTS.match(stripped))


def drop_headers_footers(blocks: Sequence, *, min_pages: int = 3) -> tuple[list, int]:
    """按「同一段文字在多数页的首尾都出现」判定页眉页脚并剔除。

    为什么不用固定行号：不同引擎给的首尾块位置不一样（native 按页抽、docling 按阅读顺序给），
    重复率是唯一对引擎无关的判据。页数不足 min_pages 时不做判定——两三页的文档里
    任何一句重复都可能是正文本身。

    :return: (保留的块, 被剔除的块数)
    """
    blocks = list(blocks or [])
    if not blocks:
        return [], 0
    pages: dict[int, list] = {}
    for b in blocks:
        if int(getattr(b, "page", 0) or 0) <= 0:
            return blocks, 0          # 无页概念（txt/markdown/docx 部分情形）：不判定
        pages.setdefault(int(b.page), []).append(b)
    if len(pages) < min_pages:
        return blocks, 0
    # 每页首尾各取两块做候选（页眉页脚一定出现在这个范围里）
    candidates: dict[str, int] = {}
    for page_blocks in pages.values():
        edge = page_blocks[:2] + page_blocks[-2:]
        for b in edge:
            key = _norm_key(getattr(b, "text", ""))
            if key:
                candidates[key] = candidates.get(key, 0) + 1
    total_pages = len(pages)
    noisy = {k for k, cnt in candidates.items()
             if cnt >= max(min_pages, int(total_pages * C.HEADER_FOOTER_REPEAT_RATIO))}
    if not noisy:
        return blocks, 0
    keep: list = []
    dropped = 0
    for b in blocks:
        key = _norm_key(getattr(b, "text", ""))
        text = getattr(b, "text", "") or ""
        if key in noisy or (len(text) <= 40 and is_page_noise(text)):
            dropped += 1
            continue
        keep.append(b)
    return keep, dropped


def drop_toc(blocks: Sequence) -> tuple[list, int]:
    """剔除目录：目录标题之后的连续目录行整段丢掉（正文里的少量点号行不受影响）。

    只管文字：图片这类 text 恒为空的块必须原样留下——它们的正文靠 image 承载，
    一旦被这里 continue 掉，后面的图片上传钩子就收不到任何图，「文档图片转 URL」整条链路断掉，
    而且 dropped 不计数，连一句告警都不会有（纯截图的 docx 甚至会退化成「解析结果为空」）。
    """
    blocks = list(blocks or [])
    keep: list = []
    dropped = 0
    in_toc = False
    run_hits = 0
    for b in blocks:
        text = (getattr(b, "text", "") or "").strip()
        if not text:
            if getattr(b, "is_media", False) or (getattr(b, "body", lambda: "")() or "").strip():
                keep.append(b)
            continue
        if _TOC_HEADING.match(text):
            in_toc = True
            dropped += 1
            continue
        if in_toc:
            if is_toc_line(text) or run_hits > 0 and len(text) < 40:
                dropped += 1
                run_hits += 1
                continue
            # 目录段结束：命中密度不够就把这段当作正文保留
            in_toc = False
            run_hits = 0
        keep.append(b)
    return keep, dropped


def strip_markdown_noise(text: Optional[str]) -> str:
    """去掉 markdown/富文本的排版噪声，保留可读文字。

    保留的：表格竖线（分块的 excel/table 策略要看结构）、代码块内容；
    去掉的：强调符、引用符、图片语法（换成 alt 文字）、链接语法（留锚文本）、HTML 标签、转义符。
    """
    if not text:
        return ""
    out = text
    out = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[\1]", out)          # 图片 → [alt]
    out = re.sub(r"\[([^\]]+)\]\(([^)]*)\)", r"\1", out)           # 链接 → 锚文本
    out = re.sub(r"<[^>]{1,200}>", " ", out)                        # 残留 HTML 标签
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.M)         # 标题井号（层级另判）
    out = re.sub(r"^\s{0,3}>\s?", "", out, flags=re.M)              # 引用
    out = re.sub(r"(\*\*|__|\*|_|~~)", "", out)                     # 强调与删除线
    out = re.sub(r"^\s*[-=]{3,}\s*$", "", out, flags=re.M)          # 分隔线
    out = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", r"\1", out)      # 反斜杠转义
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _norm_key(text: Optional[str]) -> str:
    """重复判定用的键：去空白与标点后取前 60 字符（页眉里的空格差异不该影响判重）。"""
    cleaned = re.sub(r"[\s\u3000]+", "", str(text or ""))
    return cleaned[:60].lower()
