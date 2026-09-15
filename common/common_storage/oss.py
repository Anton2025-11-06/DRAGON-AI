# -*- coding: utf-8 -*-
"""阿里云 OSS 存储后端：官方 Python SDK V2 的**异步客户端**（oss.aio.AsyncClient，基于 aiohttp）。

对象键 = {path_prefix}{文件名}，文件名即业务唯一标识（{uuid}_{原始名}），与本地后端契约一致，
换后端业务侧无感。只搬字节，不在本地留任何副本。

凭证来源：Nacos yml `storage:` 段的 access_key / secret_key（与 mysql、redis 口令同级），
环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET 仅作兜底（优先级见 __init__.py）。

匿名可访问 URL：直接给 V4 预签名地址（私有桶可用，签名自带有效期 storage.url_expires，
上限 7 天），不需要把桶开成公共读，也不依赖业务模块暴露下载地址。
"""
from __future__ import annotations

import datetime
from typing import Optional

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2 import aio  # 顶层包不会自动导出子包，需显式 import
from alibabacloud_oss_v2.exceptions import (
    OperationError, ResponseNotReadError, ServiceError,
)

from common.common_log.log_init import log
from common.common_storage.base import (
    DEFAULT_URL_EXPIRES, StorageBackend, check_name,
)

# V4 预签名 URL 的最长有效期（7 天），超出会被服务端拒绝
MAX_PRESIGN_EXPIRES = 7 * 24 * 3600

# 判定「对象不存在」的错误码（HeadObject 部分场景只返回 404 不带 code）
_NOT_FOUND_CODES = ("NoSuchKey", "NoSuchObject", "NotFound")

# 凭证本身有问题（初始化即失败，避免运行期才报一堆无意义错误）
_FATAL_CRED_CODES = ("InvalidAccessKeyId", "InvalidSecurityToken",
                     "SignatureDoesNotMatch", "AccessKeyIdNotFound")


class OSSStorageBackend(StorageBackend):
    """阿里云 OSS 后端：全链路异步（aiohttp），文件名 → object key 内部补齐前缀。"""

    backend_name = "ali-oss"

    def __init__(self) -> None:
        super().__init__(url_expires=DEFAULT_URL_EXPIRES)
        self._client: Optional[aio.AsyncClient] = None
        self._bucket: str = "dragon-ai"
        self._prefix: str = ""

    async def init(self, region: str, access_key: str, secret_key: str,
                   bucket: str = "dragon-ai", endpoint: Optional[str] = None,
                   security_token: Optional[str] = None, path_prefix: str = "",
                   use_internal_endpoint: bool = False,
                   use_accelerate_endpoint: bool = False,
                   use_cname: bool = False, use_path_style: bool = False,
                   connect_timeout: Optional[int] = None,
                   readwrite_timeout: Optional[int] = None,
                   retry_max_attempts: Optional[int] = None,
                   url_expires: int = DEFAULT_URL_EXPIRES) -> None:
        """建立异步客户端并校验凭证（凭证无效直接抛错，由 bootstrap 阻断启动）。"""
        if not region:
            raise ValueError("OSS 存储后端需要配置 region（如 cn-hangzhou）")
        if not access_key or not secret_key:
            raise ValueError("OSS 存储后端需要配置 access_key / secret_key")

        cfg = oss.config.load_default()
        cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
            access_key_id=access_key,
            access_key_secret=secret_key,
            security_token=security_token or None,
        )
        cfg.region = region
        if endpoint:
            cfg.endpoint = endpoint
        cfg.use_internal_endpoint = bool(use_internal_endpoint)
        cfg.use_accelerate_endpoint = bool(use_accelerate_endpoint)
        cfg.use_cname = bool(use_cname)
        cfg.use_path_style = bool(use_path_style)
        if connect_timeout:
            cfg.connect_timeout = int(connect_timeout)
        if readwrite_timeout:
            cfg.readwrite_timeout = int(readwrite_timeout)
        if retry_max_attempts:
            cfg.retry_max_attempts = int(retry_max_attempts)

        self._client = aio.AsyncClient(cfg)
        self._bucket = bucket or "dragon-ai"
        self._prefix = (path_prefix or "").strip("/")
        if self._prefix:
            self._prefix += "/"
        self.url_expires = int(url_expires or DEFAULT_URL_EXPIRES)
        self._enabled = True

        await self._probe(region)
        log.info(f"OSS storage backend ready(async/aiohttp): region={region} "
                 f"bucket={self._bucket} endpoint={endpoint or '(按 region 构造)'} "
                 f"prefix={self._prefix or '-'} url_expires={self.url_expires}s")

    async def _probe(self, region: str) -> None:
        """一次 GetBucketInfo：凭证/region 配错时启动即报，而不是运行期刷无意义错误。

        注意 SDK 的 is_bucket_exist 走 GetBucketAcl，无权限时会把 AccessDenied 当成
        「存在」，所以这里用 GetBucketInfo 自己判（字段名是 bucket_info，不是 bucket）。
        """
        try:
            info = await self._client.get_bucket_info(
                oss.GetBucketInfoRequest(bucket=self._bucket))
        except Exception as e:  # noqa: BLE001
            if _fatal_credential_error(e):
                raise ValueError(f"OSS 凭证无效，请检查 access_key/secret_key/region: {e}") from e
            # 网络/权限类问题不阻断启动（首次读写会再暴露），只提示排查方向
            log.warning(f"OSS bucket 检查跳过（不阻断启动）: {self._bucket} - {_perm_hint(e) or e}")
            return
        bucket = getattr(info, "bucket_info", None)
        location = (getattr(bucket, "location", None) or "").replace("oss-", "")
        if location and location != region:
            log.warning(f"OSS region 与 bucket 实际所在地域不一致: region={region} "
                        f"bucket {self._bucket} 在 {location}，请修正 storage.region")
        log.info(f"OSS bucket 就绪: {self._bucket} location={location or '-'} "
                 f"extranet={getattr(bucket, 'extranet_endpoint', None) or '-'}")

    async def close(self) -> None:
        """关闭 aiohttp 会话（bootstrap/worker 停机时统一 await，可重复调用）。"""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:  # noqa: BLE001
                log.warning(f"oss client close failed: {e}")
            self._client = None
            self._enabled = False

    # ---------- 抽象实现 ----------

    async def save(self, name: str, data: bytes,
                   content_type: Optional[str] = None) -> None:
        self.ensure_ready()
        await self._client.put_object(
            oss.PutObjectRequest(bucket=self._bucket, key=self._key(name),
                                 body=data, content_type=content_type))

    async def load(self, name: str) -> bytes:
        self.ensure_ready()
        key = self._key(name)
        try:
            resp = await self._client.get_object(oss.GetObjectRequest(
                bucket=self._bucket, key=key))
        except (ServiceError, OperationError) as e:
            raise ValueError(self._err(key, "读取", e)) from e
        body = resp.body
        try:
            # get_object（非流式）已把响应体读进内存；万一拿到未消费的流则退回 read()
            try:
                data = body.content
            except ResponseNotReadError:
                data = await body.read()
            return data if isinstance(data, bytes) else bytes(data)
        finally:
            try:
                await body.close()
            except Exception:  # noqa: BLE001  已消费/已关闭时重复 close 无需冒泡
                pass

    async def exists(self, name: str) -> bool:
        self.ensure_ready()
        key = self._key(name)
        try:
            return await self._client.is_object_exist(self._bucket, key)
        except (ServiceError, OperationError) as e:
            if _is_not_found(e):
                return False
            raise ValueError(self._err(key, "查询", e)) from e

    async def delete(self, name: str) -> bool:
        self.ensure_ready()
        key = self._key(name)
        try:
            await self._client.delete_object(
                oss.DeleteObjectRequest(bucket=self._bucket, key=key))
            return True
        except (ServiceError, OperationError) as e:
            # DeleteObject 对不存在对象是幂等的（不报 404），走到这里说明权限/网络问题
            if _is_not_found(e):
                return False
            raise ValueError(self._err(key, "删除", e)) from e

    async def public_url(self, name: str,
                         base_url: Optional[str] = None) -> tuple[str, int]:
        """V4 预签名 URL（匿名可访问，到期即失效）；base_url 对 OSS 无意义，忽略。"""
        self.ensure_ready()
        seconds = max(1, min(self.url_expires, MAX_PRESIGN_EXPIRES))
        result = await self._client.presign(
            oss.GetObjectRequest(bucket=self._bucket, key=self._key(name)),
            expires=datetime.timedelta(seconds=seconds),
        )
        url = getattr(result, "url", None)
        if not url:
            raise ValueError(f"OSS 预签名失败（无 URL 返回）: {self._key(name)}")
        return url, seconds

    # ---------- 内部工具 ----------

    def _key(self, ref: str) -> str:
        """文件名 → OSS object key（幂等补前缀，历史无前缀记录也能定位）。"""
        ref = (ref or "").lstrip("/")
        if not ref:
            raise ValueError("OSS 文件名为空")
        # 只取末段：容忍调用方传成完整 key 或下载 URL 路径
        name = check_name(ref.rsplit("/", 1)[-1])
        return self._prefix + name

    @staticmethod
    def _err(key: str, action: str, e: Exception) -> str:
        base = f"OSS 对象{action}失败: {key} - {e}"
        hint = _perm_hint(e)
        return f"{base} | 排查建议: {hint}" if hint else base


