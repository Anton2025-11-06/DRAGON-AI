import asyncio
import json
import os
import re
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from common.common_log.log_init import log
from common.common_constants.constant import (PREFIX_LOGIN, PREFIX_WORKFLOW_API_KEY,
                                              SERVICE_ALIASES)
from common.common_entity.response_schema import ApiResponse
from common.common_httpx.httpx import httpx_pool
from common.common_redis.redis import client
from common.common_utils.jwt_util import decode_token
from common.common_utils.rate_limiter import RateLimiter
from service.service_gateway.util.model_proxy_router import model_proxy

router = APIRouter(tags=["业务网关"])

# 不透传给下游服务的请求头
_EXCLUDED_HEADERS = {"host", "content-length", "connection", "accept-encoding"}


@router.post("/api/model", summary="模型网关：api-key 鉴权，按 (类型,供应商) 走 common_model 直连")
async def model_proxy_forward(request: Request):
    """统一入口：模型登记只需 base_url（厂商接口基础地址）+ provider + category，端点由 common_model 子类自拼。"""
    return await model_proxy(request)


@router.post("/api/model/{path:path}", summary="模型网关（OpenAI SDK 兼容别名：忽略接口路径，同 /api/model）")
async def model_proxy_entry(path: str, request: Request):
    """兼容 OpenAI 风格客户端（base_url 后自动追加 /chat/completions 等）：path 仅作路由别名，不再参与转发。"""
    return await model_proxy(request)


# 模块名别名归一化：/api/system/... 映射到 Nacos 注册名 service_system
def _normalize_service_name(name: str) -> str:
    """模块名别名 → Nacos 注册名（已带 service_ 前缀的路径原样转发）"""
    return SERVICE_ALIASES.get(name, None)


