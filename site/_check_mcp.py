# -*- coding: utf-8 -*-
"""检查 mcp SDK 2.x 关闭 API（临时脚本）"""
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters
import inspect

print("has __aexit__:", hasattr(ClientSession, "__aexit__"))
print("has aclose:", hasattr(ClientSession, "aclose"))
print("has close:", hasattr(ClientSession, "close"))
print("StdioServerParameters:", inspect.signature(StdioServerParameters))
print("fields:", getattr(StdioServerParameters, "__dataclass_fields__", {}).keys())