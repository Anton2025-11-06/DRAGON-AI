# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：wanx2.1-t2i-turbo 原生异步任务（提交-轮询）。"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import MT_TEXT_TO_IMAGE, PROVIDER_DASHSCOPE
from common.common_model.base import (ModelResult, http_client, poll_task,
                                      register)
from common.common_model.text_to_image import TextToImageBase

_SUBMIT = "/api/v1/services/aigc/text2image/image-synthesis"


@register(MT_TEXT_TO_IMAGE, PROVIDER_DASHSCOPE)
class DashscopeTextToImage(TextToImageBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "wanx2.1-t2i-turbo"

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    def _hdr(self):
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def ainvoke(self, prompt: str, size: str = "1024*1024", n: int = 1,
                      wait: bool = True, **kwargs) -> ModelResult:
        origin = self._origin()
        r = await http_client().post(origin + _SUBMIT, headers={**self._hdr(), "X-DashScope-Async": "enable"},
                                     json={"model": self.model, "input": {"prompt": prompt},
                                           "parameters": {"size": size, "n": n, **self.extra}})
        r.raise_for_status()
        task_id = r.json().get("output", {}).get("task_id")
        if not wait:
            return ModelResult(task_id=task_id, raw=r.json())

        async def fetch():
            rr = await http_client().get(f"{origin}/api/v1/tasks/{task_id}", headers=self._hdr())
            rr.raise_for_status()
            return rr.json()

        snap = await poll_task(fetch, is_done=lambda d: d.get("output", {}).get("task_status") == "SUCCEEDED",
                               is_fail=lambda d: d.get("output", {}).get("task_status") == "FAILED")
        urls = [x.get("url") for x in snap.get("output", {}).get("results", []) if x.get("url")]
        return ModelResult(urls=urls, url=urls[0] if urls else None, task_id=task_id, raw=snap)
