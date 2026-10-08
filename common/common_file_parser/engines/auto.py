# -*- coding: utf-8 -*-
"""auto 引擎：按文档复杂度自动挑引擎（SPEC §7.2 末段「也可由系统自动根据文档复杂度选择」）。

判据与代价
----------
探测本身必须比解析便宜得多，否则「先探再选」就变成了双份开销。所以只做三件便宜事：

* PDF：`PdfReader` 只读目录结构就能拿到页数，再抽前几页文字算密度、数前几页图片对象——
  不逐页解析，扫描件特征在首页密度上已经足够显现；
* DOCX/PPTX：解压包里数 `media/` 条目即图片数，读 XML 里 slide/paragraph 数即规模（零解析）；
* 其余格式：按字符量判断，本来 native 就够好，不值得动用增强引擎。

偏好顺序：扫描件/大页量/多图 → minerU；表格类与纯文本 → native。
增强引擎不可用（没配 minerU 地址）时回落 native，并把「回落原因」写进
warnings——页面上看得到，用户才知道自己拿到的是降级结果。
"""
from __future__ import annotations

import io
import re
import zipfile

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C
from common.common_file_parser.engines.base import (BaseEngine, EngineNotAvailable,
                                                    ParseError, build_engine,
                                                    normalize_ext)
from common.common_file_parser.models import ParsedDocument
from common.common_file_parser.utils.text_utils import decode_bytes

# 探测 PDF 时最多翻几页（首页密度足以判定扫描件，翻全本是纯浪费）
PROBE_PAGES = 3
# 认为「文档很大」的字节数：超过它优先增强引擎（大文件出错的重跑成本也最高）
BIG_FILE_BYTES = 20 * 1024 * 1024


def probe_pdf(raw: bytes) -> dict:
    """页数 + 首页文字密度 + 前几页图片数（不抽全本，探测成本压到几十毫秒量级）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages = len(reader.pages)
    chars = 0
    images = 0
    for page in reader.pages[:PROBE_PAGES]:
        chars += len(re.sub(r"\s+", "", page.extract_text() or ""))
        try:
            images += len(list(page.images))
        except Exception:                               # noqa: BLE001 - 图片解码失败不影响判据
            pass
    scanned = pages > 0 and chars / min(pages, PROBE_PAGES) < C.SCANNED_MIN_CHARS_PER_PAGE
    return {"pages": pages, "chars": chars, "images": images, "scanned": scanned}


def probe_office(raw: bytes) -> dict:
    """docx/pptx/xlsx 的规模：zip 条目数即图片数，slide/paragraph 数即体量（不跑解析库）。"""
    out = {"pages": 0, "chars": 0, "images": 0, "scanned": False}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            out["images"] = sum(1 for n in names if "/media/" in n and not n.endswith("/"))
            slides = sum(1 for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
            if slides:
                out["pages"] = slides
            for target in ("word/document.xml", "ppt/presentation.xml"):
                if target in names:
                    out["chars"] += len(zf.read(target))       # XML 长度当规模代理值
    except (zipfile.BadZipFile, KeyError, OSError):
        pass
    return out


def probe_document(raw: bytes, ext: str) -> dict:
    """统一探测入口：返回 {pages, chars, images, scanned}，探不动的一律给零（倾向 native）。"""
    ext = (ext or "").lower().lstrip(".")
    try:
        if ext in C.EXT_PDF:
            info = probe_pdf(raw)
        elif ext in C.EXT_WORD or ext in C.EXT_PPT or ext in C.EXT_EXCEL:
            info = probe_office(raw)
        else:
            info = {"pages": 0, "chars": len(decode_bytes(raw)), "images": 0, "scanned": False}
    except Exception:                                   # noqa: BLE001 - 探测失败不该阻断解析
        info = {"pages": 0, "chars": 0, "images": 0, "scanned": False}
    info["size"] = len(raw or b"")
    return info


def rank_candidates(ext: str, probe: dict) -> list[str]:
    """给出该文档的引擎偏好顺序（越靠前越合适），由调用方按可用性过滤。"""
    complex_doc = bool(probe.get("scanned")) \
        or int(probe.get("pages") or 0) >= C.AUTO_MIN_PAGES \
        or int(probe.get("images") or 0) >= C.AUTO_MIN_IMAGES \
        or int(probe.get("size") or 0) >= BIG_FILE_BYTES
    if ext in C.LEGACY_OFFICE:
        # 老版 Office 只有 minerU 能吃（native 一律拒收 OLE2 复合文档），顺序不能变
        return [C.ENGINE_MINERU, C.ENGINE_NATIVE]
    if ext in C.TEXT_FORMATS and not complex_doc:
        return [C.ENGINE_NATIVE]
    if complex_doc:
        return [C.ENGINE_MINERU, C.ENGINE_NATIVE]
    return [C.ENGINE_NATIVE]


class AutoEngine(BaseEngine):
    """auto 引擎：探测 → 选路 → 委托真引擎，产物里的 engine 字段回填实际使用者。"""

    name = C.ENGINE_AUTO
    # 与 native 同口径：只按文档格式探测与路由，媒体文件走服务层的转写分支，不进本引擎。
    # doc/ppt 不剔（与 native 不同）：auto 自己读不动，但 rank_candidates 把它们优先
    # 路由给 minerU。配置页只列 native/mineru 两个真引擎，不把这种「看部署脸色」的
    # 条件支持面投给用户。
    formats = set(RC.DOC_TEXT_EXTS)

    async def _parse(self, raw: bytes, filename: str, ext: str) -> ParsedDocument:
        ext = ext or normalize_ext(filename)
        if not raw:
            raise ParseError("文件内容为空")
        probe = await self.run_cpu(probe_document, raw, ext)
        chosen, tried = self._pick(ext, probe)
        delegate = build_engine(chosen, {**self.options, "engine_requested": self.name})
        delegate.ensure_available()
        doc = await delegate.parse(raw, filename, ext=ext)
        # doc.engine 保留实跑的那个引擎（auto 只是路由）：列表页的「解析引擎」列与排障
        # 都靠它，写成 auto 等于什么也没说
        doc.meta["auto_probe"] = probe
        doc.meta["engine_actual"] = chosen
        note = f"自动选择 {chosen}（页数 {probe.get('pages')}、图片 {probe.get('images')}、" \
               f"文字密度 {'低/疑似扫描件' if probe.get('scanned') else '正常'}）"
        if tried:
            note += f"；未采用的引擎：{tried}"
        doc.warnings.insert(0, note)
        return doc

    def _pick(self, ext: str, probe: dict) -> tuple[str, str]:
        """按偏好顺序挑第一个可用引擎，同时记录「跳过了谁、为什么」（页面可见）。"""
        skipped: list[str] = []
        for candidate in rank_candidates(ext, probe):
            try:
                engine = build_engine(candidate, self.options)
                engine.ensure_available()
            except (ParseError, EngineNotAvailable) as e:
                skipped.append(f"{candidate} 不可用({e})")
                continue
            return candidate, "、".join(skipped)
        raise ParseError("没有可用的解析引擎：" + "；".join(skipped))
