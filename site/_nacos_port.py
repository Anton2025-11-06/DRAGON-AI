# -*- coding: utf-8 -*-
"""探测 Nacos 远程端口连通性：8848(HTTP) / 9848(client gRPC) / 9849(server gRPC)。"""
import socket

HOST = "121.43.156.100"


def check(port, timeout=5):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((HOST, port))
        return f"{HOST}:{port} OPEN"
    except Exception as e:
        return f"{HOST}:{port} CLOSED ({type(e).__name__}: {e})"
    finally:
        s.close()


for p in (8848, 9848, 9849):
    print(check(p))
