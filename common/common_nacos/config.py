import dataclasses
import logging


@dataclasses.dataclass
class Config:
    nacos_name = "nacos"
    nacos_password = "nacos"
    nacos_server_address = "127.0.0.1:8848"
    nacos_namespace_id = "dragon-ai"
    nacos_log_level = logging.INFO


