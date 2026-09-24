# -*- coding: utf-8 -*-
"""动态 Python 函数工具（tb_tool）：CRUD + 下拉清单 + 沙箱执行。

执行环境与工作流「代码执行」节点共用一份实现（workflow_engine/py_sandbox.py）：
入口方法（main → run → 最后定义的顶层函数）、白名单 builtins、危险模块拦截、
超时口径全部一致 —— 能在代码节点里跑的代码，登记成工具后同样能跑，
页面测试结果与节点实际行为也不会有两套口径。

parameters_schema 存 JSON：
    {"parameters": [{"name", "type", "required", "description", "default"}]}
这是工具参数的「定义」（工具页表单据此渲染）；工作流工具节点里配的是「取值」
（引用上游变量或自定义值），节点表单按本定义渲染绑定行，未绑定项回落 default。
"""
import asyncio
import json
import time
from typing import Any, Callable, Optional

from sqlalchemy import delete, func, select, update

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import build_data_scope_filter
from service.service_workflow.models.agent_entity import Tool
from service.service_workflow.workflow_engine import py_sandbox

DEFAULT_TIMEOUT_MS = py_sandbox.DEFAULT_TIMEOUT_MS
# 超时下限：填 0/负数会让 wait_for 立即取消，用户只会看到一条莫名的超时
MIN_TIMEOUT_MS = 100


