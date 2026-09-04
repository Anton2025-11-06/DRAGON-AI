import json
import logging
import random
import socket
import urllib.parse
import urllib.request
import yaml
from typing import Optional, Tuple, Any, Coroutine
from v2.nacos import ClientConfigBuilder, GRPCConfig, NacosNamingService, RegisterInstanceParam, \
    DeregisterInstanceParam, NacosConfigService, ConfigParam, ListInstanceParam
from common.common_log.log_init import log


def _get_local_ip(server_address: str) -> str:
    """自动探测本机局域网 IP：UDP 连接 Nacos 服务器（无真实数据发送），
    获取本机与 Nacos 同网段的网卡地址；失败时回退 hostname 解析"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            host = server_address.split(":")[0]
            s.connect((host, 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return socket.gethostbyname(socket.gethostname())


class NacosClient:

    def __init__(self, *,
                 user_name, password, server_address, log_level=logging.INFO, log_dir="./nacos_log",
                 service_name, ip, port, group_name="DEFAULT_GROUP", weight=1.0, namespace_id):

        self.user_name = user_name
        self.password = password
        self.server_address = server_address
        self.log_level = log_level
        self.log_dir = log_dir
        self.service_name = service_name
        self.ip = ip
        self.port = port
        self.group_name = group_name
        self.weight = weight
        self.namespace_id = namespace_id

        client_config = (ClientConfigBuilder()
                         .server_address(server_address)
                         .endpoint(server_address)
                         .username(user_name)
                         .password(password)
                         .namespace_id(namespace_id)
                         .log_level(log_level)
                         .log_dir(log_dir)
                         .grpc_config(GRPCConfig(grpc_timeout=5000))
                         .build())

        self.client_config = client_config
        self.service_client: NacosNamingService | None = None
        self.config_client: NacosConfigService | None = None

    async def init(self):
        self.service_client = await NacosNamingService.create_naming_service(self.client_config)
        self.config_client = await NacosConfigService.create_config_service(self.client_config)
        log.info("Nacos naming & config client init success")

    async def register_service(self):
        """注册服务到 Nacos：ip 未显式指定时自动探测本机局域网 IP
        使用持久实例（ephemeral=False），配套服务端 healthCheckMode=none：
        不依赖 Nacos 服务端 TCP 主动探测（跨网段/防火墙下常被误判为不健康），
        实例注册后保持 healthy；注意服务下线需显式注销（脚手架已实现）。"""
        if not self.ip:
            self.ip = _get_local_ip(self.server_address)
            log.info(f"Auto detected local ip: {self.ip}")
        success = await self.service_client.register_instance(
            request=RegisterInstanceParam(service_name=self.service_name,
                                          group_name=self.group_name,
                                          ip=self.ip,
                                          port=self.port,
                                          weight=self.weight,
                                          enabled=True,
                                          healthy=True,
                                          ephemeral=False
                                          ))
        if success:
            log.info(f"Service {self.service_name} registered to Nacos at {self.ip}:{self.port}")
        else:
            log.info(f"Service {self.service_name} fail register to Nacos at {self.ip}:{self.port}")

    async def deregister_service(self):
        """从 Nacos 注销服务"""
        success = await self.service_client.deregister_instance(
            request=DeregisterInstanceParam(service_name=self.service_name, group_name=self.group_name, ip=self.ip,
                                            port=self.port)
        )
        if success:
            log.info(f"Service {self.service_name} deregistered from Nacos at {self.ip}:{self.port}")
        else:
            log.info(f"Service {self.service_name} failed deregister from Nacos at {self.ip}:{self.port}")

    async def get_config_content(self, data_id: str, group_name="DEFAULT_GROUP") -> dict:
        content = await self.config_client.get_config(ConfigParam(
            data_id=data_id,
            group=group_name
        ))
        log.info(f"Service {self.service_name} {self.ip}:{self.port} success get config content from Nacos")
        return yaml.safe_load(content)

    async def get_one_healthy_instance(self, service_name: str,
                                       group_name: str = "DEFAULT_GROUP") -> Optional[Tuple[str, int]]:
        """获取一个健康实例的 (ip, port)：优先 HTTP 接口（健康状态可靠），
        过滤未启用实例后按权重随机挑选；无可用实例返回 None"""
        instances = await self.service_client.list_instances(
            request=ListInstanceParam(
                service_name=service_name,
                group_name=group_name,
                # 禁用缓存查询
                subscribe=False,
                healthy_only=True,
            )
        )
        hosts = [{"ip": ins.ip, "port": ins.port,
                  "healthy": ins.healthy, "enabled": True,
                  "weight": getattr(ins, "weight", 1.0)} for ins in instances]
        # 过滤掉不健康/被禁用的实例
        candidates = [(h["ip"], h["port"], float(h.get("weight") or 1.0))
                      for h in hosts if h.get("healthy") and h.get("enabled", True)]
        if not candidates:
            log.warning(f"No healthy instance found for service {service_name}")
            return None
        ip, port, weight = random.choices(candidates, weights=[w for _, _, w in candidates])[0]
        return ip, port

    async def get_all_healthy_instance(self, service_name: str,
                                       group_name: str = "DEFAULT_GROUP") -> list[
        tuple[str | int | bool | float | Any, str | int | bool | float | Any]]:
        """获取一个健康实例的 (ip, port)：优先 HTTP 接口（健康状态可靠），
        过滤未启用实例后按权重随机挑选；无可用实例返回 None"""
        # 兜底：HTTP 失败时回退 SDK gRPC 查询
        instances = await self.service_client.list_instances(
            request=ListInstanceParam(
                service_name=service_name,
                group_name=group_name,
                # 禁用缓存查询
                subscribe=False,
                healthy_only=True,
            )
        )
        hosts = [{"ip": ins.ip, "port": ins.port,
                  "healthy": ins.healthy, "enabled": True,
                  "weight": getattr(ins, "weight", 1.0)} for ins in instances]
        # 过滤掉不健康/被禁用的实例
        return [(h["ip"], h["port"]) for h in hosts if h.get("healthy") and h.get("enabled", True)]

    async def close_config_client(self):
        await self.config_client.shutdown()
        log.info(f"config_client shutdown success")
