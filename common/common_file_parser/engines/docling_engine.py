# -*- coding: utf-8 -*-
"""docling 增强解析引擎（SPEC §7.2 第 2 项 / §12 第 1 项：纯 pip 依赖、CPU 运行）。

实现取舍
--------
docling 的价值是「版面分析 + 表格结构还原 + 双栏阅读顺序」，它的产物是一颗结构化文档树
（DoclingDocument）。本引擎**不自己遍历那棵树**，而是走 `export_to_markdown()` 再把 markdown
交给统一的块构造器（md_block_builder）：

1. 版本容错：docling 2.x 内部模型（ItemLabel / content 数组 / prov 字段）在 minor 版本间
   反复改过，直接遍历树代码迟早被一次 pip 升级打断；markdown 导出是它的对外稳定契约。
2. 结构没丢：标题的 #、表格的管道行（或 tables_as_html 的 <table>）、图片的 ![]() 全部保留，
   而 md_block_builder 正是按这些标记还原 ParsedBlock 的，与 native 读 md 走同一条路。
   「换个引擎切片形态就变了」是最难查的问题，统一构造器从根上消掉它。

转换器（DocumentConverter）会加载版面模型，构造一次要几秒到十几秒，故进程内单例复用；
它不是线程安全的共享对象，构造用锁串行，转换调用交给 docling 自己的实例（单 worker 单实例）。
"""
from __future__ import annotations

import importlib
import inspect
import io
import threading
import zipfile
from typing import Any

from common.common_file_parser import constants as C
from common.common_file_parser.engines.base import (BaseEngine, EngineNotAvailable,
                                                    ParseError, guard_legacy_office,
                                                    normalize_ext)
from common.common_file_parser.engines.md_block_builder import markdown_to_blocks
from common.common_file_parser.models import ParsedDocument

try:                                          # noqa: SIM105 - docling 是可选重依赖
    from docling.document_converter import DocumentConverter
    HAS_DOCLING = True
except ImportError:
    DocumentConverter = None                  # type: ignore[assignment]
    HAS_DOCLING = False

DOCLING_INSTALL_HINT = "未安装 docling（pip install 'docling>=2.15'），或把解析引擎改回 native"

# OOXML 包里能当媒体用的部件后缀。EMF/WMF 是矢量图，native 也解不动，不该进告警计数；
# 音频与视频也算：docling 对这三类一概不导出字节，计数只为把一次「静默丢失」说成一句人话。
_MEDIA_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp", "svg", "jpf",
               "mp3", "wav", "m4a", "aac", "flac",
               "mp4", "mov", "avi", "mkv", "webm"}

_converter: Any = None
_converter_lock = threading.Lock()


def _build_converter() -> Any:
    """带图片导出的转换器：PDF 流水线的 generate_picture_images 不打开就没有图片字节。"""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import PdfFormatOption

    opts = PdfPipelineOptions()
    opts.generate_picture_images = True
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _embedded_value() -> Any:
    """导出参数里「图片内联」那个枚举值（类名与路径都随 docling-core 版本改过，拿不到就退字符串）。"""
    for path in ("docling_core.types.doc.base.ImageRefMode",
                 "docling_core.types.doc.base.ImageExportMode",
                 "docling_core.types.doc.document.ImageRefMode"):
        pkg, _, cls_name = path.rpartition(".")
        try:
            value = getattr(getattr(importlib.import_module(pkg), cls_name, None), "EMBEDDED", None)
        except Exception:                               # noqa: BLE001 - 版本漂移只当没这个枚举
            continue
        if value is not None:
            return value
    return "embedded"


def _image_export_kwargs(conv: Any) -> dict[str, Any]:
    """按当前 docling 的导出签名选「把图片字节写进 markdown」的参数名。

    这个参数在两版之间改过名（image_export_mode → image_mode），写死任意一个都会被另一个版本
    当成不支持参数丢掉，而丢掉的后果是静默的：导出退化成 ``<!-- image -->`` 占位 →
    图片没有字节 → 传不了存储 → 正文里看不到原位地址。所以先读一次签名再决定用哪个名字。
    """
    value = _embedded_value()
    try:
        params = set(inspect.signature(conv.export_to_markdown).parameters)
    except (TypeError, ValueError):                     # noqa: BLE001 - 读不到签名就按新名试
        params = set()
    for key in ("image_mode", "image_export_mode"):
        if params and key not in params:
            continue
        return {key: value}
    return {}


