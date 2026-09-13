# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：wanx2.1-i2v-turbo 异步图生视频（提交-轮询）。"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import MT_IMAGE_TO_VIDEO, PROVIDER_DASHSCOPE
from common.common_model.base import (ModelResult, http_client, poll_task,
                                      register)
from common.common_model.image_to_video import ImageToVideoBase

_SUBMIT = "/api/v1/services/aigc/video-generation/video-synthesis"


@register(MT_IMAGE_TO_VIDEO, PROVIDER_DASHSCOPE)
class DashscopeImageToVideo(ImageToVideoBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "wanx2.1-i2v-turbo"

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    def _hdr(self):
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def ainvoke(self, image_url: str, prompt: str = "让画面自然动起来",
                      resolution: str = "480P", wait: bool = True, **kwargs) -> ModelResult:
        origin = self._origin()
        r = await http_client().post(origin + _SUBMIT, headers={**self._hdr(), "X-DashScope-Async": "enable"},
                                     json={"model": self.model,
                                           "input": {"prompt": prompt, "img_url": image_url},
                                           "parameters": {"resolution": resolution, **self.extra}})
        r.raise_for_status()
        task_id = r.json().get("output", {}).get("task_id")
        if not wait:
            return ModelResult(task_id=task_id, raw=r.json())

        async def fetch():
            rr = await http_client().get(f"{origin}/api/v1/tasks/{task_id}", headers=self._hdr())
            return rr.json()

        snap = await poll_task(fetch,
                               is_done=lambda d: d.get("output", {}).get("task_status") == "SUCCEEDED",
                               is_fail=lambda d: d.get("output", {}).get("task_status") == "FAILED",
                               interval=5.0, timeout=900.0)
        url = snap.get("output", {}).get("video_url")
        return ModelResult(url=url, task_id=task_id, raw=snap)
