from typing import Optional, Any

import httpx


class AsyncHttpPool:
    _instance: Optional["AsyncHttpPool"] = None
    _client: Optional[httpx.AsyncClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init(self, timeout: int = 30, max_connections: int = 500, max_keepalive_connections: int = 150,
             keepalive_expiry: int = 120):
        self._client = httpx.AsyncClient(timeout=timeout,
                                         trust_env=False,
                                         limits=httpx.Limits(max_connections=max_connections,
                                                             max_keepalive_connections=max_keepalive_connections,
                                                             keepalive_expiry=keepalive_expiry))

    @property
    def client(self):
        return self._client


httpx_pool = AsyncHttpPool()
