import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from common.common_entity.response_schema import ApiResponse
from common.common_log.log_init import log
from common.common_constants.constant import SERVICE_ALIASES

router = APIRouter(tags=["网关"])

# 全局复用转发客户端：连接池 keep-alive 复用，避免每个请求新建连接
# （高频短连接容易触发 Windows accept 异常，且降低转发开销）
# trust_env=False：跳过 Windows 系统代理（Clash 127.0.0.1:7897），否则内网转发被代理拦截返回 502
_client = httpx.AsyncClient(timeout=30, trust_env=False)
# 不透传给下游服务的请求头
_EXCLUDED_HEADERS = {"host", "content-length", "connection", "accept-encoding"}

# URI 规范：/api/模块名 分类（如 /api/system/users），网关据此归一化为 Nacos 注册服务名


# 下游服务路由前缀：网关别名 → 下游接口前缀（空=直接根路径）
# service_login 的路由本身以 /login 开头（POST /login、/register、/logout、/refresh），
# 网关 path 段直接拼接即可，无需额外前缀；service_system 接口直接挂根路径（/users /roles /menus /depts /logs）
_PATH_PREFIXES = {}


def _normalize_service_name(name: str) -> str:
    """模块名别名 → Nacos 注册名（已带 service_ 前缀的路径原样转发）"""
    return SERVICE_ALIASES.get(name, name)


@router.api_route("/api/{service_name}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                  summary="按服务名动态转发")
async def proxy(service_name: str, path: str, request: Request):
    """
    网关转发入口：/api/{service_name}/{下游路径}
    2. 通过 Nacos 服务发现动态获取下游健康实例地址，透传方法/查询参数/请求头/请求体
    """
    # if not breaker.allow(service_name):
    #     raise HTTPException(status_code=503, detail=f"服务 {service_name} 触发熔断，请稍后再试")

    # 模块名别名归一化：/api/system/... 映射到 Nacos 注册名 service_system
    service_name = _normalize_service_name(service_name)
    # 下游路由前缀：service_login 接口挂在 /login 下，转发时补回
    path_prefix = _PATH_PREFIXES.get(service_name, "")

    nacos_service = request.app.state.nacos_service
    target = await nacos_service.get_one_healthy_instance(service_name)
    if target is None:
        raise HTTPException(status_code=503, detail=f"服务 {service_name} 无可用实例")

    ip, port = target
    url = f"http://{ip}:{port}{path_prefix}/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_HEADERS}
    # 网关 TokenCheckMiddleware 校验通过后写入 scope["token"]，此处注入内部头 X-User-Token
    # （覆盖客户端伪造值），下游服务直接读取即可，无需重复校验 token
    if request.scope.get("token"):
        headers["X-User-Token"] = request.scope["token"]
    body = await request.body()

    try:
        resp = await _client.request(
                method=request.method,
                url=url,
                params=request.query_params,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as e:
        # breaker.record_failure(service_name)
        log.error(f"Gateway forward to {service_name} failed: {str(e)}")
        raise HTTPException(status_code=502, detail="下游服务请求失败")

    # 下游 5xx 视为故障计入熔断，其他成功恢复
    # if resp.status_code >= 500:
    #     breaker.record_failure(service_name)
    # else:
    #     breaker.record_success(service_name)

    resp_headers = {k: v for k, v in resp.headers.items()
                    if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers,
                    media_type=resp.headers.get("content-type"))