class ToolService:
    """动态 Python 函数工具：CRUD + options + 受限沙箱执行（测试 / 按定义测试 / 节点调用）"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10,
                   name: str = None, status: int = None, login_user: dict = None) -> dict:
        async with mysql_client.get_session() as session:
            conds = []
            if name:
                conds.append(Tool.name.like(f"%{name}%"))
            if status is not None:
                conds.append(Tool.status == status)
            # 数据权限：非管理员仅可见本人创建或可见部门内创建的工具
            scope_cond = build_data_scope_filter(login_user, Tool.created_by) if login_user else None
            if scope_cond is not None:
                conds.append(scope_cond)
            total = (await session.execute(
                select(func.count()).select_from(Tool).where(*conds))).scalar()
            rows = (await session.execute(
                select(Tool).where(*conds)
                .order_by(Tool.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [{
                "id": r.id, "name": r.name, "description": r.description,
                # 列表页只回源码预览（截断 200 字），完整代码走 detail
                "function_code": (r.function_code or "")[:200],
                "parameters_schema": r.parameters_schema,
                "status": bool(r.status), "timeout": r.timeout, "created_by": r.created_by,
                "create_time": ToolService._fmt_time(r.create_time),
                "update_time": ToolService._fmt_time(r.update_time),
            } for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    def _fmt_time(value) -> Optional[str]:
        """DATETIME → 前端展示字符串（对齐旧 SQL 的 DATE_FORMAT 口径）。"""
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None

    @staticmethod
    async def get_by_id(id_: int) -> Optional[Tool]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(Tool).where(Tool.id == id_))).scalar_one_or_none()

    @staticmethod
    def _to_dict(row: Tool) -> dict:
        """get_by_id 行 → 字典（列名只在这一处维护）"""
        return {
            "id": row.id, "name": row.name, "description": row.description,
            "function_code": row.function_code, "parameters_schema": row.parameters_schema,
            "status": bool(row.status), "timeout": row.timeout or DEFAULT_TIMEOUT_MS,
        }

    @staticmethod
    async def detail(id_: int) -> dict | None:
        row = await ToolService.get_by_id(id_)
        return ToolService._to_dict(row) if row else None

    @staticmethod
    async def options() -> list[dict]:
        """启用中工具的下拉清单（含参数定义），供工作流工具节点选工具并渲染参数绑定行。

        一次性把参数定义带回去：节点表单若只给工具名，用户还得逐个手填参数名，
        「选择工具后自动列出入参」这个基本体验就断了。
        """
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(Tool).where(Tool.status == 1)
                .order_by(Tool.id.desc()))).scalars().all()
        return [{
            "id": r.id, "name": r.name, "description": r.description,
            "timeout": r.timeout or DEFAULT_TIMEOUT_MS,
            "parameters": ToolService.parse_parameters(r.parameters_schema),
        } for r in rows]

    # ==================== 新增 / 更新 / 删除 ====================
    @staticmethod
    async def create(name: str, function_code: str, description: str = None,
                     parameters_schema: str = None, status: bool = True,
                     created_by: int = 0, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        # 入库前先编译校验，避免无效源码入库
        py_sandbox.compile_check(function_code)
        async with mysql_client.get_session() as session:
            tool = Tool(name=name, description=description, function_code=function_code,
                        parameters_schema=parameters_schema, status=1 if status else 0,
                        timeout=ToolService.normalize_timeout(timeout), created_by=created_by)
            session.add(tool)
            await session.flush()
            new_id = tool.id
            await session.commit()
            log.info(f"Tool created: {name}")
            return new_id

    @staticmethod
    async def update(id_: int, name: str = None, function_code: str = None,
                     description: str = None, parameters_schema: str = None,
                     status: bool = None, timeout: int = None) -> bool:
        if function_code:
            py_sandbox.compile_check(function_code)
        async with mysql_client.get_session() as session:
            values: dict = {}
            if name is not None:
                values["name"] = name
            if function_code is not None:
                values["function_code"] = function_code
            if description is not None:
                values["description"] = description
            if parameters_schema is not None:
                values["parameters_schema"] = parameters_schema
            if status is not None:
                values["status"] = 1 if status else 0
            if timeout is not None:
                values["timeout"] = ToolService.normalize_timeout(timeout)
            if not values:
                return True
            await session.execute(
                update(Tool).where(Tool.id == id_).values(**values))
            await session.commit()
            log.info(f"Tool updated: id={id_}")
            return True

    @staticmethod
    def normalize_timeout(timeout) -> int:
        """超时列/请求参数兜底：非法值取默认，过小值抬到下限"""
        try:
            value = int(timeout)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_MS
        return max(value, MIN_TIMEOUT_MS)

    @staticmethod
    async def delete(id_: int) -> bool:
        async with mysql_client.get_session() as session:
            result = await session.execute(delete(Tool).where(Tool.id == id_))
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> bool:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                update(Tool).where(Tool.id == id_).values(status=1 if status else 0))
            await session.commit()
            return result.rowcount > 0

    # ==================== 参数定义 ====================
    @staticmethod
    def parse_parameters(raw: Any) -> list[dict]:
        """parameters_schema JSON → 参数定义数组；解析不了就当无参数，不让列表页 500。

        兼容三种历史写法：{"parameters": [...]}、{"inputs": [...]}、裸数组 [...]，
        以及最早期的 {"example": {...}} / {"a": 1} 示例对象（按键名反推参数）。
        """
        if not raw:
            return []
        data = raw
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                return []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("parameters") or data.get("inputs")
            if not items:
                example = data.get("example") if isinstance(data.get("example"), dict) else data
                items = [{"default": v, "name": k} for k, v in example.items()]
        else:
            return []
        result = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            default = item.get("default")
            if default is None:
                default = item.get("value")
            result.append({
                "default": default,
                "description": item.get("description") or "",
                "name": name,
                "required": bool(item.get("required")),
                "type": item.get("type") or "string",
            })
        return result

    # ==================== 执行 ====================
    @staticmethod
    async def call(code: str, parameters: dict = None, timeout_ms: int = None,
                   entry: str = "main") -> Any:
        """在受限沙箱内执行工具代码，返回入口函数结果；失败抛 ValueError（供节点直接上抛）。"""
        py_sandbox.compile_check(code)
        timeout_ms = ToolService.normalize_timeout(timeout_ms)
        try:
            result = await py_sandbox.run_code(
                code, parameters or {}, entry=entry, timeout_ms=timeout_ms)
        except asyncio.TimeoutError as e:
            raise ValueError(f"执行超时（>{timeout_ms}ms），请检查函数是否死循环") from e
        py_sandbox.validate_output(result)
        return result

    @staticmethod
    async def _run_guarded(code: str, parameters: dict, timeout_ms: int) -> dict:
        """页面测试统一出口：把异常摊成 {success, result/error, duration_ms}，不抛错。"""
        start = time.perf_counter()
        try:
            result = await ToolService.call(code, parameters, timeout_ms)
            return {"success": True, "result": ToolService.json_safe(result),
                    "duration_ms": int((time.perf_counter() - start) * 1000)}
        except Exception as e:  # noqa: BLE001
            log.warning("Tool execution failed: {}", str(e))
            return {"success": False, "error": f"{type(e).__name__}: {e}",
                    "duration_ms": int((time.perf_counter() - start) * 1000)}

    @staticmethod
    def json_safe(value):
        """结果 JSON 可序列化兜底：set/bytes 等不可序列化时转为字符串，避免接口 500"""
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    async def test(id_: int, parameters: dict = None) -> dict:
        """按已保存的工具测试（工具页「运行测试」）。"""
        row = await ToolService.get_by_id(id_)
        if not row:
            raise ValueError("工具不存在")
        if not row.status:
            raise ValueError("工具已停用，请先启用后再测试")
        return await ToolService._run_guarded(row.function_code, parameters or {}, row.timeout)

    @staticmethod
    async def test_definition(function_code: str, parameters: dict = None,
                              timeout_ms: int = None) -> dict:
        """按定义测试：用表单里还没保存的代码 + 参数直接跑一次（新增/编辑弹窗用）。"""
        return await ToolService._run_guarded(function_code, parameters or {}, timeout_ms)

    @staticmethod
    async def load_for_node(tool_id: int = None, name: str = None) -> dict:
        """按 ID（优先）或名称取启用中的工具，供工作流工具节点执行。"""
        async with mysql_client.get_session() as session:
            if tool_id:
                stmt = select(Tool).where(Tool.id == tool_id)
            else:
                stmt = select(Tool).where(Tool.name == name).order_by(Tool.id.desc()).limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            raise ValueError(f"工具不存在: {name or tool_id}")
        if not row.status:
            raise ValueError(f"工具已停用: {row.name}")
        return ToolService._to_dict(row)

    @staticmethod
    async def run_for_node(tool: dict, inputs: list, resolve_ref: Callable[[str], Any],
                           timeout_ms: int = None) -> Any:
        """工具节点执行入口：参数绑定行 → kwargs → 沙箱执行，返回入口函数结果。

        绑定行为空的参数先用工具定义里的 default 兜底（定义即「默认取值」），
        仍取不到且必填才报错 —— 报错信息里带上工具名，便于多工具工作流定位节点。
        """
        bound = {str(item.get("name")) for item in inputs or [] if item.get("name")}
        merged = list(inputs or [])
        for define in ToolService.parse_parameters(tool.get("parameters_schema")):
            if define["name"] in bound or define.get("default") is None:
                continue
            merged.append({"name": define["name"], "type": define["type"],
                           "required": False, "sourceType": "CONSTANT",
                           "value": define["default"]})
        kwargs = py_sandbox.resolve_kwargs(merged, resolve_ref)
        try:
            return await ToolService.call(
                tool["function_code"], kwargs,
                timeout_ms or tool.get("timeout") or DEFAULT_TIMEOUT_MS)
        except ValueError as e:
            raise ValueError(f"工具「{tool['name']}」执行失败: {e}") from e