def _media_part_count(raw: bytes, ext: str) -> int:
    """OOXML 包（docx/pptx）里 media 目录的媒体件数：只看包结构、不解析内容。

    只为了把一个静默丢失变成一句可见说明：“docling 吃完了、一个字没丢，但图一张没剩下”
    比解析失败更难查。拿不到包结构（不是 zip、表头损坏）一律当 0 个：告警是锦上添花，
    不该反过来给解析添一种新失败。
    """
    if ext not in ("docx", "pptx"):
        return 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
    except Exception:                                   # noqa: BLE001 - 包结构不是我们要报错的东西
        return 0
    return sum(1 for n in names
               if n.startswith(("word/media/", "ppt/media/"))
               and normalize_ext(n) in _MEDIA_EXTS)


def _shared_converter() -> Any:
    """进程内复用一个 DocumentConverter（模型只加载一次，避免每篇文档都付一遍冷启动）。

    默认配置不导出图片字节，正文里只会留下一句 <!-- image --> 占位；这里把
    generate_picture_images 打开，embedded 形态才有真字节能传存储。打不开就退回默认构造
    （版面解析照常，只是图片拿不到原位地址），不能让一次参数漂移把整篇解析判死。
    """
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                try:
                    _converter = _build_converter()
                except Exception:                       # noqa: BLE001 - 版本差异退默认构造
                    _converter = DocumentConverter()
    return _converter


def reset_converter() -> None:
    """释放转换器实例（长驻 worker 内存吃紧时的手动回收口）。"""
    global _converter
    _converter = None


