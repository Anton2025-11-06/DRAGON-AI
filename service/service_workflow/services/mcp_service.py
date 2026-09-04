import ast
import json
import shutil
import time
from datetime import datetime

import httpx
from sqlalchemy import select, update, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

# MCP 连接测试超时（秒）
_PROBE_TIMEOUT = 5.0
# STDIO 进程存活探测时间（秒）
_STDIO_PROBE_KEEP = 2.0


class McpServerService:
    """MCP 服务器连接配置：CRUD + 连通性测试 + 工具探测（SSE/STDIO 两种模式）"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10,
                   name: str = None, status: int = None) -> dict:
        """分页查询 MCP 连接配置"""
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
                text(f"SELECT COUNT(*) FROM tb_mcp_server {where}"), params)).scalar()
            rows = (await session.execute(
                text(f"""SELECT id, name, type, url, command, args, env, status, created_by,
                               create_time, update_time
                        FROM tb_mcp_server {where}
                        ORDER BY id DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": page_size, "offset": (page - 1) * page_size})).all()
            items = [McpServerService._to_dict(r) for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    def _to_dict(row) -> dict:
        """行转字典：status 统一转 bool（前端 dict-switch 使用）"""
        return {
            "id": row[0], "name": row[1], "type": row[2], "url": row[3],
            "command": row[4], "args": row[5], "env": row[6],
            "status": bool(row[7]), "created_by": row[8],
            "create_time": row[9], "update_time": row[10],
        }

    @staticmethod
    async def get_by_id(id_: int):
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                text("""SELECT id, name, type, url, command, args, env, status, created_by
                        FROM tb_mcp_server WHERE id = :id"""), {"id": id_})).first()
            return row

    @staticmethod
    async def batch_tools(server_ids: list) -> list:
        """批量返回工具清单（demo 探测结果缓存于内存，真实连接信息由测试连接接口输出）"""
        return []

    # ==================== 新增 / 更新 / 删除 ====================
    @staticmethod
    async def create(name: str, type_: str, url: str = None, command: str = None,
                     args: str = None, env: str = None, status: bool = True,
                     created_by: int = 0) -> int:
        async with mysql_client.get_session() as session:
            result = await session.execute(text(
                """INSERT INTO tb_mcp_server (name, type, url, command, args, env, status, created_by)
                   VALUES (:name, :type, :url, :command, :args, :env, :status, :created_by)"""),
                {"name": name, "type": type_, "url": url, "command": command,
                 "args": args, "env": env, "status": 1 if status else 0, "created_by": created_by})
            await session.commit()
            log.info(f"McpServer created: {name} ({type_})")
            return result.lastrowid

    @staticmethod
    async def update(id_: int, name: str = None, type_: str = None, url: str = None,
                     command: str = None, args: str = None, env: str = None,
                     status: bool = None) -> bool:
        async with mysql_client.get_session() as session:
            fields, params = [], {"id": id_}
            if name is not None:
                fields.append("name = :name"); params["name"] = name
            if type_ is not None:
                fields.append("type = :type"); params["type"] = type_
            if url is not None:
                fields.append("url = :url"); params["url"] = url
            if command is not None:
                fields.append("command = :command"); params["command"] = command
            if args is not None:
                fields.append("args = :args"); params["args"] = args
            if env is not None:
                fields.append("env = :env"); params["env"] = env
            if status is not None:
                fields.append("status = :status"); params["status"] = 1 if status else 0
            if not fields:
                return True
            await session.execute(
                text(f"UPDATE tb_mcp_server SET {', '.join(fields)} WHERE id = :id"), params)
            await session.commit()
            log.info(f"McpServer updated: id={id_}")
            return True

    @staticmethod
    async def delete(id_: int) -> bool:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                text("DELETE FROM tb_mcp_server WHERE id = :id"), {"id": id_})
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> bool:
        """切换启用/停用"""
        async with mysql_client.get_session() as session:
            result = await session.execute(
                text("UPDATE tb_mcp_server SET status = :status WHERE id = :id"),
                {"id": id_, "status": 1 if status else 0})
            await session.commit()
            return result.rowcount > 0

    # ==================== 连通性测试 ====================
    @staticmethod
    async def test_by_id(id_: int) -> dict:
        """按配置 ID 测试连接"""
        row = await McpServerService.get_by_id(id_)
        if not row:
            raise ValueError("MCP 连接不存在")
        return await McpServerService.test_params(
            name=row[1], type_=row[2], url=row[3], command=row[4], args=row[5], env=row[6])

    @staticmethod
    async def test_params(name: str = None, type_: str = 'SSE', url: str = None,
                          command: str = None, args: str = None, env: str = None) -> dict:
        """
        按参数测试连接（添加/编辑表单"测试"按钮使用，无需保存即可验证）：
        - SSE：发起流式请求握手，检查状态码与响应头
        - STDIO：校验命令可执行 + 参数 JSON 合法 + 进程能存活启动
        """
        start = time.perf_counter()
        try:
            t = (type_ or 'SSE').upper()
            if t == 'SSE':
                detail = await McpServerService._probe_sse(url, env)
            elif t == 'STDIO':
                detail = await McpServerService._probe_stdio(command, args, env)
            else:
                raise ValueError(f"不支持的连接类型: {type_}")
            cost_ms = int((time.perf_counter() - start) * 1000)
            return {"success": True, "serverName": name or "MCP Server",
                    "toolCount": detail.get("toolCount", 0), "responseTime": cost_ms,
                    "errorMessage": detail.get("message", "连接正常")}
        except Exception as e:
            cost_ms = int((time.perf_counter() - start) * 1000)
            log.warning(f"McpServer test failed: {str(e)}")
            return {"success": False, "serverName": name or "MCP Server",
                    "toolCount": 0, "responseTime": cost_ms,
                    "errorMessage": str(e) or "连接失败"}

    @staticmethod
    async def _probe_sse(url: str, env: str = None) -> dict:
        """SSE 握手探测：能建立连接并收到响应头即视为可达"""
        if not url:
            raise ValueError("SSE 连接地址不能为空")
        if not url.startswith(("http://", "https://")):
            raise ValueError("SSE 地址需以 http:// 或 https:// 开头")
        headers = {"Accept": "text/event-stream"}
        McpServerService._parse_json(env, "env")  # 仅校验 env 格式合法
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, trust_env=False,
                                     headers=headers) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ValueError(f"连接被拒绝（HTTP {resp.status_code}）")
                return {"message": f"SSE 连接正常（HTTP {resp.status_code}）", "toolCount": 0}

    @staticmethod
    async def _probe_stdio(command: str, args: str, env: str = None) -> dict:
        """STDIO 探测：命令可执行 + 参数合法 + 进程可启动并存活"""
        if not command:
            raise ValueError("STDIO 命令不能为空")
        exe = shutil.which(command.split()[0]) if command else None
        if not exe:
            raise ValueError(f"命令不可执行: {command.split()[0]}")
        cmd_args = McpServerService._parse_json(args, "args")
        if cmd_args and not isinstance(cmd_args, list):
            raise ValueError("STDIO 参数必须是 JSON 数组格式")
        env_dict = McpServerService._parse_json(env, "env")

        import subprocess
        full_cmd = [command] + [str(a) for a in (cmd_args or [])]
        try:
            process = subprocess.Popen(
                full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env_dict or None, shell=True if len(full_cmd) == 1 else False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            import asyncio
            await asyncio.sleep(_STDIO_PROBE_KEEP)
            if process.poll() is not None:
                stderr = process.stderr.read().decode("utf-8", "ignore") if process.stderr else ""
                process.kill()
                raise ValueError(f"进程启动即退出（code={process.returncode}）{stderr[:200]}")
            process.kill()
            return {"message": f"STDIO 进程启动正常（{command}）", "toolCount": 0}
        except FileNotFoundError:
            raise ValueError(f"命令不存在: {command}")

    @staticmethod
    def _parse_json(value: str, field: str):
        """解析可选 JSON 字段（args 数组 / env 对象），非法时给出明确错误"""
        if not value or not str(value).strip():
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                raise ValueError(f"{field} 字段不是合法 JSON: {value[:100]}")