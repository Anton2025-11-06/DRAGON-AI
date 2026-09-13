# -*- coding: utf-8 -*-
"""文件服务上传/下载路由直调验证（不启动完整 bootstrap，走本地存储后端）。

验证计划风险点：file_id -> ref 的 sidecar 索引映射、下载读回字节一致性、
路径穿越防护、非法/缺失 file_id 的兜底。
"""
import asyncio
import io
import os
import sys
import types

# 保证以项目根为 sys.path[0]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 索引落到临时目录，避免污染真实 data/workflow_files
_TMP = os.path.join(ROOT, "site", "_file_idx_tmp")
os.environ["WORKFLOW_FILE_DIR"] = _TMP

from fastapi import UploadFile  # noqa: E402
from starlette.datastructures import Headers  # noqa: E402

from service.service_file.routers import file_router  # noqa: E402


class _FakeURL:
    scheme = "http"
    netloc = "127.0.0.1:9007"


class _FakeRequest:
    url = _FakeURL()
    headers = Headers({"host": "127.0.0.1:18000"})


def _make_upload(data: bytes, name: str, ctype: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data), headers=Headers({
        "content-type": ctype,
    }))


async def main():
    req = _FakeRequest()
    payload = "你好，文件服务 / hello file service".encode("utf-8")

    # 1) 上传
    up = await file_router.upload_file(req, _make_upload(payload, "说明.txt", "text/plain"))
    assert up["code"] == 200, f"upload failed: {up}"
    file_id = up["data"]["fileId"]
    url = up["data"]["url"]
    print("upload fileId:", file_id)
    print("upload url:", url)
    assert url.endswith(f"/api/file/file/download/{file_id}"), f"bad url: {url}"
    assert "127.0.0.1:18000" in url, "public_base 未回退到请求 Host"

    # 2) 下载
    resp = await file_router.download_file(file_id)
    assert resp.status_code == 200, f"download status: {resp.status_code}"
    assert resp.body == payload, "下载字节与上传不一致"
    print("download OK, bytes match:", len(resp.body))

    # 3) 缺失 file_id -> 404
    miss = await file_router.download_file("deadbeef" * 4)
    assert isinstance(miss, dict) and miss["code"] == 404, f"expect 404, got {miss}"
    print("missing file_id -> 404 OK")

    # 4) 路径穿越 -> 400
    bad = await file_router.download_file("../../etc/passwd")
    assert isinstance(bad, dict) and bad["code"] == 400, f"expect 400, got {bad}"
    print("path traversal -> 400 OK")

    # 5) 空文件 -> 400
    empty = await file_router.upload_file(req, _make_upload(b"", "empty.bin", "application/octet-stream"))
    assert isinstance(empty, dict) and empty["code"] == 400, f"expect 400, got {empty}"
    print("empty file -> 400 OK")

    print("FILE_ROUNDTRIP_ALL_OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # 清理临时索引
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
