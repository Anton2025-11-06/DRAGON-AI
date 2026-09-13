import dataclasses
import logging
import os


@dataclasses.dataclass
class Config:
    # 服务端 Nacos 未开启鉴权（auth_enabled=false，standalone 默认）时，凭据必须留空：
    # v2 Python SDK 只要 username/password 同时非空就会强制去 /v1/auth/users/login 换 access token，
    # 而关闭鉴权时该接口无用户可验证（返回 500 User not found）→ 抛 "get access token failed"。
    # 若日后启用鉴权（docker 加 -e NACOS_AUTH_ENABLE=true 并创建用户），再改为对应环境变量/此默认值。
    nacos_name = ""
    nacos_password = ""
    nacos_server_address = "121.43.156.100:8848"
    nacos_namespace_id = "dragon-ai"
    nacos_log_level = logging.INFO


