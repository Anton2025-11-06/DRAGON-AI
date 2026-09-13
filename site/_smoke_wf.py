# -*- coding: utf-8 -*-
"""工作流 LLM 节点胶水逻辑冒烟：_build_kwargs(12 类型入参) + _map_output(下游可引用产出)。

不依赖引擎/网络，仅验证「按类型翻译入参」与「ModelResult→输出变量」映射的形态正确性，
覆盖用户要求：上游文本/文件/图片/音频/视频 输入衔接 + 下游 文本/向量/图片/音频/视频 输出引用。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_model.base import ModelResult
from service.service_workflow.workflow_engine.nodes.ai_nodes import LLMNodeExecutor


class FakeCtx:
    def __init__(self, vars_):
        self.vars = vars_
        self.inputs = {}

    def resolve(self, ref):
        return self.vars.get(ref)

    def render(self, tpl):
        return tpl


class FakeNode:
    def __init__(self, data):
        self.data = data
        self.id = "n1"
        self.label = "LLM"


def ex(data):
    return LLMNodeExecutor(FakeNode(data), None)


def check(name, cond):
    print(("OK " if cond else "FAIL") + " | " + name)
    assert cond, name


# ---- 上游输入变量：图片/音频/视频 url 由上游节点产出，节点用 VariableSelect 引用 ----
CTX = FakeCtx({
    "nodes.img_gen.urls": ["https://x/a.png"],
    "nodes.up_audio.audio_url": "https://x/a.wav",
    "nodes.up_video.video_url": "https://x/v.mp4",
    "nodes.start.docs": ["苹果手机", "香蕉"],
    "nodes.start.q": "苹果",
})

# 1) text_to_image：promptTemplate -> prompt + size/n
e = ex({"promptTemplate": "一只猫", "size": "1024x1024", "imageN": 2})
kw = e._build_kwargs(CTX, "text_to_image")
check("text_to_image prompt/size/n", kw["prompt"] == "一只猫" and kw["size"] == "1024x1024" and kw["n"] == 2)

# 2) image_understand：引用上游图片 url 列表
e = ex({"promptTemplate": "图里有啥", "imageVariable": "nodes.img_gen.urls"})
kw = e._build_kwargs(CTX, "image_understand")
check("image_understand prompt/image_urls", kw["image_urls"] == ["https://x/a.png"] and kw["prompt"] == "图里有啥")

# 3) ocr：图片来源 + thinking 透传（非 streamable 不加 thinking）
e = ex({"imageVariable": "https://x/a.png"})
kw = e._build_kwargs(CTX, "ocr")
check("ocr 字面量图片 url", kw["image_urls"] == ["https://x/a.png"])

# 4) video_understand：引用上游视频
e = ex({"promptTemplate": "讲了啥", "videoVariable": "nodes.up_video.video_url"})
kw = e._build_kwargs(CTX, "video_understand")
check("video_understand video_url", kw["video_url"] == "https://x/v.mp4")

# 5) audio_to_text：引用上游音频
e = ex({"audioVariable": "nodes.up_audio.audio_url"})
kw = e._build_kwargs(CTX, "audio_to_text")
check("audio_to_text audio_url", kw["audio_url"] == "https://x/a.wav")

# 6) text_embedding：inputVariable 单值
e = ex({"inputVariable": "nodes.start.q"})
kw = e._build_kwargs(CTX, "text_embedding")
check("text_embedding input", kw["input"] == "苹果")

# 7) text_rerank：query + documents 数组 + topN
e = ex({"queryVariable": "nodes.start.q", "documentsVariable": "nodes.start.docs", "topN": 1})
kw = e._build_kwargs(CTX, "text_rerank")
check("text_rerank query/documents/top_n",
      kw["query"] == "苹果" and kw["documents"] == ["苹果手机", "香蕉"] and kw["top_n"] == 1)

# 8) image_embedding / image_to_video / text_to_video / text_to_audio 形态
kw = ex({"imageVariable": "nodes.img_gen.urls"})._build_kwargs(CTX, "image_embedding")
check("image_embedding image_urls", kw["image_urls"] == ["https://x/a.png"])
kw = ex({"imageVariable": "nodes.img_gen.urls", "promptTemplate": "动起来"})._build_kwargs(CTX, "image_to_video")
check("image_to_video image_url/prompt", kw["image_url"] == "https://x/a.png" and kw["prompt"] == "动起来")
kw = ex({"promptTemplate": "走路"})._build_kwargs(CTX, "text_to_video")
check("text_to_video prompt", kw["prompt"] == "走路")
kw = ex({"promptTemplate": "你好世界", "voice": "Chelsie"})._build_kwargs(CTX, "text_to_audio")
check("text_to_audio text/voice", kw["text"] == "你好世界" and kw["voice"] == "Chelsie")

# 9) 空值剔除：无输入不应产生空 prompt 键
kw = ex({})._build_kwargs(CTX, "text_to_image")
check("空入参剔除", "prompt" not in kw and "size" not in kw)

print("\n---- 下游输出映射 _map_output ----")

# 文本类
out = ex({})._map_output("image_understand", ModelResult(content="有只狗", reasoning_content="想想看"), "output")
check("文本类 output/text/reasoning", out["output"] == "有只狗" and out["text"] == "有只狗" and out["reasoning"] == "想想看")

# 向量类
out = ex({})._map_output("text_embedding", ModelResult(vectors=[[0.1, 0.2, 0.3]]), "output")
check("向量 vectors/count/dimension", out["vectors"] == [[0.1, 0.2, 0.3]] and out["count"] == 1 and out["dimension"] == 3)

# 重排
out = ex({})._map_output("text_rerank", ModelResult(scores=[{"index": 0, "relevance_score": 0.9}]), "output")
check("重排 scores", out["scores"][0]["relevance_score"] == 0.9)

# 图片生成
out = ex({})._map_output("text_to_image", ModelResult(urls=["https://x/1.png", "https://x/2.png"]), "output")
check("图片 urls/url", out["urls"] == ["https://x/1.png", "https://x/2.png"] and out["url"] == "https://x/1.png")

# 视频生成
out = ex({})._map_output("text_to_video", ModelResult(url="https://x/v.mp4"), "output")
check("视频 url/video_url", out["url"] == "https://x/v.mp4" and out["video_url"] == "https://x/v.mp4")

# 音频生成
out = ex({})._map_output("text_to_audio", ModelResult(url="https://x/a.wav"), "output")
check("音频 url/audio_url", out["url"] == "https://x/a.wav" and out["audio_url"] == "https://x/a.wav")

# 音频转文字（文本产出）
out = ex({})._map_output("audio_to_text", ModelResult(content="转写文本"), "output")
check("ASR output/text", out["text"] == "转写文本")

print("\n工作流 LLM 节点胶水逻辑全部通过 ✅")
