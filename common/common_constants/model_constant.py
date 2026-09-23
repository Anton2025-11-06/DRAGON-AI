# -*- coding: utf-8 -*-
"""模型类型常量（统一入口）。

业务代码中禁止出现模型类型魔法值，一律引用本模块。

【设计变更 2026-09】原 7 大类 category（TEXT_GEN/EMBEDDING/... 名为「类别」实则与
12 种真实能力对不齐）经确认全部废弃，tb_model.category 直接存 12 种 MT_* 细分类型，
common_model 注册/发现、网关分发、模型测试按钮、工作流大模型节点统一以 MT_* 为准。
本文件仍保留少量历史名（MODEL_CATEGORY_LABELS 等）作为兼容别名，指向 12 类型，避免
外部 import 断裂。
"""
from __future__ import annotations

# =====================================================================================
# common_model 12 种能力类型（category 枚举，tb_model.category 取值即这 12 个 code）
# - 供应商支持关系不硬编码：由各 (类型,供应商) 子类是否成功 @register 动态决定，
#   通过 ModelRegistry.providers_for(MT_*) / categories_for(provider) 反查。
# =====================================================================================
MT_TEXT_TO_TEXT = "text_to_text"          # 文生文（支持 stream）
MT_TEXT_EMBEDDING = "text_embedding"      # 文本向量
MT_TEXT_RERANK = "text_rerank"            # 文本重排
MT_IMAGE_EMBEDDING = "image_embedding"    # 图片向量
MT_MULTIMODAL_EMBEDDING = "multimodal_embedding"  # 多模态向量（文本+图片+视频，仅通义）
MT_TEXT_TO_IMAGE = "text_to_image"        # 文生图
MT_AUDIO_TO_TEXT = "audio_to_text"        # 音频转文字
MT_IMAGE_UNDERSTAND = "image_understand"  # 图片理解（支持 stream）
MT_VIDEO_UNDERSTAND = "video_understand"  # 视频理解（支持 stream）
MT_OCR = "ocr"                            # OCR
MT_IMAGE_TO_VIDEO = "image_to_video"      # 图生视频
MT_TEXT_TO_VIDEO = "text_to_video"        # 文生视频
MT_TEXT_TO_AUDIO = "text_to_audio"        # 文生音频

# 13 类型全集合（顺序即业务定义顺序，一个都不能少）
MODEL_TYPES_ALL = [
    MT_TEXT_TO_TEXT, MT_TEXT_EMBEDDING, MT_TEXT_RERANK, MT_IMAGE_EMBEDDING,
    MT_MULTIMODAL_EMBEDDING, MT_TEXT_TO_IMAGE, MT_AUDIO_TO_TEXT,
    MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR, MT_IMAGE_TO_VIDEO,
    MT_TEXT_TO_VIDEO, MT_TEXT_TO_AUDIO,
]

# 支持流式（astream）的类型集合：仅这三类实现 astream 抽象方法
MODEL_TYPES_STREAMABLE = {MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND}

# 可参与工具调用（OpenAI tools / tool_calls 协议）的类型集合：
# 只有对话族的文生文能输出 tool_calls 并由引擎回填 tool 消息，其余 12 类均不具备。
MODEL_TYPES_TOOL_CALLABLE = {MT_TEXT_TO_TEXT}

# 类型 → 中文名（模型广场分类字典 /models/categories 接口数据源）
MODEL_TYPE_LABELS = {
    MT_TEXT_TO_TEXT: "文生文",
    MT_TEXT_EMBEDDING: "文本向量",
    MT_TEXT_RERANK: "文本重排",
    MT_IMAGE_EMBEDDING: "图片向量",
    MT_MULTIMODAL_EMBEDDING: "多模态向量",
    MT_TEXT_TO_IMAGE: "文生图",
    MT_AUDIO_TO_TEXT: "音频转文字",
    MT_IMAGE_UNDERSTAND: "图片理解",
    MT_VIDEO_UNDERSTAND: "视频理解",
    MT_OCR: "OCR",
    MT_IMAGE_TO_VIDEO: "图生视频",
    MT_TEXT_TO_VIDEO: "文生视频",
    MT_TEXT_TO_AUDIO: "文生音频",
}

