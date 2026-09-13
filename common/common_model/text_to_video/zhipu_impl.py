# -*- coding: utf-8 -*-
"""供应商 智谱 zhipu：cogvideox-flash 异步文生视频（提交-轮询）。

真实协议（docs.bigmodel.cn「视频生成(异步)」+「查询异步结果」实测校正）：
- 提交：POST {base}/videos/generations  body={model, prompt} → 返回 {id, task_status:"PROCESSING"}
- 轮询：GET  {base}/async-result/{id}   → {task_status: PROCESSING|SUCCESS|FAIL, video_result:[{url, cover_image_url}]}
注意：查询端点是 async-result/{id}，**不是** videos/generations/{id}（早期误用导致一直轮询不到终态）。
"""
from common.common_constants.model_constant import MT_TEXT_TO_VIDEO, PROVIDER_ZHIPU
from common.common_model.base import (ModelResult, http_client, poll_task,
                                      register)
from common.common_model.text_to_video import TextToVideoBase

_SUBMIT = "/videos/generations"
_RESULT = "/async-result/"


@register(MT_TEXT_TO_VIDEO, PROVIDER_ZHIPU)
class ZhipuTextToVideo(TextToVideoBase):
    provider = PROVIDER_ZHIPU
    default_model = "cogvideox-flash"

    def _hdr(self):
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def ainvoke(self, prompt: str, size: str = "1024x1024",
                      fps: int = 30, wait: bool = True, **kwargs) -> ModelResult:
        base = self.config.base_url.rstrip("/")
        payload = {"model": self.model, "prompt": prompt, "size": size, "fps": fps,
                   **self.extra, **kwargs}
        r = await http_client().post(base + _SUBMIT, headers=self._hdr(), json=payload)
        r.raise_for_status()
        j = r.json()
        # 提交响应里任务 ID 字段是 id（AsyncResponse）
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
