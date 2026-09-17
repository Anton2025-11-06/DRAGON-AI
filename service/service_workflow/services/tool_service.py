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
from typing import Any, Callable

from sqlalchemy import text

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.workflow_engine import py_sandbox

DEFAULT_TIMEOUT_MS = py_sandbox.DEFAULT_TIMEOUT_MS
# 超时下限：填 0/负数会让 wait_for 立即取消，用户只会看到一条莫名的超时
MIN_TIMEOUT_MS = 100


class ToolService:
    """动态 Python 函数工具：CRUD + options + 受限沙箱执行（测试 / 按定义测试 / 节点调用）"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10,
                   name: str = None, status: int = None) -> dict:
        async with mysql_client.get_session() as session:
            where = "WHERE 1=1"
            params = {}
            if name:
                where += " AND name LIKE :name"
                params["name"] = f"%{name}%"
            if status is not None:
                where += " AND status = :status"
                params["status"] = status
            total = (await session.execute(
                text(f"SELECT COUNT(*) FROM tb_tool {where}"), params)).scalar()
            rows = (await session.execute(
                text(f"""SELECT id, name, description, LEFT(function_code, 200) AS function_preview,
                               parameters_schema, status, timeout, created_by,
                               DATE_FORMAT(create_time, '%Y-%m-%d %H:%i:%s') AS create_time,
                               DATE_FORMAT(update_time, '%Y-%m-%d %H:%i:%s') AS update_time
                        FROM tb_tool {where}
                        ORDER BY id DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": page_size, "offset": (page - 1) * page_size})).all()
            items = [{
                "id": r[0], "name": r[1], "description": r[2],
                "function_code": r[3], "parameters_schema": r[4],
                "status": bool(r[5]), "timeout": r[6], "created_by": r[7],
                "create_time": r[8], "update_time": r[9],
            } for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def get_by_id(id_: int):
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                text("""SELECT id, name, description, function_code, parameters_schema,
                               status, timeout
                        FROM tb_tool WHERE id = :id"""), {"id": id_})).first()
            return row

    @staticmethod
    def _to_dict(row) -> dict:
        """get_by_id 行 → 字典（列序只在这一处维护）"""
        return {
            "id": row[0], "name": row[1], "description": row[2],
            "function_code": row[3], "parameters_schema": row[4],
            "status": bool(row[5]), "timeout": row[6] or DEFAULT_TIMEOUT_MS,
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
                text("""SELECT id, name, description, parameters_schema, timeout
                        FROM tb_tool WHERE status = 1 ORDER BY id DESC"""))).all()
        return [{
            "id": r[0], "name": r[1], "description": r[2],
            "timeout": r[4] or DEFAULT_TIMEOUT_MS,
            "parameters": ToolService.parse_parameters(r[3]),
        } for r in rows]

    # ==================== 新增 / 更新 / 删除 ====================
    @staticmethod
    async def create(name: str, function_code: str, description: str = None,
                     parameters_schema: str = None, status: bool = True,
                     created_by: int = 0, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        # 入库前先编译校验，避免无效源码入库
        py_sandbox.compile_check(function_code)
        async with mysql_client.get_session() as session:
            result = await session.execute(text(
                """INSERT INTO tb_tool (name, description, function_code, parameters_schema,
                                        status, timeout, created_by)
                   VALUES (:name, :description, :function_code, :parameters_schema,
                           :status, :timeout, :created_by)"""),
                {"name": name, "description": description, "function_code": function_code,
                 "parameters_schema": parameters_schema, "status": 1 if status else 0,
                 "timeout": ToolService.normalize_timeout(timeout),
                 "created_by": created_by})
            await session.commit()
            log.info(f"Tool created: {name}")
            return result.lastrowid

    @staticmethod
    async def update(id_: int, name: str = None, function_code: str = None,
                     description: str = None, parameters_schema: str = None,
                     status: bool = None, timeout: int = None) -> bool:
        if function_code:
            py_sandbox.compile_check(function_code)
        async with mysql_client.get_session() as session:
            fields, params = [], {"id": id_}
            if name is not None:
                fields.append("name = :name"); params["name"] = name
            if function_code is not None:
                fields.append("function_code = :function_code"); params["function_code"] = function_code
            if description is not None:
                fields.append("description = :description"); params["description"] = description
            if parameters_schema is not None:
                fields.append("parameters_schema = :parameters_schema"); params["parameters_schema"] = parameters_schema
            if status is not None:
                fields.append("status = :status"); params["status"] = 1 if status else 0
            if timeout is not None:
                fields.append("timeout = :timeout")
                params["timeout"] = ToolService.normalize_timeout(timeout)
            if not fields:
                return True
            await session.execute(
                text(f"UPDATE tb_tool SET {', '.join(fields)} WHERE id = :id"), params)
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
            result = await session.execute(
                text("DELETE FROM tb_tool WHERE id = :id"), {"id": id_})
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> bool:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                text("UPDATE tb_tool SET status = :status WHERE id = :id"),
                {"id": id_, "status": 1 if status else 0})
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
        if not row[5]:
            raise ValueError("工具已停用，请先启用后再测试")
        return await ToolService._run_guarded(row[3], parameters or {}, row[6])

    @staticmethod
    async def test_definition(function_code: str, parameters: dict = None,
                              timeout_ms: int = None) -> dict:
        """按定义测试：用表单里还没保存的代码 + 参数直接跑一次（新增/编辑弹窗用）。"""
        return await ToolService._run_guarded(function_code, parameters or {}, timeout_ms)

    @staticmethod
    async def load_for_node(tool_id: int = None, name: str = None) -> dict:
        """按 ID（优先）或名称取启用中的工具，供工作流工具节点执行。"""
        sql = ("SELECT id, name, description, function_code, parameters_schema, status, timeout "
               "FROM tb_tool WHERE id = :id" if tool_id else
               "SELECT id, name, description, function_code, parameters_schema, status, timeout "
               "FROM tb_tool WHERE name = :name ORDER BY id DESC LIMIT 1")
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                text(sql), {"id": tool_id} if tool_id else {"name": name})).first()
        if not row:
            raise ValueError(f"工具不存在: {name or tool_id}")
        if not row[5]:
            raise ValueError(f"工具已停用: {row[1]}")
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
