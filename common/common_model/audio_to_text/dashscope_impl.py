# -*- coding: utf-8 -*-
"""供应商 通义 dashscope：paraformer-v2 录音文件识别（异步提交-轮询-取转写文本）。"""
from urllib.parse import urlsplit

from common.common_constants.model_constant import MT_AUDIO_TO_TEXT, PROVIDER_DASHSCOPE
from common.common_model.base import (ModelResult, http_client, poll_task,
                                      register)
from common.common_model.audio_to_text import AudioToTextBase

_SUBMIT = "/api/v1/services/audio/asr/transcription"


@register(MT_AUDIO_TO_TEXT, PROVIDER_DASHSCOPE)
class DashscopeAudioToText(AudioToTextBase):
    provider = PROVIDER_DASHSCOPE
    default_model = "paraformer-v2"

    def _origin(self) -> str:
        s = urlsplit(self.config.base_url)
        return f"{s.scheme}://{s.netloc}"

    def _hdr(self):
        return {"Authorization": f"Bearer {self.config.api_key}"}

    async def ainvoke(self, audio_url: str = None, file_urls: list = None,
                      **kwargs) -> ModelResult:
        origin = self._origin()
        urls = file_urls or ([audio_url] if audio_url else [])
        r = await http_client().post(origin + _SUBMIT, headers={**self._hdr(), "X-DashScope-Async": "enable"},
                                     json={"model": self.model, "input": {"file_urls": urls},
                                           "parameters": {"channel_id": [0], **self.extra}})
        r.raise_for_status()
        task_id = r.json().get("output", {}).get("task_id")

        async def fetch():
            rr = await http_client().get(f"{origin}/api/v1/tasks/{task_id}", headers=self._hdr())
            return rr.json()

        snap = await poll_task(fetch, is_done=lambda d: d.get("output", {}).get("task_status") == "SUCCEEDED",
                               is_fail=lambda d: d.get("output", {}).get("task_status") == "FAILED")
        # 取转写结果 json（results[].transcription_url）
        text = ""
        for res in snap.get("output", {}).get("results", []):
            turl = res.get("transcription_url")
            if turl:
                tj = (await http_client().get(turl)).json()
                for t in tj.get("transcripts", []):
                    text += t.get("text", "")
                break
        return ModelResult(content=text, task_id=task_id, raw=snap)
