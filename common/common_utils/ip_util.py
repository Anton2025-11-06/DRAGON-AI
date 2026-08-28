
from starlette.requests import Request



def get_client_ip(request: Request) -> str:
    """从请求中提取客户端真实 IP（优先取反向代理透传的头部，返回空字串表示无法识别）。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host or ""
    return ""