class DoclingEngine(BaseEngine):
    """docling 引擎：版面分析 + 表格还原，产物经 markdown 归一到统一块结构。"""

    name = C.ENGINE_DOCLING
    # docling 认识的格式比 native 宽（含图片型 pdf、asciidoc 等），但**老版 Office 必须剔掉**：
    # EXT_WORD/EXT_PPT 是「业务上的 Word/PPT 文档」分类，不等于 docling 真读得动 OLE2。
    # 这一位是 engine_formats() 的数据源，前端据它做「当前引擎不支持该格式」提示，
    # 留着 doc/ppt 就等于主动告诉用户这个引擎能吃它们。
    formats = (set(C.EXT_PDF) | set(C.EXT_WORD) | set(C.EXT_PPT) | set(C.EXT_EXCEL)
               | set(C.EXT_MARKDOWN) | set(C.EXT_HTML)) - C.LEGACY_OFFICE

    def ensure_available(self) -> None:
        if not HAS_DOCLING:
            raise EngineNotAvailable(DOCLING_INSTALL_HINT)

    async def _parse(self, raw: bytes, filename: str, ext: str) -> ParsedDocument:
        if not raw:
            raise ParseError("文件内容为空")
        # 排在格式闸门之前：formats 已剔掉 doc/ppt，走那道闸门只会得到
        # 一句没头没尾的「docling 不支持 .doc」，而这一句会告诉用户转成什么
        guard_legacy_office(ext)
        if ext not in self.formats:
            raise ParseError(f"docling 不支持 .{ext}")
        doc = self.new_document(filename, ext)
        table_format = str(self.option("docling_table_format", "markdown")).lower()
        md_text, pages, errors = await self.run_cpu(self._convert, raw, doc.name, ext,
                                                    table_format)
        for msg in errors:
            doc.warnings.append(f"docling: {msg}")
        if not md_text.strip():
            raise ParseError("docling 解析结果为空（可能是纯扫描件或加密文档），"
                             "可改用 minerU 或先做图像预处理")
        # 计数提到 markdown_to_blocks 之前：那一句只认识“整篇没正文”，
        # 而一篇只嵌了媒体的 docx 在这里的真实问题是“docling 没导出媒体”——
        # 告警排在 raise 后面就永远来不及说，用户只能看到一句误导的「格式无法识别」。
        parts = _media_part_count(raw, ext)
        try:
            blocks = markdown_to_blocks(md_text, page=0)
        except ParseError as e:
            if parts:
                # 别说成「docling 的 docx 通道不导图片」——实测 2.132 是能导的（内联 data URI）。
                # 走到这里只可能是这一次运行一个字节都没吐出来：要么版本不认内联导出参数
                # （_convert 重试时留过一句「不支持导出参数」），要么原文件里只有音频/视频。
                dropped = next((m for m in errors if "导出参数" in m), "")
                reason = (f"{dropped}；" if dropped else "") + \
                    "内嵌音频/视频是 docling 任何版本都取不到的"
                raise ParseError(
                    f"docling 从这份 .{ext} 里没导出任何正文，而原文件里有 {parts} 个内嵌媒体部件，"
                    f"这一版 docling 没能把它们导成可用字节（{reason}）。"
                    f"需要「媒体传存储 + 正文原位地址 + 解析增强」请改用解析引擎 native"
                    f"（它直接从 OOXML 包里抽内嵌部件，不依赖 docling 的导出能力）"
                ) from e
            raise ParseError(
                f"{e}（docling 导出的 markdown 只剩占位符，正文可能全是图片/扫描件："
                f"换 native 看内嵌媒体，或换 minerU 做版面增强）") from e
        doc.blocks = blocks
        doc.meta = {"pages": pages, "chars": len(md_text),
                    "images": sum(1 for b in blocks if b.is_media)}
        # docx/pptx 那两路的图片导出在新版 docling 上靠 export 的 image_mode=EMBEDDED 拿到了
        # （data URI 形态），但音频/视频仍然导不出来：只在「确实一个媒体块也没产出」时说这句。
        if parts and not doc.meta["images"]:
            doc.warnings.append(
                f"原文件里有 {parts} 个内嵌媒体（图片/音频/视频），docling 没把它们导出来（正文只剩占位）："
                f"需要「媒体传存储 + 正文原位地址」请改用原生解析 native")
        return doc

    def _convert(self, raw: bytes, name: str, ext: str,
                 table_format: str) -> tuple[str, int, list[str]]:
        """线程池里执行的同步转换：返回 (markdown, 页数, 引擎告警)。"""
        try:
            from docling.datamodel.base_models import DocumentStream
        except ImportError as e:                        # pragma: no cover
            raise ParseError(DOCLING_INSTALL_HINT) from e

        source = DocumentStream(name=name or f"document.{ext}", stream=io.BytesIO(raw))
        try:
            result = _shared_converter().convert(source)
        except Exception as e:                          # noqa: BLE001 - 转换失败原因五花八门
            raise ParseError(f"docling 解析失败: {e}") from e
        conv = getattr(result, "document", None)
        if conv is None:
            status = getattr(getattr(result, "status", None), "name", "unknown")
            raise ParseError(f"docling 未产出文档（status={status}）")
        errors = [str(x) for x in (getattr(result, "errors", None) or [])][:10]

        # 图片导出形态必须内联：默认的 placeholder 只在正文里留一句 <!-- image --> 注释，
        # 既传不上存储也显示不出来；embedded 才会拼成 data URI（参数名随版本变，见 _image_export_kwargs）
        markdown_kwargs: dict[str, Any] = _image_export_kwargs(conv)
        if table_format == "html":
            markdown_kwargs["tables_as_html"] = True
        kwargs = dict(markdown_kwargs)
        while kwargs:                                   # 一个参数一个参数地退，直到版本认得为止
            try:
                md_text = conv.export_to_markdown(**kwargs)
                break
            except TypeError as e:
                drop = next((k for k in kwargs if str(e) == k or f"'{k}'" in str(e)),
                            list(kwargs)[-1])
                errors.append(f"当前 docling 版本不支持导出参数 {drop}，已去掉它重试")
                kwargs = {k: v for k, v in kwargs.items() if k != drop}
        else:
            md_text = conv.export_to_markdown()
        pages = self._page_count(conv)
        return str(md_text or ""), int(pages or 0), errors

    @staticmethod
    def _page_count(conv: Any) -> int:
        """页数（各版本入口不一：新版 num_pages()，旧版 pages 字典；都拿不到就 0）。"""
        fn = getattr(conv, "num_pages", None)
        if callable(fn):
            try:
                return int(fn())
            except Exception:                           # noqa: BLE001
                pass
        pages = getattr(conv, "pages", None)
        if isinstance(pages, dict):
            return len(pages)
        return 0