# ==================== 错误判定 ====================

def _unwrap(err: Exception) -> Exception:
    """OperationError 包装了真实的服务端错误，取内层做判定。"""
    inner = getattr(err, "unwrap", None)
    if callable(inner):
        try:
            target = inner()
            if target is not None:
                return target
        except Exception:  # noqa: BLE001
            pass
    return err


def _service_error(err: Exception) -> Optional[ServiceError]:
    target = _unwrap(err)
    return target if isinstance(target, ServiceError) else None


def _is_not_found(err: Exception) -> bool:
    target = _service_error(err)
    return bool(target) and (target.status_code == 404 or target.code in _NOT_FOUND_CODES)


def _fatal_credential_error(err: Exception) -> bool:
    target = _service_error(err)
    return bool(target) and target.code in _FATAL_CRED_CODES


def _perm_hint(err: Exception) -> Optional[str]:
    """把高频 403 文案翻译成可操作的排查建议（桶名写错/跨账号/RAM 未授权同文案）。"""
    target = _service_error(err)
    if not target or target.status_code != 403:
        return None
    text = f"{target.code} {target.message}".lower()
    if "belong to you" in text or "bucket acl" in text:
        # 实测：同账号但 RAM 用户未授权时 OSS 也回这两句，不能直接当成「跨账号」结论
        return ("该 AccessKey 的 RAM 用户未被授权访问此 bucket（或确为跨账号）："
                "先用 STS GetCallerIdentity 比对 AK 的 AccountId 与桶所属账号，"
                "同账号→挂 AliyunOSSFullAccess 或限定该 bucket 的策略；不同账号→换桶主账号的 AK")
    if "securitytoken" in text or "token has expired" in text:
        return "STS 临时凭证已过期，请刷新 OSS_SESSION_TOKEN"
    return "权限不足：请在 RAM 策略里放开该 bucket 的 oss:ListObjects/GetBucketInfo 与对象读写"