# ==================== 供应商 provider 标识（common_model 注册 + tb_model.provider） ====================
# 仅这 3 个 key 有 common_model 实现；其中 openai=通用 OpenAI 兼容客户端（base_url 可配），
# deepseek/kimi/豆包 等一切 OpenAI 兼容厂商均归入 openai（配各自 base_url + 管理端 key）。
PROVIDER_OPENAI = "openai"            # 通用 OpenAI 兼容客户端（base_url 可配置）
PROVIDER_DASHSCOPE = "dashscope"      # 通义千问
PROVIDER_ZHIPU = "zhipu"              # 智谱
PROVIDERS_ALL = [PROVIDER_OPENAI, PROVIDER_DASHSCOPE, PROVIDER_ZHIPU]
PROVIDER_LABELS = {
    PROVIDER_OPENAI: "OpenAI兼容",
    PROVIDER_DASHSCOPE: "通义千问",
    PROVIDER_ZHIPU: "智谱",
}

# =====================================================================================
# 兼容层：历史 7 大类名 / 工作流下拉 type 维度，全部重定向到 12 类型体系
# =====================================================================================
# 历史别名：外部仍以 MODEL_CATEGORY_LABELS 取分类字典时，返回 12 类型字典
MODEL_CATEGORY_LABELS = MODEL_TYPE_LABELS
# 历史默认类别值（ModelConfig.category 兜底）
MODEL_CATEGORY_TEXT_GEN = MT_TEXT_TO_TEXT

# ==================== 工作流 /models/list 的下拉 type 维度 ====================
# 下拉 type（前端筛选维度）→ 对应的 12 类型 code 集合（category 现直接存 12 类型）。
MODEL_TYPE_TEXT = "text"                  # 对话族：文生文/图片理解/视频理解/OCR
MODEL_TYPE_TEXT_GEN = "text_gen"          # 仅文生文
MODEL_TYPE_MULTIMODAL = "multimodal"      # 多模态理解：图片/视频理解 + OCR
MODEL_TYPE_EMBEDDING = "embedding"        # 向量：文本向量 + 图片向量
MODEL_TYPE_RERANK = "rerank"              # 文本重排
MODEL_TYPE_IMAGE = "image"                # 文生图
MODEL_TYPE_VIDEO = "video"                # 文生视频 + 图生视频
MODEL_TYPE_AUDIO = "audio"                # 文生音频 + 音频转文字

MODEL_TYPE_CATEGORY_MAP = {
    MODEL_TYPE_TEXT: [MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR],
    MODEL_TYPE_TEXT_GEN: [MT_TEXT_TO_TEXT],
    MODEL_TYPE_MULTIMODAL: [MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR],
    MODEL_TYPE_EMBEDDING: [MT_TEXT_EMBEDDING, MT_IMAGE_EMBEDDING, MT_MULTIMODAL_EMBEDDING],
    MODEL_TYPE_RERANK: [MT_TEXT_RERANK],
    MODEL_TYPE_IMAGE: [MT_TEXT_TO_IMAGE],
    MODEL_TYPE_VIDEO: [MT_TEXT_TO_VIDEO, MT_IMAGE_TO_VIDEO],
    MODEL_TYPE_AUDIO: [MT_TEXT_TO_AUDIO, MT_AUDIO_TO_TEXT],
    # 12 类型 code 直接作为下拉值时也支持（前端可按细分类型精确筛选）
    **{mt: [mt] for mt in MODEL_TYPES_ALL},
}

# 工作流 LLM 类节点默认下拉类型
MODEL_TYPE_DEFAULT = MODEL_TYPE_TEXT_GEN
# 未知 type 参数时的兜底类别（保持向后兼容的宽松语义）
MODEL_TYPE_FALLBACK_CATEGORIES = [MT_TEXT_TO_TEXT]
