# -*- coding: utf-8 -*-
"""模型类型常量（统一入口）。

业务代码中禁止出现模型类型魔法值（"TEXT_GEN"/"text_gen" 等），一律引用本模块：
- MODEL_CATEGORY_*：模型广场 tb_model.category 枚举（7 类）
- MODEL_TYPE_*：工作流 /models/list 的 type 参数值（下拉分类维度）
- MODEL_TYPE_CATEGORY_MAP：下拉类型 → 类别集合映射
"""
from __future__ import annotations

# ==================== 模型类别（tb_model.category 枚举） ====================
MODEL_CATEGORY_TEXT_GEN = "TEXT_GEN"       # 文本生成
MODEL_CATEGORY_EMBEDDING = "EMBEDDING"     # 向量化
MODEL_CATEGORY_RERANK = "RERANK"           # 重排序
MODEL_CATEGORY_MULTIMODAL = "MULTIMODAL"   # 多模态
MODEL_CATEGORY_IMAGE_GEN = "IMAGE_GEN"     # 图片生成
MODEL_CATEGORY_AUDIO_GEN = "AUDIO_GEN"     # 语音生成
MODEL_CATEGORY_VIDEO_GEN = "VIDEO_GEN"     # 视频生成

# 类别 → 中文名（模型广场分类字典 /models/categories 接口数据源）
MODEL_CATEGORY_LABELS = {
    MODEL_CATEGORY_TEXT_GEN: "文本生成",
    MODEL_CATEGORY_EMBEDDING: "向量化",
    MODEL_CATEGORY_RERANK: "重排序",
    MODEL_CATEGORY_MULTIMODAL: "多模态",
    MODEL_CATEGORY_IMAGE_GEN: "图片生成",
    MODEL_CATEGORY_AUDIO_GEN: "语音生成",
    MODEL_CATEGORY_VIDEO_GEN: "视频生成",
}

# ==================== 模型下拉类型（/models/list 的 type 参数） ====================
MODEL_TYPE_TEXT = "text"              # 文本 + 多模态（兼容旧调用方）
MODEL_TYPE_TEXT_GEN = "text_gen"      # 仅文本生成
MODEL_TYPE_MULTIMODAL = "multimodal"  # 仅多模态
MODEL_TYPE_EMBEDDING = "embedding"    # 向量化
MODEL_TYPE_RERANK = "rerank"          # 重排序
MODEL_TYPE_IMAGE = "image"            # 图片生成

# 下拉类型 → 模型类别集合
MODEL_TYPE_CATEGORY_MAP = {
    MODEL_TYPE_TEXT: [MODEL_CATEGORY_TEXT_GEN, MODEL_CATEGORY_MULTIMODAL],
    MODEL_TYPE_TEXT_GEN: [MODEL_CATEGORY_TEXT_GEN],
    MODEL_TYPE_MULTIMODAL: [MODEL_CATEGORY_MULTIMODAL],
    MODEL_TYPE_EMBEDDING: [MODEL_CATEGORY_EMBEDDING],
    MODEL_TYPE_RERANK: [MODEL_CATEGORY_RERANK],
    MODEL_TYPE_IMAGE: [MODEL_CATEGORY_IMAGE_GEN],
}

# 工作流 LLM 类节点默认下拉类型
MODEL_TYPE_DEFAULT = MODEL_TYPE_TEXT_GEN
# 未知 type 参数时的兜底类别（保持向后兼容的宽松语义）
MODEL_TYPE_FALLBACK_CATEGORIES = [MODEL_CATEGORY_TEXT_GEN]

# ==================== 非直连后缀自动匹配关键词 ====================
# 非直连模型 base_url 不带接口路径时，按接口类型在 suffixes 中匹配 url 关键词
MODEL_ENDPOINT_CHAT = "chat"        # 对话 /chat/completions 类后缀
MODEL_ENDPOINT_EMBEDDING = "embedding"  # 向量化 /embeddings 类后缀
MODEL_ENDPOINT_RERANK = "rerank"    # 重排序 /rerank 类后缀

# 各类接口的默认（兜底）完整路径：suffixes 未配置/未匹配到时拼接使用
MODEL_ENDPOINT_DEFAULT_CHAT = "/v1/chat/completions"
MODEL_ENDPOINT_DEFAULT_EMBEDDING = "/v1/embeddings"
MODEL_ENDPOINT_DEFAULT_RERANK = "/v1/rerank"