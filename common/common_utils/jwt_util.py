import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from common.common_constants.constant import TOKEN_EXPIRE

# JWT 签名密钥，生产环境务必通过环境变量 JWT_SECRET 覆盖
JWT_SECRET = os.environ.get("JWT_SECRET", "ai-platform-rbac-jwt-secret-2024")
# 签名算法（HS256）
JWT_ALGORITHM = "HS256"


def create_access_token(payload: dict, expires_seconds: int = TOKEN_EXPIRE) -> str:
    """
    签发 JWT 访问令牌
    :param payload: 自定义载荷（user_id/username/roles/permissions/dept_id 等）
    :param expires_seconds: 有效期（秒），默认 8 小时
    :return: JWT token 字符串（含 exp/jti 标准声明）
    """
    now = datetime.now(timezone.utc)
    token_payload = {
        **payload,
        # 标准声明：签发时间 / 过期时间 / 唯一 ID（用于 Redis 侧踢下线）
        "iat": now,
        "exp": now + timedelta(seconds=expires_seconds),
        # 优先复用调用方注入的 jti（保证与 Redis 登录态键一致），否则自动生成
        "jti": payload.get("jti") or uuid.uuid4().hex,
    }
    return jwt.encode(token_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """
    解析并校验 JWT token，失败（过期/篡改）返回 None
    :param token: JWT 字符串
    :return: 载荷 dict 或 None
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        # 过期/签名错误统一视为无效 token
        return None


def get_token_jti(token: str) -> Optional[str]:
    """提取 token 的 jti（用于 Redis 键关联），非 JWT 格式返回 None"""
    payload = decode_token(token)
    return payload.get("jti") if payload else None