import dataclasses
import logging


@dataclasses.dataclass
class Config:
    nacos_name = "nacos"
    nacos_password = "Inforeiot2025."
    nacos_server_address = "10.88.129.3:8848"
    nacos_namespace_id = "bigdata-python-service-test"
    nacos_log_level = logging.INFO
