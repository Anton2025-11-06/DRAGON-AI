import logging
import random
import socket
import yaml
from typing import Optional, Tuple
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
    """Nacos 服务发现 + 配置中心的进程级单例（与 common_mysql.AsyncMysqlClient 同一口径）。

    构造不收参数，连接与注册参数一律在 init() 传入：__new__ 返回的是同一个实例，
    在 __init__ 里收参数会让每次 NacosClient(...) 都重跑一遍、覆掉已有配置，
    再跟一个 init() 又重建一套 gRPC 长连接。业务侧统一 import 模块级的 nacos_client。
    """
    _instance: Optional["NacosClient"] = None
    # 注册信息（init 时落地）：未初始化前由这些默认值保证读到干净的空态
    server_address: str = ""
    service_name: str = ""
    group_name: str = "DEFAULT_GROUP"
    weight: float = 1.0
    ip: Optional[str] = None
    port: int = 0
    _service_client: Optional[NacosNamingService] = None
    _config_client: Optional[NacosConfigService] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def service_client(self) -> NacosNamingService:
        """服务发现客户端（未 init 就取用直接报错，与 mysql 的 engine 同一口径）"""
        if self._service_client is None:
            raise RuntimeError("Nacos client not initialized, call init() first")
        return self._service_client

    @property
    def config_client(self) -> NacosConfigService:
        """配置中心客户端（未 init 就取用直接报错）"""
        if self._config_client is None:
            raise RuntimeError("Nacos client not initialized, call init() first")
        return self._config_client

    async def init(self, *, user_name: str, password: str, server_address: str,
                   namespace_id: Optional[str] = None, service_name: str = "",
                   ip: Optional[str] = None, port: int = 0,
                   group_name: str = "DEFAULT_GROUP", weight: float = 1.0,
                   log_level: int = logging.INFO, log_dir: str = "./nacos_log") -> None:
        """建立 naming/config 两个客户端（幂等：已初始化则跳过，不重建 gRPC 长连接）

        :param server_address: Nacos 地址 host:port（也是自动探测本机 IP 的对端）
        :param service_name/ip/port/group_name/weight: 本服务的注册信息，供
            register_service 使用；只读配置不注册时（arq worker）可略
        :param ip: 留空则由 register_service 自动探测本机局域网 IP
        """
        if self._service_client is not None:
            log.info("Nacos client already initialized, skip re-init")
            return

        self.server_address = server_address
        self.service_name = service_name
        self.ip = ip
        self.port = port
        self.group_name = group_name
        self.weight = weight

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
        try:
            self._service_client = await NacosNamingService.create_naming_service(client_config)
            self._config_client = await NacosConfigService.create_config_service(client_config)
        except Exception as e:
            # 半初始化不能留下：否则下次 init 会认为已就绪而跳过，配置客户端永远是空的
            await self.close()
            log.error(f"Nacos connect failed: {str(e)}")
            raise
        log.info(f"Nacos client init success: {server_address}, "
                 f"namespace={namespace_id or 'public'}")

    async def close(self):
        """释放两个客户端并把单例打回未初始化（允许再次 init）。

        先置空再 shutdown：失败的关闭也要把状态推进，不让调用方retry 时拿到已关的连接。
        """
        naming, config = self._service_client, self._config_client
        self._service_client = self._config_client = None
        # 配置客户端先关（与旧 close_config_client 的关闭顺序一致）
        for name, client in (("config", config), ("naming", naming)):
            if client is None:
                continue
            try:
                await client.shutdown()
            except Exception as e:
                log.warning(f"Nacos {name} client shutdown failed: {e}")
        if naming is not None or config is not None:
            log.info("Nacos client closed")

    async def register_service(self):
        """注册服务到 Nacos：ip 未显式指定时自动探测本机局域网 IP
        使用持久实例（ephemeral=False），配套服务端 healthCheckMode=none：
        不依赖 Nacos 服务端 TCP 主动探测（跨网段/防火墙下常被误判为不健康），
        实例注册后保持 healthy；注意服务下线需显式注销（脚手架已实现）。"""
        if not self.service_name or not self.port:
            raise RuntimeError("Nacos 注册信息缺失：init() 需同时给出 service_name 与 port")
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
        """读一份 YAML 配置（data_id 通常为服务名）。

        读不到或不是对象直接报错：否则调用方拿着 None 会在下一句 .get() 上抛
        AttributeError，看不出真因是 Nacos 里没有这份配置。
        """
        content = await self.config_client.get_config(ConfigParam(
            data_id=data_id,
            group=group_name
        ))
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise RuntimeError(f"Nacos 配置为空或不是 YAML 对象："
                               f"data_id={data_id}, group={group_name}")
        log.info(f"Nacos config loaded: data_id={data_id}, group={group_name}")
        return data

    async def _list_healthy_instances(self, service_name: str,
                                      group_name: str = "DEFAULT_GROUP") -> list[Tuple[str, int, float]]:
        """列出某服务的健康实例 (ip, port, weight)（禁用缓存查询，取服务端当前视图）。

        两个对外方法共用的唯一一条查询路径；不健康实例已由 healthy_only 在服务端过滤。
        """
        instances = await self.service_client.list_instances(
            request=ListInstanceParam(
                service_name=service_name,
                group_name=group_name,
                # 禁用缓存查询
                subscribe=False,
                healthy_only=True,
            )
        )
        return [(ins.ip, ins.port, float(getattr(ins, "weight", 1.0) or 1.0))
                for ins in instances if ins.healthy]

    async def get_one_healthy_instance(self, service_name: str,
                                       group_name: str = "DEFAULT_GROUP") -> Optional[Tuple[str, int]]:
        """获取一个健康实例的 (ip, port)：按权重随机挑选；无可用实例返回 None"""
        candidates = await self._list_healthy_instances(service_name, group_name)
        if not candidates:
            log.warning(f"No healthy instance found for service {service_name}")
            return None
        ip, port, _weight = random.choices(candidates, weights=[w for _, _, w in candidates])[0]
        return ip, port

    async def get_all_healthy_instance(self, service_name: str,
                                       group_name: str = "DEFAULT_GROUP") -> list[Tuple[str, int]]:
        """获取全部健康实例的 (ip, port)：限流策略发布这类需要逐个网关广播的场景。"""
        return [(ip, port) for ip, port, _weight in
                await self._list_healthy_instances(service_name, group_name)]


# 进程级单例（与 common_mysql.mysql_client 同一用法：直接 import 使用，不自行构造）
nacos_client = NacosClient()
