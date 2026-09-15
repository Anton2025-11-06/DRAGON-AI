# -*- coding: utf-8 -*-
"""文档文本提取：文件 URL → 下载字节 → 内存解析（11 种格式）。

使用方：DOC_EXTRACTOR 节点。开始节点上传文件后拿到的就是匿名可访问 URL
（见 common.common_storage.upload），下游节点引用的也是这个 URL，所以下游
只按 URL 取字节，不再读本地路径、不再查 DB、不再凭 fileId 反推存储引用。

**无盘化约定**：所有解析都在内存里做（pypdf / python-docx / openpyxl / xlrd /
python-pptx 都接受可 seek 的 BytesIO），唯一例外是老格式 .doc/.ppt 要 LibreOffice
子进程转换 —— 子进程只认真实路径，那里用 tempfile 落地、finally 立即删除，
绝不在业务目录或缓存目录留长期副本。
"""
from __future__ import annotations

import asyncio
import io
import os
from typing import Optional
from urllib.parse import unquote, urlsplit

import httpx

from common.common_storage import MAX_FILE_SIZE, original_name

# 文本类只取前 2MB（沿用历史行为，防超大日志把内存打满）
_MAX_TEXT_CHARS = 2 * 1024 * 1024
# 解码顺序：utf-8 优先，中文老文件常见 gbk，最后兜底忽略非法字节
_ENCODINGS = ("utf-8", "gbk")
# 拉取远端文件的超时（秒）：连接 10s，整体读取给够（大文件走内网 OSS）
_FETCH_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class FileUtils:
    """文档文本提取静态工具类（无业务 DB 依赖、无磁盘依赖）。"""

    # ==================== URL → 文本 ====================

    @staticmethod
    async def extract_from_url(url: str, supported_types: Optional[list] = None,
                               http_client: Optional[httpx.AsyncClient] = None
                               ) -> tuple[str, dict]:
        """文件 URL → (文本内容, 元数据)：内存直读直解，不落盘、不留缓存。

        http_client 传引擎的共享连接池（runtime.http_client）；没传才临时建一个。
        文件名从 URL 路径末段反推（存储名形如 {uuid}_{原始名}，展不展示不影响解析，
        但扩展名要用原始名判，故先剥 uuid 前缀）。
        """
        if not url:
            raise ValueError("文件 URL 为空")
        data = await FileUtils._fetch(url, http_client)
        name = _url_file_name(url)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        _check_type(ext, supported_types)
        try:
            content = await asyncio.to_thread(FileUtils.extract_text, data, ext)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001  解析库抛出的格式错误统一成业务错误
            raise ValueError(f"文档解析失败（{name}）: {e}") from e
        return content, {"name": name, "size": len(data), "type": ext or "txt", "url": url}

    @staticmethod
    async def _fetch(url: str, client: Optional[httpx.AsyncClient]) -> bytes:
        """URL → 字节：复用引擎的共享连接池，没给才临时建一个。"""
        if client is not None:
            return FileUtils._bytes_of(await client.get(url))
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as own:
            return FileUtils._bytes_of(await own.get(url))

    @staticmethod
    def _bytes_of(resp: httpx.Response) -> bytes:
        """响应 → 字节：状态/空文件/超大小三道校验（按实际字节数判，不信任 Content-Length）。"""
        try:
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ValueError(f"文件下载失败: {resp.url} - {e}") from e
        data = resp.content
        if not data:
            raise ValueError("文件内容为空，无法解析")
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"文件超过大小上限 {MAX_FILE_SIZE // 1024 // 1024}MB")
        return data

    @staticmethod
    def extract_text(data: bytes, ext: str) -> str:
        """按扩展名从字节里提取文本（同步方法，pdf 无内嵌文本页返回空串，不报错）。"""
        data = bytes(data or b"")
        if ext in ("txt", "md", "log", "csv", "json"):
            return FileUtils._decode(data)[:_MAX_TEXT_CHARS]
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if ext == "docx":
            from docx import Document
            doc = Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
            return "\n".join(parts)
        if ext == "xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                rows = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        cells = ["" if v is None else str(v) for v in row]
                        if any(c.strip() for c in cells):
                            rows.append("\t".join(cells))
                return "\n".join(rows)
            finally:
                wb.close()
        if ext == "xls":
            import xlrd
            book = xlrd.open_workbook(file_contents=data)
            rows = []
            for sheet in book.sheets():
                for r in range(sheet.nrows):
                    cells = ["" if v is None else str(v) for v in sheet.row_values(r)]
                    if any(c.strip() for c in cells):
                        rows.append("\t".join(cells))
            return "\n".join(rows)
        if ext == "pptx":
            from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            parts = []
            for slide in prs.slides:
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            text = "".join(run.text for run in para.runs).strip()
                            if text:
                                texts.append(text)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            cells = [c.text.strip() for c in row.cells]
                            if any(cells):
                                texts.append(" | ".join(cells))
                if texts:
                    parts.append("\n\n".join(texts))
            return "\n\n".join(parts)
        if ext == "html":
            import html as html_mod
            import re
            raw = FileUtils._decode(data)[:_MAX_TEXT_CHARS]
            raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
            text = re.sub(r"(?s)<[^>]+>", " ", raw)
            return html_mod.unescape(re.sub(r"[ \t]+", " ", text)).strip()
        if ext in ("doc"):
            raise ValueError(f"不支持的文件类型: .{ext} 请转换为 .docx 后重试")
        if ext in ("ppt"):
            raise ValueError(f"不支持的文件类型: .{ext} 请转换为 .pptx 后重试")
        raise ValueError(f"不支持的文件类型: .{ext}")

    # ==================== 内部工具 ====================

    @staticmethod
    def _decode(data: bytes) -> str:
        """字节 → 文本：utf-8 → gbk → 最后忽略非法字节。"""
        for enc in _ENCODINGS:
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="ignore")

    @staticmethod
    def _decode_read(path: str) -> str:
        """读临时转换产物（LibreOffice 输出可能是 utf-8 或本地编码）。"""
        with open(path, "rb") as f:
            return FileUtils._decode(f.read())[:_MAX_TEXT_CHARS]


# ==================== 模块工具 ====================

def _url_file_name(url: str) -> str:
    """URL → 原始文件名：取路径末段（已 URL 解码）再剥掉存储名的 uuid 前缀。"""
    path = unquote(urlsplit(url).path or "")
    return original_name(path.rsplit("/", 1)[-1]) or "unnamed"


def _check_type(ext: str, supported_types: Optional[list]) -> None:
    """节点配了 supportedTypes 时做类型白名单校验（前端传大写不带点，保持容错）。"""
    if not supported_types:
        return
    allowed = {str(t).lower().lstrip(".") for t in supported_types}
    if ext not in allowed:
        raise ValueError(f"不支持的文件类型 .{ext or '(无扩展名)'}"
                         f"（允许: {', '.join(sorted(allowed))}）")
