# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：cogvideox-flash + image_url 异步图生视频。

真实协议同文生视频：提交 POST {base}/videos/generations（带 image_url）→ GET {base}/async-result/{id}
轮询 task_status=SUCCESS，结果取 video_result[0].url。图生视频用 image_url 参数（非 first_frame）。
"""
from common.common_constants.model_constant import MT_IMAGE_TO_VIDEO, PROVIDER_ZHIPU
from common.common_model.base import (ModelResult, http_client, poll_task,
                                      register)
from common.common_model.image_to_video import ImageToVideoBase

_SUBMIT = "/videos/generations"
_RESULT = "/async-result/"


@register(MT_IMAGE_TO_VIDEO, PROVIDER_ZHIPU)
class ZhipuImageToVideo(ImageToVideoBase):
    provider = PROVIDER_ZHIPU
    default_model = "cogvideox-flash"

    def _hdr(self):
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def ainvoke(self, image_url: str, prompt: str = "", size: str = "1024x1024",
                      fps: int = 30, wait: bool = True, **kwargs) -> ModelResult:
        base = self.config.base_url.rstrip("/")
        payload = {"model": self.model, "image_url": image_url,
                   "prompt": prompt or "让画面自然动起来", "size": size, "fps": fps,
                   **self.extra, **kwargs}
        r = await http_client().post(base + _SUBMIT, headers=self._hdr(), json=payload)
        r.raise_for_status()
        j = r.json()
        task_id = str(j.get("id") or j.get("task_id") or "")
        if not wait:
            return ModelResult(task_id=task_id, raw=j)

        async def fetch():
            rr = await http_client().get(f"{base}{_RESULT}{task_id}", headers=self._hdr())
            return rr.json()

        snap = await poll_task(
            fetch,
            is_done=lambda d: str(d.get("task_status", "")).upper() == "SUCCESS",
            is_fail=lambda d: str(d.get("task_status", "")).upper() == "FAIL",
            interval=5.0, timeout=900.0)
        vr = snap.get("video_result") or []
        url = vr[0].get("url") if vr else None
        return ModelResult(url=url, task_id=task_id, raw=snap)
