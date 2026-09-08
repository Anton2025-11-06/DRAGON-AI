/**
 * 模型类型常量（与后端 common/common_constants/model_constant.py 一一对应）。
 * 业务代码禁止出现模型类型魔法值，一律引用本文件。
 */

// 模型类别（tb_model.category 枚举，模型广场 7 类）
export const MODEL_CATEGORY_TEXT_GEN = 'TEXT_GEN'; // 文本生成
export const MODEL_CATEGORY_EMBEDDING = 'EMBEDDING'; // 向量化
export const MODEL_CATEGORY_RERANK = 'RERANK'; // 重排序
export const MODEL_CATEGORY_MULTIMODAL = 'MULTIMODAL'; // 多模态
export const MODEL_CATEGORY_IMAGE_GEN = 'IMAGE_GEN'; // 图片生成
export const MODEL_CATEGORY_AUDIO_GEN = 'AUDIO_GEN'; // 语音生成
export const MODEL_CATEGORY_VIDEO_GEN = 'VIDEO_GEN'; // 视频生成

/** 模型类别全集（as const 供类型推导） */
export const MODEL_CATEGORY_LIST = [
  MODEL_CATEGORY_TEXT_GEN,
  MODEL_CATEGORY_EMBEDDING,
  MODEL_CATEGORY_RERANK,
  MODEL_CATEGORY_MULTIMODAL,
  MODEL_CATEGORY_IMAGE_GEN,
  MODEL_CATEGORY_AUDIO_GEN,
  MODEL_CATEGORY_VIDEO_GEN,
] as const;

/** 模型类别联合类型（模型广场分类字段） */
export type ModelCategory = (typeof MODEL_CATEGORY_LIST)[number];

// 模型下拉类型（GET /models/list 的 type 参数）
export const MODEL_TYPE_TEXT = 'text';
export const MODEL_TYPE_TEXT_GEN = 'text_gen';
export const MODEL_TYPE_MULTIMODAL = 'multimodal';
export const MODEL_TYPE_EMBEDDING = 'embedding';
export const MODEL_TYPE_RERANK = 'rerank';
export const MODEL_TYPE_IMAGE = 'image';

/**
 * LLM 类节点可选模型类型（先选类型，再按类型拉取有权模型列表）
 */
export const LLM_TYPE_OPTIONS = [
  { label: '文本生成', value: MODEL_TYPE_TEXT_GEN },
  { label: '多模态', value: MODEL_TYPE_MULTIMODAL },
];