# -*- coding: utf-8 -*-
"""把「标记语言」（markdown / HTML）转成结构块：native 直读 md/html，docling 与 minerU
的返回值本身也是 markdown，三者共用这一套块构造，保证「换引擎不换切片形态」。

为什么不走 langchain 的 markdown loader：它按语法树产出 Document，标题层级与表格结构
都会在中途被摊平成纯文本，而分块策略要看的就是「这块属于哪个标题」「这张表的表头是哪行」。
"""
from __future__ import annotations

import base64
import re
from typing import Callable, Optional

from common.common_file_parser import constants as C
from common.common_file_parser.engines.base import ParseError, mime_of, normalize_ext
from common.common_file_parser.models import ImageRef, ParsedBlock
from common.common_file_parser.utils.heading_utils import (infer_heading,
                                                            infer_setext,
                                                            normalize_title)
from common.common_file_parser.utils.text_utils import normalize_space

# 围栏代码块 ```lang ... ```（代码块是完整语义单元，内部绝不按空行拆段）
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+\-\.]*)\s*$")
# 行内图片语法：![alt](src "title")
_MD_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 表格行（markdown 管道语法）
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# 列表/引用前缀（判断段落延续用）
_LIST_PREFIX = re.compile(r"^\s{0,3}([-*+]\s+|\d{1,3}[.)]\s+)")
# 整行就是一个 HTML 注释：minerU 用 ``<!-- image -->`` 标记图片位置，
# 它既不是正文也不该进切片（真正的图片引用 ![](...) 在紧随其后的那一行）
_HTML_COMMENT_LINE = re.compile(r"^\s*<!--.*-->\s*$")
# 行内注释（混在正文里的排版说明）：只删注释本身，不动一行里的其他文字
_HTML_COMMENT_INLINE = re.compile(r"<!--.*?-->")
# 已经是网络地址的图片引用（不需要再上传存储，也就能直接拿去描述）
_EXT_SRC = re.compile(r"^(?:https?:)?//", re.I)


def _image_from_src(src: str, alt: str, *, page: int = 0, bid: str = "") -> ImageRef:
    """图片引用：data URI 直接解出字节，外链只留地址（不做远程抓取，防止解析阶段被外网拖死）。

    相对路径（minerU 的 ``images/x.jpg``）既不是地址也不是字节，但 src 得留着：
    只有靠它才能把服务单独返回的 base64 图片数据配回正文原位。
    """
    ref = ImageRef(block_id=bid, page=page, alt=(alt or "").strip(), mime="image/png")
    raw = (src or "").strip()
    ref.src = "" if raw.startswith("data:") else raw[:512]
    if raw.startswith("data:"):
        head, _, body = raw.partition(",")
        ref.mime = (head[5:].split(";")[0] or "image/png").strip() or "image/png"
        try:
            ref.data = base64.b64decode(body) if body else None
        except (ValueError, TypeError):
            ref.data = None
    elif _EXT_SRC.match(raw):
        ref.url = raw
    ext = normalize_ext(raw.split("?")[0].rsplit("/", 1)[-1])
    if ext:
        ref.mime = mime_of(ext)
    return ref


def html_table_to_markdown(table_text: str) -> str:
    """HTML 表格 → markdown 表格（第一行作表头）。

    docling 配 `tables_as_html` 或用户上传 .html 时会给出 HTML 表格；分块的 excel/table
    判定与页面渲染都按 markdown 表格走，所以在这里一次性收敛成同一形态。
    """
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_text, re.S | re.I)
    if not rows:
        return normalize_space(table_text)
    out: list[list[str]] = []
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        one = []
        for cell in cells:
            text = re.sub(r"<br\s*/?>", " ", cell, flags=re.I)
            text = re.sub(r"<[^>]+>", "", text)
            text = normalize_space(text).replace("\n", " ").replace("|", "\\|")
            one.append(text.strip())
        if one:
            out.append(one)
    if not out:
        return ""
    width = max(len(r) for r in out)
    lines = []
    for i, r in enumerate(out):
        pad = r + [""] * (width - len(r))
        lines.append("| " + " | ".join(pad) + " |")
        if i == 0:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


