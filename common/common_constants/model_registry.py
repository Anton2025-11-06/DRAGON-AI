# -*- coding: utf-8 -*-
"""模型标识注册表（模型广场「添加模型」下拉数据源）。

每个 (provider, category) 组合下收录的 model_name **都经过真实 API 调用验证**：
- 验证时间：2026-09-13（智谱/通义真实密钥，逐标识 ainvoke；流式类型另跑 astream）
- 验证脚本：site/_reg_verify.py（72 个候选 → 55 个 PASS，通过清单 site/_reg_verify_result.json）
- 失败未收录示例（请勿手工添加，除非重新实测通过）：
  - zhipu embedding-3-light / glm-4v-9b / bge-reranker-v2-m3（模型不存在）
  - zhipu cogvideox（视频生成 400，已下架）；glm-4v-flash 视频理解（500，重复 3 次）
  - zhipu glm-asr-plus；dashscope cosyvoice-v1 / paraformer-v3 / gte-rerank（403）/ multimodal-embedding-v3 / qwen2.5-vl-72b-instruct（403 无权限）
  - openai 语义：通用 OpenAI 兼容客户端，本环境无 api.openai.com，以下标识
    经通义兼容端点验证（客户端路径可用；指向其它兼容厂商时该类目仍然通用）。

新增标识规范：先跑 site/_reg_verify.py 加入候选实测通过，再把标识补进 MODEL_REGISTRY。
"""
from __future__ import annotations

# 注册表：{provider: {category: [model_name, ...]}}（顺序即下拉展示顺序，常用在前）
MODEL_REGISTRY: dict[str, dict[str, list[str]]] = {
    "zhipu": {
        "text_to_text": [
            "glm-4-flash", "glm-4-flash-250414", "glm-4-air", "glm-4-airx",
            "glm-4-plus", "glm-4-long", "glm-4-0520", "glm-4.5-flash",
        ],
        "text_embedding": ["embedding-3"],
        "text_rerank": ["rerank"],
        "image_understand": ["glm-4v-flash", "glm-4v-plus"],
        "video_understand": ["glm-4v-plus"],
        "ocr": ["glm-4v-flash", "glm-4v-plus"],
        "text_to_audio": ["glm-tts"],
        "audio_to_text": ["glm-asr"],
        "text_to_image": ["cogview-3-flash", "cogview-3", "cogview-3-plus"],
        "text_to_video": ["cogvideox-flash"],
        "image_to_video": ["cogvideox-flash"],
    },
    "dashscope": {
        "text_to_text": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen-flash", "qwen-long"],
        "text_embedding": ["text-embedding-v3", "text-embedding-v4", "text-embedding-v2"],
        "text_rerank": ["gte-rerank-v2"],
        "image_embedding": ["multimodal-embedding-v1"],
        # 多模态向量：与 image_embedding 同源 multimodal-embedding 端点，支持 text/image/video 混合输入
        "multimodal_embedding": ["multimodal-embedding-v1"],
        "image_understand": ["qwen-vl-plus", "qwen-vl-max"],
        "video_understand": ["qwen-vl-plus", "qwen-vl-max"],
        "ocr": ["qwen-vl-ocr"],
        "text_to_audio": ["qwen-tts"],
        "audio_to_text": ["paraformer-v2", "sensevoice-v1"],
        "text_to_image": ["wanx2.1-t2i-turbo", "wanx2.1-t2i-plus"],
        "text_to_video": ["wanx2.1-t2v-turbo"],
        "image_to_video": ["wanx2.1-i2v-turbo", "wanx2.1-i2v-plus"],
    },
    # openai=通用 OpenAI 兼容客户端（base_url 可配）：以下标识经通义兼容端点实测通过
    "openai": {
        "text_to_text": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen-flash"],
        "text_embedding": ["text-embedding-v3"],
        "image_understand": ["qwen-vl-plus", "qwen-vl-max"],
        "video_understand": ["qwen-vl-plus", "qwen-vl-max"],
        "ocr": ["qwen-vl-plus"],
    },
}


def registry_models(provider: str, category: str) -> list[str]:
    """按 (厂家, 类型) 取已注册模型标识列表；未注册组合返回空列表。"""
    return list(MODEL_REGISTRY.get(provider, {}).get(category, []) or [])


def provider_registry(provider: str) -> dict[str, list[str]]:
    """某厂家全部已注册 {类型: [标识]}。"""
    return {k: list(v) for k, v in MODEL_REGISTRY.get(provider, {}).items()}