import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from common.common_log.log_init import log
from common.common_constants.constant import SERVICE_ALIASES
from common.common_httpx.httpx import httpx_pool
from service.service_gateway.util.model_proxy_router import model_proxy

router = APIRouter(tags=["业务网关"])

# 不透传给下游服务的请求头
_EXCLUDED_HEADERS = {"host", "content-length", "connection", "accept-encoding"}


@router.post("/api/model", summary="直连模型：api-key 鉴权，转发模型真实地址（完整路径）")
async def model_proxy_forward(request: Request):
    """直连：模型 base_url 维护的是完整接口路径，直接转发"""
    return await model_proxy(request, True)


@router.post("/api/model/{path:path}", summary="非直连模型：api-key 鉴权，base_url + 接口后缀转发")
async def model_proxy_entry(path: str, request: Request):
    """
    非直连：模型 base_url 维护的是接口基础地址，path 为接口后缀（如 v1/chat/completions），
    网关拼接 base_url + /path 后转发，并校验 path 在模型维护的后缀列表中
    """
    return await model_proxy(request, False, path)


@router.api_route("/api/{service_name}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                  summary="按服务名动态转发")
async def proxy(service_name: str, path: str, request: Request):
    """
    网关转发入口：/api/{service_name}/{下游路径}
    通过 Nacos 服务发现动态获取下游健康实例地址，透传方法/查询参数/请求头/请求体
    """

    # 模块名别名归一化：/api/system/... 映射到 Nacos 注册名 service_system
    def _normalize_service_name(name: str) -> str:
        """模块名别名 → Nacos 注册名（已带 service_ 前缀的路径原样转发）"""
        return SERVICE_ALIASES.get(name, None)

    service_name = _normalize_service_name(service_name)
    if service_name is None:
        raise HTTPException(status_code=404, detail="无效请求")

    nacos_service = request.app.state.nacos_service
    target = await nacos_service.get_one_healthy_instance(service_name)
    if target is None:
        raise HTTPException(status_code=503, detail=f"服务 {service_name} 无可用实例")

    ip, port = target
    url = f"http://{ip}:{port}/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_HEADERS}
    # 网关 TokenCheckMiddleware 校验通过后写入 scope["token"]
    # 跨服务，request.scope request.state读不到，改写到header下游消费用
    # 此处注入内部头 X-User-Token，下游可重复读，（覆盖客户端伪造值），下游服务直接读取即可，无需重复校验 token
    if request.scope.get("token"):
        headers["X-User-Token"] = request.scope["token"]
    body = await request.body()

    try:
        resp = await httpx_pool.client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            headers=headers,
            content=body if body else None,
        )
    except httpx.RequestError as e:
        log.error(f"Gateway forward to {service_name} failed: {str(e)}")
        raise HTTPException(status_code=502, detail="下游服务请求失败")

    resp_headers = {k: v for k, v in resp.headers.items()
                    if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers,
                    media_type=resp.headers.get("content-type"))
