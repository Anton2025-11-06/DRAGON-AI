import asyncio
import builtins
import inspect
import time

from sqlalchemy import text

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

# 函数执行超时（秒），防止死循环拖垮服务
FUNC_TIMEOUT = 10
# 受限执行环境：去掉危险内建（文件IO/动态执行/输入），仅保留纯计算能力
_UNSAFE_BUILTINS = {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint", "exit", "quit"}
_SAFE_BUILTINS = {k: v for k, v in vars(builtins).items() if k not in _UNSAFE_BUILTINS}


class ToolService:
    """动态 Python 函数工具：CRUD + 受限环境执行测试"""

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
                               parameters_schema, status, created_by,
                               DATE_FORMAT(create_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS create_time,
                               DATE_FORMAT(update_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS update_time
                        FROM tb_tool {where}
                        ORDER BY id DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": page_size, "offset": (page - 1) * page_size})).all()
            items = [{
                "id": r[0], "name": r[1], "description": r[2],
                "function_code": r[3], "parameters_schema": r[4],
                "status": bool(r[5]), "created_by": r[6],
                "create_time": r[7], "update_time": r[8],
            } for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def get_by_id(id_: int):
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                text("""SELECT id, name, description, function_code, parameters_schema, status
                        FROM tb_tool WHERE id = :id"""), {"id": id_})).first()
            return row

    # ==================== 新增 / 更新 / 删除 ====================
    @staticmethod
    async def create(name: str, function_code: str, description: str = None,
                     parameters_schema: str = None, status: bool = True,
                     created_by: int = 0) -> int:
        # 入库前先编译校验，避免无效源码入库
        ToolService._compile_code(function_code)
        async with mysql_client.get_session() as session:
            result = await session.execute(text(
                """INSERT INTO tb_tool (name, description, function_code, parameters_schema, status, created_by)
                   VALUES (:name, :description, :function_code, :parameters_schema, :status, :created_by)"""),
                {"name": name, "description": description, "function_code": function_code,
                 "parameters_schema": parameters_schema, "status": 1 if status else 0,
                 "created_by": created_by})
            await session.commit()
            log.info(f"Tool created: {name}")
            return result.lastrowid

    @staticmethod
    async def update(id_: int, name: str = None, function_code: str = None,
                     description: str = None, parameters_schema: str = None,
                     status: bool = None) -> bool:
        if function_code:
            ToolService._compile_code(function_code)
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
            if not fields:
                return True
            await session.execute(
                text(f"UPDATE tb_tool SET {', '.join(fields)} WHERE id = :id"), params)
            await session.commit()
            log.info(f"Tool updated: id={id_}")
            return True

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

    # ==================== 执行测试 ====================
    @staticmethod
    def _compile_code(source: str):
        """编译校验源码：语法错误直接抛 ValueError（带行号）"""
        if not source or not source.strip():
            raise ValueError("函数源码不能为空")
        try:
            compile(source, "<tool>", "exec")
        except SyntaxError as e:
            raise ValueError(f"源码语法错误: 第{e.lineno}行 {e.msg}")

    @staticmethod
    async def test(id_: int, parameters: dict = None) -> dict:
        """
        在执行受限环境中运行工具函数并返回结果：
        - 约定的入口：函数名为 run（未找到则取源码中第一个函数）
        - 参数以关键字方式传入（parameters 为对象时），标量参数直接传入
        - 线程池中执行 + asyncio 超时，防止死循环
        """
        row = await ToolService.get_by_id(id_)
        if not row:
            raise ValueError("工具不存在")
        if not row[5]:
            raise ValueError("工具已停用，请先启用后再测试")

        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, ToolService._execute, row[3], parameters or {}),
                timeout=FUNC_TIMEOUT)
            return {"success": True, "result": result,
                    "duration_ms": int((time.perf_counter() - start) * 1000)}
        except asyncio.TimeoutError:
            log.warning(f"Tool execution timeout: id={id_}")
            return {"success": False, "error": f"执行超时（>{FUNC_TIMEOUT}s），请检查函数是否死循环",
                    "duration_ms": int((time.perf_counter() - start) * 1000)}
        except Exception as e:
            log.warning(f"Tool execution failed: id={id_}, {str(e)}")
            return {"success": False, "error": f"{type(e).__name__}: {e}",
                    "duration_ms": int((time.perf_counter() - start) * 1000)}

    @staticmethod
    def _execute(source: str, parameters: dict):
        """在线程中执行用户函数（同步部分），返回 JSON 可序列化结果"""
        namespace = {"__builtins__": _SAFE_BUILTINS}
        exec(compile(source, "<tool>", "exec"), namespace)
        # 找到入口函数：优先 run，否则取源码中第一个用户函数（以全局命名空间归属识别）
        fn = namespace.get("run")
        if fn is None:
            for name, obj in namespace.items():
                if name.startswith("_"):
                    continue
                if inspect.isfunction(obj) and getattr(obj, "__globals__", None) is namespace:
                    fn = obj
                    break
        if fn is None:
            raise ValueError("源码中未定义任何函数，请定义 run 或任意函数")

        if isinstance(parameters, dict) and parameters:
            return fn(**parameters)
        return fn(parameters)