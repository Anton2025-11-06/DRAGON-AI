# -*- coding: utf-8 -*-
"""确认空凭据下 ClientConfig.username/password 保持为空（SDK 据此跳过换 token）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2.nacos import ClientConfigBuilder, GRPCConfig

cfg = (ClientConfigBuilder()
       .server_address("121.43.156.100:8848")
       .endpoint("121.43.156.100:8848")
       .username("").password("")
       .namespace_id("dragon-ai")
       .grpc_config(GRPCConfig(grpc_timeout=5000))
       .build())
print("username=[%s] password=[%s]" % (cfg.username, cfg.password))
print("will_skip_login =", not (cfg.username and cfg.password))
