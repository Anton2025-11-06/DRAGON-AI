from sqlalchemy import select

from common.common_constants.constant import PREFIX_LOGIN
from common.common_entity.rbac_entity import Dept
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_redis.redis import client


class OnlineService:
    """在线用户管理：基于 Redis 登录态（login_{jti}）实时统计在线用户并支持强制踢出"""

    @staticmethod
    async def list_online(page: int = 1, page_size: int = 10, keyword: str = None) -> dict:
        """
        分页查询在线用户列表（全量扫描后内存过滤 + 分页）。
        登录态 payload 由登录服务写入（user_id/username/real_name/dept_id/dept_name/roles/jti/iat/exp）。
        keyword 支持账号/真实姓名/部门名称/角色的模糊匹配。
        """
        items = []
        async for key in client.client.scan_iter(match=PREFIX_LOGIN + "*", count=100):
            payload = await client.get(key, to_dict=True)
            if not isinstance(payload, dict):
                continue
            jti = key[len(PREFIX_LOGIN):]
            items.append({
                "jti": jti,
                "user_id": payload.get("user_id"),
                "username": payload.get("username", ""),
                "real_name": payload.get("real_name", ""),
                "dept_id": payload.get("dept_id", 0),
                "dept_name": payload.get("dept_name", ""),
                "roles": payload.get("roles", []) or [],
                # 登录/过期时间戳（登录服务注入的整数秒），旧登录态可能缺失
                "login_at": payload.get("iat"),
                "expire_at": payload.get("exp"),
            })
        # 登录时间新→旧排序
        items.sort(key=lambda x: x.get("login_at") or 0, reverse=True)
        # 关键词模糊匹配：账号/真实姓名/部门名称/角色（不区分大小写）
        if keyword and keyword.strip():
            kw = keyword.strip().lower()
            items = [
                it for it in items
                if kw in (it["username"] or "").lower()
                or kw in (it["real_name"] or "").lower()
                or kw in (it["dept_name"] or "").lower()
                or any(kw in str(r).lower() for r in it["roles"])
            ]
        total = len(items)
        start = (page - 1) * page_size
        return {"total": total, "items": items[start:start + page_size]}

    @staticmethod
    async def kick(jti: str) -> bool:
        """踢出指定登录态（删除 Redis key 后该 token 立即失效）"""
        key = PREFIX_LOGIN + jti
        if not await client.exists(key):
            return False
        await client.delete(key)
        log.info(f"Kick online user by admin, jti={jti}")
        return True