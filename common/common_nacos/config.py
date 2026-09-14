import dataclasses
import logging
import os


@dataclasses.dataclass
class Config:
    nacos_name = os.environ.get("NACOS_NAME")
    nacos_password = os.environ.get("NACOS_PASSWD")
    nacos_server_address = os.environ.get("NACOS_ADDR")
    nacos_namespace_id = os.environ.get("NACOS_NS_ID")
    nacos_log_level = logging.INFO