def _close_paragraph(buf: list[str], blocks: list[ParsedBlock], *, page: int) -> None:
    text = normalize_space("\n".join(buf))
    buf.clear()
    if not text:
        return
    level = infer_heading(text.split("\n", 1)[0] if "\n" not in text else "", default_level=0)
    blocks.append(ParsedBlock(type=C.BLOCK_TITLE if level else C.BLOCK_TEXT,
                              text=text, level=level, page=page))


def markdown_to_blocks(text: str, *, page: int = 0,
                       page_of: Optional[Callable[[int], int]] = None) -> list[ParsedBlock]:
    """markdown → 结构块（标题带层级、表格成块、代码块不被拆、图片成独立块）。

    :param page_of: 行号 → 页码的映射（minerU 的分页标记用得上），留空则全部同一页
    """
    if not (text or "").strip():
        return []
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[ParsedBlock] = []
    buf: list[str] = []
    page_break = 0
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        cur_page = int(page_of(idx)) if page_of else page + page_break
        stripped = line.strip()

        # 分页标记（minerU / 手工写的 \newpage）：影响后续块的页码归属
        if re.fullmatch(r"(-{3,}\s*page\s*-+|\\newpage|<div class=\"pagebreak\"></div>)",
                        stripped, re.I):
            _close_paragraph(buf, blocks, page=cur_page)
            page_break += 1
            idx += 1
            continue

        fence = _FENCE.match(line)
        if fence:                                  # 代码块整块吃掉
            _close_paragraph(buf, blocks, page=cur_page)
            marker = fence.group(1)[0]
            lang = fence.group(2)
            body: list[str] = []
            idx += 1
            while idx < len(lines):
                close_line = lines[idx].strip()
                if close_line.startswith(marker * 3) and len(close_line.strip(marker)) == 0:
                    idx += 1
                    break
                body.append(lines[idx])
                idx += 1
            code = "\n".join(body).strip("\n")
            if code:
                blocks.append(ParsedBlock(type=C.BLOCK_CODE, text=code, page=cur_page,
                                          meta={"lang": lang}))
            continue

        # 整行 HTML 注释：图片占位与排版说明都不是正文，关掉段落直接丢行
        if _HTML_COMMENT_LINE.match(line):
            _close_paragraph(buf, blocks, page=cur_page)
            idx += 1
            continue

        if stripped.startswith("<table") or stripped.startswith("<TABLE"):
            _close_paragraph(buf, blocks, page=cur_page)
            chunk = [line]
            while idx + 1 < len(lines) and "</table>" not in chunk[-1].lower():
                idx += 1
                chunk.append(lines[idx])
            idx += 1
            md = html_table_to_markdown("\n".join(chunk))
            if md:
                blocks.append(ParsedBlock(type=C.BLOCK_TABLE, text=md, page=cur_page))
            continue

        if _MD_TABLE_ROW.match(stripped):          # markdown 表格：连续行合成一块
            _close_paragraph(buf, blocks, page=cur_page)
            rows = []
            while idx < len(lines) and _MD_TABLE_ROW.match(lines[idx].strip()):
                rows.append(lines[idx].strip())
                idx += 1
            blocks.append(ParsedBlock(type=C.BLOCK_TABLE, text="\n".join(rows),
                                      page=cur_page))
            continue

        # ATX 标题
        md_head = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if md_head:
            _close_paragraph(buf, blocks, page=cur_page)
            blocks.append(ParsedBlock(type=C.BLOCK_TITLE,
                                      text=normalize_title(md_head.group(2)),
                                      level=min(6, len(md_head.group(1))), page=cur_page))
            idx += 1
            continue

        # setext 标题（上一行文字 + 本行 ==== / ----）
        setext = infer_setext(lines[idx - 1] if idx else "", line)
        if setext and buf:
            title = normalize_title(buf[-1])
            buf.pop()
            _close_paragraph(buf, blocks, page=cur_page)
            blocks.append(ParsedBlock(type=C.BLOCK_TITLE, text=title, level=setext,
                                      page=cur_page))
            idx += 1
            continue

        # 图片：独立成块，原文位置由块顺序保证（SPEC §7.4 图文顺序不变）
        images = list(_MD_IMAGE.finditer(line))
        if images and not _LIST_PREFIX.match(line):
            residual = _MD_IMAGE.sub("", line).strip()
            if residual and buf:
                buf.append(residual)
                _close_paragraph(buf, blocks, page=cur_page)
            elif residual:
                blocks.append(ParsedBlock(type=C.BLOCK_TEXT, text=normalize_space(residual),
                                          page=cur_page))
            for m in images:
                blocks.append(ParsedBlock(
                    type=C.BLOCK_IMAGE, text="", page=cur_page,
                    image=_image_from_src(m.group("src"), m.group("alt"), page=cur_page)))
            idx += 1
            continue

        if not stripped:                            # 空行 = 段落边界
            _close_paragraph(buf, blocks, page=cur_page)
            idx += 1
            continue

        if not buf and _LIST_PREFIX.match(line):    # 列表项连续聚成一块（每项一行，保持结构）
            items = []
            while idx < len(lines) and (_LIST_PREFIX.match(lines[idx])
                                        or (items and lines[idx].startswith("  "))):
                items.append(lines[idx].strip())
                idx += 1
            blocks.append(ParsedBlock(type=C.BLOCK_TEXT, text="\n".join(items),
                                      page=cur_page, meta={"list": True}))
            continue

        buf.append(_HTML_COMMENT_INLINE.sub("", line))
        idx += 1

    _close_paragraph(buf, blocks, page=page + page_break)
    if not blocks:
        raise ParseError("文档内容为空或格式无法识别")
    return blocks


