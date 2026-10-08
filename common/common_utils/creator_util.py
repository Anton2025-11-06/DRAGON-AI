# -*- coding: utf-8 -*-
"""创建人/归属部门展示名补全：列表行上的 `created_by`（用户ID）→ `creatorName`（姓名），
`owner_dept_id`（部门ID）→ `deptName`（部门名）。

为什么单独成模块
----------------
数据权限（resource_guard）判的是「能不能做」，但被置灰的按钮得告诉用户「该找谁要授权」，
所以每个资源列表都要在行上带创建人姓名。这件事在 workflow / skill / tool / mcp / 知识库
五个列表里一模一样，各自写一遍就会各自漏写、各自按行数打查询。

口径
----
- 一次批量查询补一整页（`IN` 查 tb_user），绝不按行查库；
- 展示名优先 real_name，没有则 username，都没有（用户被物理删）留空串——
  前端本来就是 `v-if="item.creatorName"`，空串即不显示，比显示「未知用户」诚实；
- 调用方传了 session 就借它的连接（同一请求同一事务，避免可见性对不齐）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_mysql.mysql import mysql_client

# 行字典里默认的「归属人用户ID」键（业务表列名风格）与输出的展示名键
DEFAULT_ID_KEY = "created_by"
DEFAULT_NAME_KEY = "creatorName"
# 部门同理：tb_knowledge_base.owner_dept_id 存的是 id，页面要的是名字
DEFAULT_DEPT_ID_KEY = "owner_dept_id"
DEFAULT_DEPT_NAME_KEY = "deptName"


def _as_int(value: Any) -> int:
    """宽松取整：None/空串/脏值一律 0（0 表示历史存量行没有归属人）。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _display_name(real_name: Any, username: Any) -> str:
    """展示名：真名优先（企业内部系统里同事认的是真名），回落登录账号。"""
    return str(real_name or "").strip() or str(username or "").strip()


async def user_names(ids: Iterable[Any], *,
                     session: Optional[AsyncSession] = None) -> dict[int, str]:
    """批量取 {用户ID: 展示名}；查不到的 id 不出现在结果里（由调用方按缺省处理）。"""
    wanted = sorted({i for i in (_as_int(x) for x in (ids or [])) if i})
    if not wanted:
        return {}
    from common.common_entity.user_entity import User  # 延迟导入避免循环依赖

    async with _session_scope(session) as s:
        rows = (await s.execute(
            select(User.user_id, User.real_name, User.username)
            .where(User.user_id.in_(wanted)))).all()
    return {_as_int(r[0]): _display_name(r[1], r[2]) for r in rows}


async def user_name(user_id: Any, *, session: Optional[AsyncSession] = None) -> str:
    """单个用户的展示名（403 文案、授权记录里的人名用）；查不到返回空串。"""
    uid = _as_int(user_id)
    if not uid:
        return ""
    return (await user_names([uid], session=session)).get(uid, "")


async def attach_creator(items: list[dict], *, id_key: str = DEFAULT_ID_KEY,
                         name_key: str = DEFAULT_NAME_KEY,
                         session: Optional[AsyncSession] = None) -> list[dict]:
    """就地给每行写 `creatorName`，返回同一个 items（便于链式调用与单测断言）。

    :param id_key: 行内存着「归属人用户ID」的键名。各列表的键名风格不统一：
        ORM 手拼的字典是 created_by，Pydantic dump 出来的是 createdBy，
        所以由调用方指定而不是让这里猜两种都试。
    :param name_key: 输出键名，前端统一按 creatorName 读（现有页面已依赖）。
    """
    if not items:
        return items
    mapping = await user_names([it.get(id_key) for it in items], session=session)
    for it in items:
        if not isinstance(it, dict):
            continue
        it[name_key] = mapping.get(_as_int(it.get(id_key)), "")
    return items


async def dept_names(ids: Iterable[Any], *,
                     session: Optional[AsyncSession] = None) -> dict[int, str]:
    """批量取 {部门ID: 部门名}；查不到的 id 不出现在结果里（部门被删就是空串）。"""
    wanted = sorted({i for i in (_as_int(x) for x in (ids or [])) if i})
    if not wanted:
        return {}
    from common.common_entity.rbac_entity import Dept  # 延迟导入避免循环依赖

    async with _session_scope(session) as s:
        rows = (await s.execute(
            select(Dept.dept_id, Dept.dept_name)
            .where(Dept.dept_id.in_(wanted)))).all()
    return {_as_int(r[0]): str(r[1] or "").strip() for r in rows if str(r[1] or "").strip()}


async def dept_name(dept_id: Any, *, session: Optional[AsyncSession] = None) -> str:
    """单个部门名（403 文案与详情页用）；查不到返回空串。"""
    did = _as_int(dept_id)
    if not did:
        return ""
    return (await dept_names([did], session=session)).get(did, "")


async def attach_dept(items: list[dict], *, id_key: str = DEFAULT_DEPT_ID_KEY,
                      name_key: str = DEFAULT_DEPT_NAME_KEY,
                      session: Optional[AsyncSession] = None) -> list[dict]:
    """就地给每行写部门名，返回同一个 items（与 attach_creator 同一语义与同一批查口径）。

    归属人与归属部门总是成对出现在列表上，所以两个函数共用一个 `_session_scope`：
    调用方传同一个 session 过来，一页最多两次 `IN` 查询，不按行打库。
    """
    if not items:
        return items
    mapping = await dept_names([it.get(id_key) for it in items], session=session)
    for it in items:
        if not isinstance(it, dict):
            continue
        it[name_key] = mapping.get(_as_int(it.get(id_key)), "")
    return items


__all__ = ["attach_creator", "user_name", "user_names",
           "attach_dept", "dept_name", "dept_names",
           "DEFAULT_ID_KEY", "DEFAULT_NAME_KEY",
           "DEFAULT_DEPT_ID_KEY", "DEFAULT_DEPT_NAME_KEY"]


@asynccontextmanager
async def _session_scope(session: Optional[AsyncSession] = None):
    """有 session 就借调用方的（不提交不关闭），没有就自己开一个短会话。

    与 common_permission.resource_guard.maybe_session 同一语义：列表服务大多已经
    在一个 `async with mysql_client.get_session()` 里，这里再开一个连接就会出现
    同一请求两个事务、刚提交的数据查不到的问题。不直接 import 那边的函数，
    是因为 utils 层不能反向依赖 permission 层（permission 将来还要用工具层的名字补全）。
    """
    if session is not None:
        yield session
        return
    async with mysql_client.get_session() as s:
        yield s