@router.api_route("/api/{service_name}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                  summary="按服务名动态转发")
async def proxy(service_name: str, path: str, request: Request):
    """
    网关转发入口：/api/{service_name}/{下游路径}
    通过 Nacos 服务发现动态获取下游健康实例地址，透传方法/查询参数/请求头/请求体
    """
    service_name = _normalize_service_name(service_name)
    if service_name is None:
        raise HTTPException(status_code=404, detail="无效请求")

    # Nacos 发现失败（服务端不可用/实例未注册）时回退到静态实例表
    # nacos_service = getattr(request.app.state, "nacos_service", None)
    # target = None
    # if nacos_service is not None:
    #     try:
    #         target = await nacos_service.get_one_healthy_instance(service_name)
    #     except Exception as e:
    #         log.warning(f"Nacos discovery failed for {service_name}: {e}")
    # if target is None:
    #     raise HTTPException(status_code=503, detail=f"服务 {service_name} 无可用实例")
    if service_name == "service_workflow":
        target = ("127.0.0.1", 9003)
    if service_name == "service_login":
        target = ("127.0.0.1", 9004)
    if service_name == "service_system":
        target = ("127.0.0.1", 9001)

    ip, port = target
    url = f"http://{ip}:{port}/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_HEADERS}
    # 网关 TokenCheckMiddleware 校验通过后写入 scope["token"]
    # 跨服务，request.scope request.state读不到，改写到header下游消费用
    # 此处注入内部头 X-User-Token，下游可重复读，（覆盖客户端伪造值），下游服务直接读取即可，无需重复校验 token
    if request.scope.get("token"):
        headers["X-User-Token"] = request.scope["token"]
    body = await request.body()

    # ===== 工作流 API Key 模式：X-Workflow-Token 的鉴权与 QPS 限流都在网关做（下游读不到 key 配置）=====
    if service_name == "service_workflow" and request.headers.get("X-Workflow-Token"):
        return await workflow_api_proxy(request, path, url, headers, body)

    return await forward_downstream(request, url, headers, body)


# ==================== WebSocket 转发（submit 预览运行等） ====================
@router.websocket("/api/{service_name}/{path:path}")
async def ws_proxy(websocket: WebSocket, service_name: str, path: str):
    """WebSocket 透传代理（受理客户端 → 解析登录态 → 注入 login_user → 连下游转发）。

    协议：客户端连上后先发一帧 JSON 入参（内嵌 Authorization），随后服务端事件流
    经同一连接回推。中间件（TokenCheck/OperateLog）对 WS 不生效，
    故鉴权在此显式完成，登录态随首帧下发给下游、由下游写回 scope 供取 user_id。
    """
    target = None
    service_name = _normalize_service_name(service_name)
    if service_name is None:
        await websocket.send_text(json.dumps(
            {"code": 400, "message": "无效的请求"}, ensure_ascii=False))
        await websocket.close(code=4400)
        return
    # Nacos 发现失败（服务端不可用/实例未注册）时回退到静态实例表
    # nacos_service = getattr(request.app.state, "nacos_service", None)
    # if nacos_service is not None:
    #     try:
    #         target = await nacos_service.get_one_healthy_instance(service_name)
    #     except Exception as e:
    #         log.warning(f"Nacos discovery failed for {service_name}: {e}")
    # if target is None:
    #     raise HTTPException(status_code=503, detail=f"服务 {service_name} 无可用实例")
    if service_name == "service_workflow":
        target = ("127.0.0.1", 9003)
    if service_name == "service_login":
        target = ("127.0.0.1", 9004)
    if service_name == "service_system":
        target = ("127.0.0.1", 9001)

    if target is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    # 首帧：客户端发送的执行入参（内嵌 Authorization）
    try:
        first = json.loads(await websocket.receive_text())
    except Exception:  # noqa: BLE001
        await websocket.send_text(json.dumps(
            {"code": 400, "message": "首帧必须是包含入参的 JSON"}, ensure_ascii=False))
        await websocket.close(code=4400)
        return

    auth = first.pop("Authorization", None) or first.pop("authorization", None)

    async def _resolve_login_user_from_auth(auth) -> dict | None:
        """浏览器 WebSocket 无法自定义握手头，token 随首帧 JSON 的 Authorization 传入。
        复用网关既有鉴权口径：剥离 Bearer 前缀 → decode_token(JWT) → 兜底 Redis 历史登录态。"""
        if not auth:
            return None
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else auth.strip()
        if not token:
            return None
        payload = decode_token(token)
        if payload:
            return payload
        return await client.get(PREFIX_LOGIN + token, to_dict=True)

    login_user = await _resolve_login_user_from_auth(auth)
    if not login_user:
        await websocket.send_text(json.dumps(
            {"code": 401, "message": "未登录或登录已失效"}, ensure_ascii=False))
        await websocket.close(code=4401)
        return

    # 注入登录态供下游取 user_id（下游写回 websocket.scope["login_user"] 后 get_user_id 生效）
    first["login_user"] = login_user

    ip, port = target
    upstream_url = f"ws://{ip}:{port}/{path}"
    # 非空表示链路真的断了（非用户停止/非执行结束），需告知前端免得停在“执行中”
    broke_reason = None
    try:
        async with ws_connect(upstream_url, max_size=None) as upstream:
            await upstream.send(json.dumps(first, ensure_ascii=False))

            async def relay_to_client() -> None:
                """下游事件流 → 浏览器；下游发 close 帧（执行结束）即正常退出。"""
                async for msg in upstream:
                    await websocket.send_text(
                        msg if isinstance(msg, str) else msg.decode())

            async def absorb_client() -> bool:
                """浏览器 → 网关：只消化应用层心跳并就地回 pong。

                下游 WS submit 只读一次入参、执行期不再读客户端，故不能把 ping
                转下去（会堆积/无人消费）。返回 True 表示浏览器已断开（用户停止/关页面），
                属正常收尾，不得按链路异常告警。"""
                try:
                    while True:
                        raw = await websocket.receive_text()
                        try:
                            frame = json.loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        if frame.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
                except WebSocketDisconnect:
                    return True
                return False

            # 双向并发：任一侧结束即整体收尾（浏览器断开时立刻关下游，不等下一个事件）
            relay_task = asyncio.create_task(relay_to_client())
            client_task = asyncio.create_task(absorb_client())
            await asyncio.wait({relay_task, client_task},
                               return_when=asyncio.FIRST_COMPLETED)
            for task in (relay_task, client_task):
                if not task.done():
                    task.cancel()
            relay_err, client_res = await asyncio.gather(
                relay_task, client_task, return_exceptions=True)
            browser_left = isinstance(client_res, asyncio.CancelledError) or \
                client_res is True
            if broke_reason is None and isinstance(relay_err, BaseException) \
                    and not isinstance(relay_err, ConnectionClosedOK) \
                    and not browser_left:
                broke_reason = str(relay_err) or relay_err.__class__.__name__
                log.warning(
                    f"Gateway WS relay from {upstream_url} broke: {broke_reason}")
    except WebSocketDisconnect:
        pass
    except ConnectionClosedOK:
        # 下游正常关闭（执行结束）：收尾帧已透传，不是错误
        pass
    except ConnectionClosed as e:
        broke_reason = str(e) or "下游连接中断"
        log.warning(f"Gateway WS proxy to {upstream_url} broke: {str(e)}")
    except Exception as e:  # noqa: BLE001
        broke_reason = str(e)
        log.warning(f"Gateway WS proxy to {upstream_url} failed: {str(e)}")
    finally:
        if broke_reason:
            try:
                await websocket.send_text(json.dumps(
                    {"code": 500, "message": "执行连接已中断，请查看执行历史"},
                    ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


# ==================== 工作流 API Key 模式（第三方 API 执行） ====================

async def workflow_api_proxy(request: Request, path: str, url: str,
                             headers: dict, body: bytes):
    """第三方拿 API Key 调用工作流时的网关入口：四类请求放行，其余 403。

    - POST workflow-executions/submit          唯一提交入口（规则见 workflow_submit_proxy）
    - GET  workflow-executions/{eid}           单条详情（approvalToken 的唯一出处）
    - GET  workflow-executions/{eid}/subscribe  SSE 事件流
    - POST workflow-executions/{eid}/cancel    取消执行

    读口只开到单条详情：事件帧不承载审批内容，不给详情就没法答审批；分页/列表
    能一次拉到别人的执行，不在 key 的能力范围内。

    key 的校验分两段能做：新建执行时目标工作流由 key 决定，网关就能判；拿着
    executionId 的动作要先查库才知道这条执行属于谁，网关没有 DB，放行给下游校。
    """
    # 订阅（SSE 长连接）直接放行：executionId 是 UUID 熵足够，TokenCheckMiddleware 本来就
    # 把 /subscribe 列进了后缀白名单（不校登录态）。若在这里按“只认 submit”判掉，
    # 第三方带着 X-Workflow-Token 订阅反而会吃 403。
    if request.method == "GET" and path.endswith("/subscribe"):
        return await forward_downstream(request, url, headers, body)

    if request.method == "GET" and re.match(
            r"^workflow-executions/[0-9a-fA-F-]{36}$", path):
        return await forward_downstream(request, url, headers, body)

    if request.method == "POST" and re.match(
            r"^workflow-executions/[0-9a-fA-F-]{36}/cancel$", path):
        return await forward_downstream(request, url, headers, body)

    if request.method == "POST" and path == "workflow-executions/submit":
        return await workflow_submit_proxy(request, url, headers, body)

    return JSONResponse(status_code=403,
                        content=ApiResponse.error(
                            403, "API Key 仅支持【提交执行/查看现状/订阅事件/取消执行】工作流的操作"))


async def _workflow_api_key(request: Request) -> tuple:
    """取 X-Workflow-Token 对应的 key 配置，返回 (cfg, token, 拒绝响应)。

    有效性、启用状态、过期时间三道一起过；deny 非空时另两项无意义，直接把它回给调用方。
    """
    token = (request.headers.get("X-Workflow-Token") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    raw = await client.hget(PREFIX_WORKFLOW_API_KEY, token)
    if not raw:
        return None, None, JSONResponse(status_code=401,
                                        content=ApiResponse.error(401, "无效的 API Key"))
    try:
        cfg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, None, JSONResponse(status_code=401,
                                        content=ApiResponse.error(401, "无效的 API Key"))
    if cfg.get("status") != "ACTIVE":
        return None, None, JSONResponse(status_code=403,
                                        content=ApiResponse.error(403, "API Key 已停用"))
    expire_time = cfg.get("expireTime")
    if expire_time:
        try:
            if datetime.now() > datetime.strptime(expire_time, "%Y-%m-%d %H:%M:%S"):
                return None, None, JSONResponse(
                    status_code=403,
                    content=ApiResponse.error(403, "API Key 已过期"))
        except ValueError:
            return None, None, JSONResponse(
                status_code=401, content=ApiResponse.error(401, "无效的 API Key"))
    return cfg, token, None


async def workflow_submit_proxy(request: Request, url: str, headers: dict,
                                body: bytes):
    """submit 的 key 侧规则：已有会话的动作原样透传，新建执行按 key 认工作流。

    新建时调用方可以只发业务参数（{"query":"…"}），也可以发完整契约体；两种都会被
    整成 {"workflowId": <key 绑定的>, "values": <业务参数>}——工作流 id 不是调用方选的，
    它传了就必与 key 绑定的一致。只跑已发布版本由下游按 trigger=API 把关。
    """
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if payload.get("executionId"):
        # 答审批 / 续跑 / 重开：包体就是契约体，key ↔ 执行的归属关系下游校
        return await forward_downstream(request, url, headers, body)

    cfg, token, denied = await _workflow_api_key(request)
    if denied is not None:
        return denied
    workflow_id = int(cfg.get("workflowId") or 0)
    if payload.get("workflowId") and int(payload["workflowId"]) != workflow_id:
        return JSONResponse(status_code=403,
                            content=ApiResponse.error(403, "API Key 无权调用该工作流"))
    rate_limit = int(cfg.get("rateLimit") or 0)
    if rate_limit > 0 and not await RateLimiter.check_workflow_api_qps(token, rate_limit):
        return JSONResponse(status_code=429,
                            content=ApiResponse.error(429, "请求频率超过 API Key 限制，请稍后再试"))

    values = payload.get("values")
    if values is None:
        values = {k: v for k, v in payload.items() if k != "workflowId"}
    outbound = {"workflowId": workflow_id, "values": values or {}}
    return await forward_downstream(
        request, url, headers,
        json.dumps(outbound, ensure_ascii=False).encode())


# ==================== 通用转发 ====================

async def forward_downstream(request: Request, url: str, headers: dict, body: bytes):
    """统一下游转发：SSE/流式端点流式转发（token 实时推送），其余整读整转。"""
    # SSE / 流式端点：必须流式转发。若用整读整转（client.request + resp.content），
    # 运行中/暂停中执行的事件流会被缓冲到流结束才下发，前端实时 token/暂停事件全丢。
    is_stream = url.endswith("/subscribe") or \
                "text/event-stream" in (request.headers.get("accept") or "")
    if is_stream:
        try:
            req = httpx_pool.client.build_request(
                method=request.method,
                url=url,
                params=request.query_params,
                headers=headers,
                content=body if body else None,
                timeout=httpx.Timeout(None, connect=10.0),  # 读无限（流生命周期由下游决定）
            )
            upstream = await httpx_pool.client.send(req, stream=True)
        except httpx.RequestError as e:
            log.error(f"Gateway stream-forward to {url} failed: {str(e)}")
            raise HTTPException(status_code=502, detail="下游服务请求失败")

        async def _stream_chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()  # 归还连接池

        resp_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
        return StreamingResponse(_stream_chunks(),
                                 status_code=upstream.status_code,
                                 headers=resp_headers,
                                 media_type=upstream.headers.get("content-type"))

    try:
        resp = await httpx_pool.client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            headers=headers,
            content=body if body else None,
        )
    except httpx.RequestError as e:
        log.error(f"Gateway forward to {url} failed: {str(e)}")
        raise HTTPException(status_code=502, detail="下游服务请求失败")

    resp_headers = {k: v for k, v in resp.headers.items()
                    if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers,
                    media_type=resp.headers.get("content-type"))
