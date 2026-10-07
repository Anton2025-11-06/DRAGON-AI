import dataclasses
import logging
import os


@dataclasses.dataclass
class Config:
    nacos_name = os.environ.get("NACOS_NAME",'nacos')
    nacos_password = os.environ.get("NACOS_PASSWD", 'Jzh@616294')
    nacos_server_address = os.environ.get("NACOS_ADDR", '47.111.117.95:8848')
    nacos_namespace_id = os.environ.get("NACOS_NS_ID", 'dragon-ai')
    nacos_log_level = logging.INFO


