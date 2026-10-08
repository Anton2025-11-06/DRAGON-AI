# -*- coding: utf-8 -*-
"""minerU 增强解析引擎（SPEC §7.2 第 3 项 / §12 第 2 项：私有化 HTTP API，本地零模型依赖）。

调用形态
--------
minerU 官方 HTTP 服务提供两种口：

1. **异步任务口**（首选，大文件不占长连接）
   ``POST {base}/tasks``（multipart 上传 + 参数）→ ``{"task_id": "..."}``
   ``GET  {base}/tasks/{id}`` → ``{"status": "pending|processing|completed|failed"}``
   ``GET  {base}/tasks/{id}/result`` → ``{"results": {"文件名": {"md_content": "..."}}}``
2. **同步解析口**（部署方只开了这一个时的退路）
   ``POST {base}/file_parse`` → ``{"results": {"文件名": {"md_content": "..."}}}``

先走异步口；返回 404/405/501（部署版本没有该路由）时自动退化到同步口，并在 warnings 里说明——
这样「部署了旧版 minerU」不需要改代码，只会在文档上看出一条降级记录。

md_content 交给统一的块构造器，保证各引擎的分块输入形态一致。
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C
from common.common_file_parser.engines.base import (BaseEngine, EngineNotAvailable,
                                                    ParseError)
from common.common_file_parser.engines.md_block_builder import markdown_to_blocks
from common.common_file_parser.models import ParsedBlock, ParsedDocument

try:
    import httpx
    HAS_HTTPX = True
    HTTPX_INSTALL_HINT = ""
except ImportError:                                   # pragma: no cover
    httpx = None                                      # type: ignore[assignment]
    HAS_HTTPX = False
    HTTPX_INSTALL_HINT = "未安装 httpx（pip install httpx），minerU 引擎无法发起 HTTP 调用"

# 路由（部署方带前缀时可用 options["mineru"]["path_prefix"] 覆盖，如 /api/v1）
PATH_TASKS = "/tasks"
PATH_TASK_RESULT = "/tasks/{task_id}/result"
PATH_FILE_PARSE = "/file_parse"


class _PooledClient:
    """借用全局 httpx 连接池、但退出时不关闭它（真 client 的生死归 httpx_pool 管）。

    minerU 的单文档等待上限可以调到三十分钟量级，而全局池的默认超时是给普通 API 调用用的，
    所以这里按请求覆盖 timeout，而不是去改全局配置（改了会把其他业务的超时一并拉高）。
    """

    def __init__(self, client: Any, timeout: float) -> None:
        self._client = client
        self._timeout = timeout

    async def __aenter__(self) -> "_PooledClient":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> Any:
        kwargs["timeout"] = self._timeout
        return await self._client.post(url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> Any:
        kwargs["timeout"] = self._timeout
        return await self._client.get(url, **kwargs)


def _service_cfg(options: dict) -> dict:
    """取 minerU 服务配置（Nacos 的 mineru 段整份传进来，代码里不写死地址与超时）。"""
    cfg = options.get("mineru") or {}
    return {
        "base_url": str(cfg.get("base_url") or "").rstrip("/"),
        "api_key": str(cfg.get("api_key") or ""),
        "poll_interval": int(cfg.get("poll_interval") or C.MINERU_DEFAULT_POLL_INTERVAL),
        "timeout": int(cfg.get("timeout") or C.MINERU_DEFAULT_TIMEOUT),
        "path_prefix": str(cfg.get("path_prefix") or "").rstrip("/"),
        "backend": str(cfg.get("backend") or RC.PARSE_OPTION_DEFAULTS["mineru_backend"]),
    }


class MineruEngine(BaseEngine):
    """minerU 引擎：异步任务 + 轮询，退化同步口；产物按统一块结构归一。"""

    name = C.ENGINE_MINERU
    formats = set(C.EXT_PDF) | set(C.EXT_WORD) | set(C.EXT_PPT) | set(C.EXT_MARKDOWN) | \
        set(C.EXT_HTML) | {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}

    def __init__(self, options: Optional[dict] = None) -> None:
        super().__init__(options)
        self.cfg = _service_cfg(self.options)

    def ensure_available(self) -> None:
        if not HAS_HTTPX:
            raise EngineNotAvailable(HTTPX_INSTALL_HINT)
        if not self.cfg["base_url"]:
            raise EngineNotAvailable(
                "未配置 minerU 服务地址（Nacos 的 mineru.base_url），"
                "请把解析引擎改为 native 或补上部署配置")

    async def _parse(self, raw: bytes, filename: str, ext: str) -> ParsedDocument:
        if not raw:
            raise ParseError("文件内容为空")
        doc = self.new_document(filename, ext)
        name = doc.name or f"document.{ext}"
        payload = await self._request_parse(raw, name, doc)
        md_text, images = self._extract_result(payload, name, doc)
        if not md_text.strip():
            raise ParseError("minerU 返回的正文为空（服务可能不支持该格式，或文件为加密件）")
        blocks = markdown_to_blocks(md_text, page=0)
        blocks = self._attach_images(blocks, images, doc)
        # 图片块一律保留：传存储并把地址回填到正文原位与「图片智能解析」开关无关，
        # 配不到字节的引用会在 finalize 里退成图注文字（没图注就丢块）
        if images and not any(b.image is not None and b.image.data for b in blocks):
            doc.warnings.append(f"minerU 返回了 {len(images)} 张图片，但一张都没能配回正文引用"
                                f"（图片位置只留图注文字）")
        doc.blocks = blocks
        doc.meta = {"pages": 0, "chars": len(md_text),
                   "images": sum(1 for b in blocks if b.is_media)}
        return doc

    # ==================== HTTP ====================

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg['api_key']}"} if self.cfg["api_key"] else {}

    def _url(self, path: str) -> str:
        return f"{self.cfg['base_url']}{self.cfg['path_prefix']}{path}"

    async def _request_parse(self, raw: bytes, name: str, doc: ParsedDocument) -> dict:
        """先异步任务、失败退化同步口，返回结果 JSON。"""
        timeout = float(self.cfg["timeout"])
        form = {
            "backend": self.cfg["backend"],
            "return_md": "true",
            # 必须让服务把图片字节一并回来（base64 放在 images 字典里）：
            # 只给 markdown 正文时，图片位置就只剩一个 <!-- image --> 占位，字节也传不上存储
            "return_images": "true",
            "start_page_id": "0",
            "language": "ch",
        }
        files = {"files": (name, raw, "application/octet-stream")}
        try:
            async with self._client(timeout=timeout) as client:
                try:
                    resp = await client.post(self._url(PATH_TASKS), data=form, files=files,
                                             headers=self._headers())
                except (httpx.HTTPError, OSError) as e:
                    raise ParseError(f"minerU 服务不可达（{self.cfg['base_url']}）: {e}") from e
                if resp.status_code in (404, 405, 501):
                    doc.warnings.append("minerU 未提供异步任务口，已改用同步解析接口")
                    return await self._sync_parse(client, name, raw, form, doc)
                if resp.status_code >= 400:
                    raise ParseError(f"minerU 提交任务失败 HTTP {resp.status_code}: "
                                     f"{resp.text[:200]}")
                task_id = str((resp.json() or {}).get("task_id")
                              or (resp.json() or {}).get("id") or "")
                if not task_id:
                    raise ParseError(f"minerU 未返回 task_id: {str(resp.text)[:200]}")
                return await self._wait_result(client, task_id, doc)
        except ParseError:
            raise
        except ValueError as e:
            raise ParseError(f"minerU 响应不是合法 JSON: {e}") from e

    async def _sync_parse(self, client, name: str, raw: bytes, form: dict,
                          doc: ParsedDocument) -> dict:
        """同步解析口：一次请求拿到结果（受 timeout 约束，大文件容易超时）。"""
        files = {"files": (name, raw, "application/octet-stream")}
        resp = await client.post(self._url(PATH_FILE_PARSE), data=form, files=files,
                                 headers=self._headers())
        if resp.status_code >= 400:
            raise ParseError(f"minerU 同步解析失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json() or {}

    async def _wait_result(self, client, task_id: str, doc: ParsedDocument) -> dict:
        """轮询任务状态直到 completed/failed（间隔与上限都来自 Nacos mineru 段）。"""
        interval = max(1, int(self.cfg["poll_interval"]))
        deadline = max(10.0, float(self.cfg["timeout"]))
        waited = 0.0
        path = self._url(PATH_TASKS) + f"/{task_id}"
        while waited < deadline:
            resp = await client.get(path, headers=self._headers())
            if resp.status_code >= 400:
                raise ParseError(f"minerU 查询任务失败 HTTP {resp.status_code}: "
                                 f"{resp.text[:200]}")
            state = str((resp.json() or {}).get("status") or "").lower()
            if state in C.MINERU_STATE_DONE:
                r = await client.get(self._url(PATH_TASK_RESULT.format(task_id=task_id)),
                                     headers=self._headers())
                if r.status_code >= 400:
                    raise ParseError(f"minerU 取结果失败 HTTP {r.status_code}: {r.text[:200]}")
                return r.json() or {}
            if state in C.MINERU_STATE_FAILED:
                err = str((resp.json() or {}).get("error") or "")[:200]
                raise ParseError(f"minerU 解析任务失败: {err}")
            await asyncio.sleep(interval)
            waited += interval
        raise ParseError(f"minerU 等待解析结果超时（>{int(deadline)} 秒），"
                         f"可调大 Nacos 的 mineru.timeout 或拆分文档")

    @staticmethod
    def _client(timeout: float):
        """优先复用全局 httpx 连接池；未初始化时临时建一个（worker 里 httpx_pool 已 init）。"""
        from common.common_httpx.httpx import httpx_pool
        client = httpx_pool.client
        if client is not None:
            return _PooledClient(client, timeout)
        return httpx.AsyncClient(timeout=timeout, trust_env=False)

    # ==================== 结果归一 ====================

    def _extract_result(self, payload: dict, name: str,
                        doc: ParsedDocument) -> tuple[str, dict[str, str]]:
        """响应 → (markdown 正文, 图片 base64 字典)。

        minerU 的返回形态：``{"results": {"<文件名去扩展名>": {"md_content": ...}}}``；
        单文件请求时 results 里只有一项，取第一项比按文件名匹配可靠（服务会改写扩展名）。
        """
        if not isinstance(payload, dict):
            raise ParseError("minerU 响应结构异常（不是对象）")
        results = payload.get("results")
        entry: Any = None
        if isinstance(results, dict) and results:
            entry = results.get(name) or next(iter(results.values()))
        elif isinstance(results, list) and results:
            entry = results[0]
        elif payload.get("md_content"):
            entry = payload
        if not isinstance(entry, dict):
            raise ParseError(f"minerU 响应里没有解析结果: {str(payload)[:200]}")
        md_text = str(entry.get("md_content") or entry.get("markdown") or "")
        raw_images = entry.get("images") or {}
        images = {str(k): str(v) for k, v in raw_images.items()} if isinstance(raw_images,
                                                                              dict) else {}
        if not md_text and isinstance(results, dict) and len(results) > 1:
            doc.warnings.append("minerU 一次返回了多个文件的结果，已取第一个")
        return md_text, images

    def _attach_images(self, blocks: list[ParsedBlock], images: dict[str, str],
                       doc: ParsedDocument) -> list[ParsedBlock]:
        """把 ``![](images/x.jpg)`` 引用换成可上传的字节（service 给了 embedded 才有数据）。"""
        if not images:
            return blocks
        decoded: dict[str, bytes] = {}
        for key, value in images.items():
            body = value.split(",", 1)[-1] if value.startswith("data:") else value
            try:
                decoded[key.rsplit("/", 1)[-1]] = base64.b64decode(body)
            except (ValueError, TypeError):
                continue
        for b in blocks:
            if b.type != C.BLOCK_IMAGE or b.image is None:
                continue
            ref = b.image
            # 靠正文里的那个引用串配对（![](images/x.jpg) 的 alt 基本是空的，
            # 拿 alt 当键只有单图文档能偶然对上，多图文档会全部配丢
            key = (ref.src or ref.alt or "").replace("\\", "/").rsplit("/", 1)[-1]
            data = decoded.get(key)
            if data is None and len(decoded) == 1:
                data = next(iter(decoded.values()))      # 单图文档直接配对，容忍名字不齐
            if data is not None:
                ref.data = data
                ref.mime = ref.mime or "image/png"
        return blocks