def html_to_blocks(html: str) -> list[ParsedBlock]:
    """HTML → 结构块（lxml 解析；标题层级取 h1~h6，表格转 markdown，图片留 src/data URI）。"""
    try:
        from lxml import html as _lxml_html
    except ImportError as e:                        # pragma: no cover
        raise ParseError("缺少 lxml 依赖，无法解析 HTML（pip install lxml）") from e

    try:
        tree = _lxml_html.fromstring(html or "")
    except Exception as e:                          # noqa: BLE001 - lxml 的解析异常种类很杂
        raise ParseError(f"HTML 解析失败: {e}") from e
    body = tree.find(".//body")
    root = body if body is not None else tree
    blocks: list[ParsedBlock] = []

    def push_text(text: str, *, level: int = 0, kind: str = C.BLOCK_TEXT) -> None:
        cleaned = normalize_space(text)
        if cleaned:
            blocks.append(ParsedBlock(type=kind, text=cleaned, level=level))

    def walk(node) -> None:
        tag = str(getattr(node, "tag", "") or "").lower()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            push_text(normalize_title(node.text_content()), level=int(tag[1]),
                      kind=C.BLOCK_TITLE)
            return
        if tag == "table":
            md = html_table_to_markdown(_lxml_html.tostring(node, encoding="unicode"))
            push_text(md, kind=C.BLOCK_TABLE)
            return
        if tag in ("pre", "code"):
            push_text(node.text_content().strip("\n"), kind=C.BLOCK_CODE)
            return
        if tag == "img":
            # 图片单独成块：alt 文字进 ImageRef.alt，图片增强开启时由 image_hook 补描述
            blocks.append(ParsedBlock(
                type=C.BLOCK_IMAGE, text="",
                image=_image_from_src(node.get("src") or "", node.get("alt") or "")))
            return
        if tag in ("script", "style", "head", "title", "nav", "footer", "noscript"):
            return
        children = list(node) if hasattr(node, "__iter__") else []
        if not children:
            push_text(node.text_content())
            return
        # 容器节点：先吃自己的直接文本（避免与子节点文本重复计入），再递归
        own = node.text or ""
        push_text(own)
        for child in children:
            walk(child)
            tail = getattr(child, "tail", None)
            push_text(tail or "")

    walk(root)
    if not blocks:
        raise ParseError("HTML 里没有可提取的正文")
    return blocks
