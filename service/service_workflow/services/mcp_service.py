import ast
import asyncio
import base64
import json
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

# MCP 连接测试超时（秒）：SDK initialize + 工具清单握手必须在此时限内完成
_PROBE_TIMEOUT = 5.0

# ==================== 进程内会话缓存（官方 MCP SDK 长连接） ====================
# MCP 服务器 id → ClientSession；SSE/STDIO 均建立后保持长连接复用，
# 调用失败或 refresh 时重建。多进程部署（API 进程/arq worker）各自维护一份。


class McpConnectionRegistry:
    """MCP 长连接注册表：管理 ClientSession 生命周期（建连/复用/关闭）。"""

    _sessions: dict[int, object] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def get(cls, mcp_id: int, row=None) -> object:
        """取缓存会话；未命中则按配置建连并 initialize，返回前已就绪。"""
        async with cls._lock:
            session = cls._sessions.get(mcp_id)
            if session is not None:
                return session
            if row is None:
                row = await McpServerService.get_by_id(mcp_id)
                if not row:
                    raise ValueError("MCP 连接不存在")
            session = await McpServerService._connect(row)
            cls._sessions[mcp_id] = session
            return session

    @classmethod
    async def drop(cls, mcp_id: int) -> None:
        """断开并移除缓存会话（refresh 接口 / 连接失败自动重建时调用）。"""
        async with cls._lock:
            session = cls._sessions.pop(mcp_id, None)
            if session is not None:
                await McpServerService._close_session(session)

    @classmethod
    def size(cls) -> int:
        return len(cls._sessions)


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

    # ==================== SDK 连接与会话管理 ====================

    @staticmethod
    async def _connect(row) -> object:
        """按配置行建立官方 SDK 连接并完成 initialize 握手，返回就绪的 ClientSession。

        row 结构（与 get_by_id 一致）：(id, name, type, url, command, args, env, status, created_by)。
        - SSE：mcp.client.sse.sse_client(url, headers)
        - STDIO：mcp.client.stdio.stdio_client(StdioServerParameters(command, args, env))
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as e:  # noqa: BLE001
            raise ValueError("mcp SDK 未安装，请执行 pip install mcp") from e

        type_ = str(row[2] or "SSE").upper()
        streams = None
        if type_ == "SSE":
            url = row[3]
            if not url:
                raise ValueError("SSE 连接地址不能为空")
            if not url.startswith(("http://", "https://")):
                raise ValueError("SSE 地址需以 http:// 或 https:// 开头")
            McpServerService._parse_json(row[6], "env")  # 仅校验 env 格式合法；沿用旧格式时静默
            streams = sse_client(url, headers={"Accept": "text/event-stream"})
        elif type_ == "STDIO":
            command = row[4]
            if not command:
                raise ValueError("STDIO 启动命令不能为空")
            cmd_args = McpServerService._parse_json(row[5], "args")
            if cmd_args and not isinstance(cmd_args, list):
                raise ValueError("STDIO 参数必须是 JSON 数组格式")
            env_dict = McpServerService._parse_json(row[6], "env") or None
            params = StdioServerParameters(
                command=command,
                args=[str(a) for a in (cmd_args or [])],
                env=env_dict,
            )
            streams = stdio_client(params)
        else:
            raise ValueError(f"不支持的连接类型: {type_}")

        reader, writer = await streams.__aenter__()
        session = ClientSession(reader, writer)
        # 保持 async generator 存活：streams 若随 _connect 返回而失去引用，
        # 会被 GC 提前 close（GeneratorExit → shutdown），导致后续请求 Connection closed
        session._mcp_streams = streams  # type: ignore[attr-defined]
        try:
            await session.__aenter__()
            await session.initialize()
        except Exception:
            await McpServerService._close_session_entries(session, streams, reader, writer)
            raise
        return session

    @staticmethod
    async def _close_session(session) -> None:
        """关闭 ClientSession（SDK 2.x 无公开 close，走 context manager 出口）。

        同时关闭建连时挂载的底层 streams（_connect 中持有引用防 GC），
        顺序：先会话、后传输层。
        """
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        streams = getattr(session, "_mcp_streams", None)
        if streams is not None:
            try:
                await streams.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _close_session_entries(session, streams, reader, writer) -> None:
        """连接失败时兜底清理：session + 底层流 + streams 上下文。"""
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        try:
            await writer.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            await reader.aclose()
        except Exception:  # noqa: BLE001
            pass
        try:
            await streams.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    # ==================== 工具清单 / 工具调用 ====================

    @staticmethod
    async def batch_tools(server_ids: list) -> list:
        """并发获取多个 MCP 服务器的真实工具清单（tools/list）。

        只统计启用且连接成功的服务器；单个失败不影响其他服务器（返回降级处理）。
        """
        if not server_ids:
            return []
        results: list = []

        async def _one(server_id: int) -> None:
            try:
                session = await McpConnectionRegistry.get(server_id)
                listed = await session.list_tools()
                for t in (listed.tools or []):
                    # SDK 2.x Tool 模型字段为 input_schema；兜底兼容 inputSchema
                    schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
                    results.append({
                        "server_id": server_id,
                        "name": getattr(t, "name", ""),
                        "description": getattr(t, "description", "") or "",
                        "inputSchema": dict(schema) if schema else {},
                    })
            except Exception as e:  # noqa: BLE001
                log.warning("mcp list_tools failed server={}: {}", server_id, e)

        await asyncio.gather(*(_one(sid) for sid in server_ids))
        return results

    @staticmethod
    async def call_tool(mcp_id: int, tool_name: str, arguments: dict = None) -> dict:
        """调用 MCP 工具（tools/call）；调用失败自动重建会话重试一次。

        返回 {content, urls, isError, structured}：文本/资源内容提取为 content，
        图片/音频二进制转 data URI，便于上层节点与前端直接消费。
        """
        arguments = arguments or {}
        try:
            session = await McpConnectionRegistry.get(mcp_id)
            result = await session.call_tool(tool_name, arguments)
        except Exception as e:  # noqa: BLE001
            log.warning("mcp call_tool fail server={} tool={}, reconnect once: {}",
                        mcp_id, tool_name, e)
            await McpConnectionRegistry.drop(mcp_id)
            session = await McpConnectionRegistry.get(mcp_id)
            result = await session.call_tool(tool_name, arguments)
        return McpServerService._tool_result_to_dict(result)

    @staticmethod
    def _tool_result_to_dict(result) -> dict:
        """CallToolResult → 可 JSON 序列化 dict（文本/资源提取 + 二进制转 data URI）。"""
        texts: list = []
        urls: list = []
        for block in getattr(result, "content", None) or []:
            btype = getattr(block, "type", None)
            text = getattr(block, "text", None)
            if text:
                texts.append(str(text))
            elif btype in ("image", "audio"):
                data = getattr(block, "data", None)
                mime = getattr(block, "mimeType", None) or ""
                if isinstance(data, bytes):
                    data = base64.b64encode(data).decode()
                if data:
                    urls.append(f"data:{mime};base64,{data}")
            elif btype == "resource":
                uri = getattr(block, "uri", None) or getattr(block, "blob", None)
                if uri and not isinstance(uri, bytes):
                    urls.append(str(uri))
        return {
            "content": "\n".join(texts),
            "urls": urls,
            "isError": bool(getattr(result, "isError", False)),
            "structured": getattr(result, "structuredContent", None),
        }

    @staticmethod
    async def refresh(mcp_id: int) -> None:
        """断开并清除缓存会话（配置变更/异常后调用；下次调用自动重连）。"""
        await McpConnectionRegistry.drop(mcp_id)
        log.info("McpServer session refreshed: id={}", mcp_id)

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
        官方 SDK 真实握手——SSE/STDIO 都完成 initialize + tools/list，
        toolCount 返回真实工具数（_PROBE_TIMEOUT=5s 内未完成视为失败）。
        """
        start = time.perf_counter()
        try:
            t = (type_ or 'SSE').upper()
            if t not in ('SSE', 'STDIO'):
                raise ValueError(f"不支持的连接类型: {type_}")
            row = [0, name or "MCP Server", t, url, command, args, env, 1, 0]

            async def _handshake() -> int:
                session = await McpServerService._connect(row)
                try:
                    listed = await session.list_tools()
                    return len(listed.tools or [])
                finally:
                    await McpServerService._close_session(session)

            tool_count = await asyncio.wait_for(_handshake(), timeout=_PROBE_TIMEOUT)
            cost_ms = int((time.perf_counter() - start) * 1000)
            return {"success": True, "serverName": name or "MCP Server",
                    "toolCount": tool_count, "responseTime": cost_ms,
                    "errorMessage": f"连接正常，发现 {tool_count} 个工具"}
        except asyncio.TimeoutError:
            cost_ms = int((time.perf_counter() - start) * 1000)
            log.warning("McpServer test timeout: type={} name={}", type_, name)
            return {"success": False, "serverName": name or "MCP Server",
                    "toolCount": 0, "responseTime": cost_ms,
                    "errorMessage": f"连接超时（{_PROBE_TIMEOUT}s）：服务器未在时限内完成握手"}
        except Exception as e:  # noqa: BLE001
            cost_ms = int((time.perf_counter() - start) * 1000)
            log.warning("McpServer test failed: {}", str(e))
            return {"success": False, "serverName": name or "MCP Server",
                    "toolCount": 0, "responseTime": cost_ms,
                    "errorMessage": str(e) or "连接失败"}

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