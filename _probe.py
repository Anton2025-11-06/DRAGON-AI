# -*- coding: utf-8 -*-
"""环境探测：SDK 安装情况 + 三家 API 网络可达性。仅用于决策，非业务代码。"""
import importlib.util
import socket
import ssl
import urllib.request

for m in ["openai", "zhipuai", "dashscope", "httpx", "fastapi", "PIL"]:
    print("SDK", m, bool(importlib.util.find_spec(m)))

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

hosts = [
    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
    "https://api.openai.com/v1/chat/completions",
]
for u in hosts:
    try:
        req = urllib.request.Request(u, method="GET")
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        print("NET", u, getattr(resp, "status", "?"))
    except urllib.error.HTTPError as e:
        # 4xx/401/404 说明网络可达（有响应），只是鉴权/路径问题
        print("NET", u, "HTTP", e.code)
    except Exception as e:
        print("NET", u, "ERR", type(e).__name__, str(e)[:90])
