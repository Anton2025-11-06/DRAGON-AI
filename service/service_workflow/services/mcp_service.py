import ast
import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select, update

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_workflow.models.agent_entity import McpServer

# MCP 连接测试超时（秒）：SDK initialize + 工具清单握手必须在此时限内完成
_PROBE_TIMEOUT = 5.0

# ==================== 连接模型：现场建连，用完即关 ====================
# 不做进程内 ClientSession 缓存：缓存的会话底下是 async generator 流（SSE）或子进程（STDIO），
# 服务端回收或事件循环切换后就会变成一条已断的连接（报错表现为 "Connection closed"），
# 且 API 进程与 arq worker 各持一份、多实例下根本无法共享。因此每次 list/call 都按配置
# 现建一条连接，靠 SDK 上下文管理器在退出时逐层关闭，不留任何跨请求存活的对象。


class McpServerService:
    """MCP 服务器连接配置：CRUD + 连通性测试 + 工具探测（SSE/STDIO 两种模式）"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10,
                   name: str = None, status: int = None,
                   viewer_id: int = 0, viewer_admin: bool = False) -> dict:
        """分页查询 MCP 连接配置（非创建人且非管理员时隐藏 SSE url 值）"""
        async with mysql_client.get_session() as session:
            conds = []
            if name:
                conds.append(McpServer.name.like(f"%{name}%"))
            if status is not None:
                conds.append(McpServer.status == status)
            total = (await session.execute(
                select(func.count()).select_from(McpServer).where(*conds))).scalar()
            rows = (await session.execute(
                select(McpServer).where(*conds)
                .order_by(McpServer.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            items = [McpServerService._to_dict(r, viewer_id, viewer_admin) for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    def _to_dict(row: McpServer, viewer_id: int = 0, viewer_admin: bool = False) -> dict:
        """ORM 行转字典：status 统一转 bool（前端 dict-switch 使用）。

        SSE url 属敏感连接地址：非创建人且非管理员时置空并标 urlHidden，
        前端据此隐藏值；后端 update 会忽略空的 url，避免非创建人回写时误清。
        """
        hidden = (not viewer_admin) and (row.created_by != viewer_id)
        return {
            "id": row.id, "name": row.name, "type": row.type,
            "url": "" if hidden else row.url,
            "urlHidden": hidden,
            "command": row.command, "args": row.args, "env": row.env,
            "status": bool(row.status), "created_by": row.created_by,
            "create_time": row.create_time, "update_time": row.update_time,
            "description": row.description,
        }

    @staticmethod
    async def get_by_id(id_: int) -> Optional[McpServer]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(McpServer).where(McpServer.id == id_))).scalar_one_or_none()

    # ==================== SDK 连接与会话管理 ====================

    @staticmethod
    def _transports(row: McpServer):
        """按配置行返回传输层上下文管理器（此时尚未建连）。
    
        row 为 McpServer ORM 行（get_by_id 结果或 test_params 构造的临时对象），
        只读取 type/url/command/args/env 字段。
        - SSE：mcp.client.sse.sse_client(url, headers)
        - STDIO：mcp.client.stdio.stdio_client(StdioServerParameters(command, args, env))
        """
        try:
            from mcp.client.sse import sse_client
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as e:  # noqa: BLE001
            raise ValueError("mcp SDK 未安装，请执行 pip install mcp") from e
    
        type_ = str(row.type or "SSE").upper()
        if type_ == "SSE":
            url = row.url
            if not url:
                raise ValueError("SSE 连接地址不能为空")
            if not url.startswith(("http://", "https://")):
                raise ValueError("SSE 地址需以 http:// 或 https:// 开头")
            McpServerService._parse_json(row.env, "env")  # 仅校验 env 格式合法；沿用旧格式时静默
            return sse_client(url, headers={"Accept": "text/event-stream"})
        if type_ == "STDIO":
            command = row.command
            if not command:
                raise ValueError("STDIO 启动命令不能为空")
            cmd_args = McpServerService._parse_json(row.args, "args")
            if cmd_args and not isinstance(cmd_args, list):
                raise ValueError("STDIO 参数必须是 JSON 数组格式")
            env_dict = McpServerService._parse_json(row.env, "env") or None
            params = StdioServerParameters(
                command=command,
                args=[str(a) for a in (cmd_args or [])],
                env=env_dict,
            )
            return stdio_client(params)
        raise ValueError(f"不支持的连接类型: {type_}")

    @classmethod
    @asynccontextmanager
    async def _session(cls, row):
        """现场建一条已完成 initialize 的会话，退出时由 SDK 上下文逐层收尾。

        两层 with 的嵌套不能合并：ClientSession 一进场就要拿到读写流，退出顺序必须是
        先会话、后传输层（STDIO 下才不会漏下子进程）。
        """
        from mcp import ClientSession  # 延迟 import：未装 mcp 时不影响本模块 CRUD 接口

        async with cls._transports(row) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    # ==================== 工具清单 / 工具调用 ====================

    @staticmethod
    async def list_tools(row, server_id: int = 0) -> list:
        """按配置行取工具清单（tools/list），返回可序列化 dict 列表。

        现场建连、取完即关，所以失败一次就是“这条连接现在不通”，不会拖到下次。
        """
        async with McpServerService._session(row) as session:
            listed = await session.list_tools()
        items: list = []
        for t in (listed.tools or []):
            # SDK 2.x Tool 模型字段为 input_schema；兜底兼容 inputSchema
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            items.append({
                "server_id": server_id,
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", "") or "",
                "inputSchema": dict(schema) if schema else {},
            })
        return items

    @staticmethod
    async def batch_tools(server_ids: list) -> list:
        """并发获取多个 MCP 服务器的真实工具清单（tools/list）。

        单个失败不影响其他服务器（该服务器记一条告警后降级为空）。
        """
        if not server_ids:
            return []
        results: list = []

        async def _one(server_id: int) -> None:
            try:
                row = await McpServerService.get_by_id(server_id)
                if not row:
                    raise ValueError("MCP 连接不存在")
                results.extend(await McpServerService.list_tools(row, server_id))
            except Exception as e:  # noqa: BLE001
                log.warning("mcp list_tools failed server={}: {}", server_id, e)

        await asyncio.gather(*(_one(sid) for sid in server_ids))
        return results

    @staticmethod
    async def call_tool(mcp_id: int, tool_name: str, arguments: dict = None) -> dict:
        """调用 MCP 工具（tools/call）：每次现场建连，用完即关。

        不再做“失败后重连重试一次”：那是为缓存的旧连接打的洞，现建模式下首次失败就是真失败。
        返回 {content, urls, isError, structured}：文本/资源内容提取为 content，
        图片/音频二进制转 data URI，便于上层节点与前端直接消费。
        """
        arguments = arguments or {}
        row = await McpServerService.get_by_id(mcp_id)
        if not row:
            raise ValueError("MCP 连接不存在")
        async with McpServerService._session(row) as session:
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

    # ==================== 新增 / 更新 / 删除 ====================
    @staticmethod
    async def create(name: str, type_: str, url: str = None, command: str = None,
                     args: str = None, env: str = None, status: bool = True,
                     created_by: int = 0, description: str = None) -> int:
        async with mysql_client.get_session() as session:
            server = McpServer(name=name, type=type_, url=url, command=command,
                               args=args, env=env, status=1 if status else 0,
                               created_by=created_by, description=description)
            session.add(server)
            await session.flush()
            new_id = server.id
            await session.commit()
            log.info(f"McpServer created: {name} ({type_})")
            return new_id

    @staticmethod
    async def update(id_: int, name: str = None, type_: str = None, url: str = None,
                     command: str = None, args: str = None, env: str = None,
                     status: bool = None, description: str = None) -> bool:
        async with mysql_client.get_session() as session:
            values: dict = {}
            if name is not None:
                values["name"] = name
            if type_ is not None:
                values["type"] = type_
            # url 仅在传入非空时更新：非创建人拿到的 url 已被脱敏为空，回写不应清空真实地址
            if url:
                values["url"] = url
            if command is not None:
                values["command"] = command
            if args is not None:
                values["args"] = args
            if env is not None:
                values["env"] = env
            if status is not None:
                values["status"] = 1 if status else 0
            if description is not None:
                values["description"] = description
            if not values:
                return True
            await session.execute(
                update(McpServer).where(McpServer.id == id_).values(**values))
            await session.commit()
            log.info(f"McpServer updated: id={id_}")
            return True

    @staticmethod
    async def delete(id_: int) -> bool:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                delete(McpServer).where(McpServer.id == id_))
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> bool:
        """切换启用/停用"""
        async with mysql_client.get_session() as session:
            result = await session.execute(
                update(McpServer).where(McpServer.id == id_)
                .values(status=1 if status else 0))
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
            name=row.name, type_=row.type, url=row.url, command=row.command,
            args=row.args, env=row.env)

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
            # 临时对象：_transports 只读连接字段，不会落库
            row = McpServer(name=name or "MCP Server", type=t, url=url,
                            command=command, args=args, env=env)

            async def _handshake() -> int:
                async with McpServerService._session(row) as session:
                    listed = await session.list_tools()
                    return len(listed.tools or [])

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