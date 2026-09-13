/**
 * 模型类型常量（与后端 common/common_constants/model_constant.py 一一对应）。
 * 业务代码禁止出现模型类型魔法值，一律引用本文件。
 *
 * 【设计对齐 2026-09】tb_model.category 直接存 12 种能力类型 code（MT_*），
 * common_model 注册/发现、网关分发、模型测试按钮、大模型节点统一以 12 code 为准；
 * 模型登记只需 provider + category + model_name + base_url（厂商接口基础地址），
 * 接口端点由 common_model 各厂商子类按能力类型自行拼接。
 */

// ==================== 12 能力类型（tb_model.category 枚举，一个都不能少） ====================
export const MT_TEXT_TO_TEXT = 'text_to_text'; // 文生文（支持 stream）
export const MT_TEXT_EMBEDDING = 'text_embedding'; // 文本向量
export const MT_TEXT_RERANK = 'text_rerank'; // 文本重排
export const MT_IMAGE_EMBEDDING = 'image_embedding'; // 图片向量
export const MT_TEXT_TO_IMAGE = 'text_to_image'; // 文生图
export const MT_AUDIO_TO_TEXT = 'audio_to_text'; // 音频转文字
export const MT_IMAGE_UNDERSTAND = 'image_understand'; // 图片理解（支持 stream）
export const MT_VIDEO_UNDERSTAND = 'video_understand'; // 视频理解（支持 stream）
export const MT_OCR = 'ocr'; // OCR
export const MT_IMAGE_TO_VIDEO = 'image_to_video'; // 图生视频
export const MT_TEXT_TO_VIDEO = 'text_to_video'; // 文生视频
export const MT_TEXT_TO_AUDIO = 'text_to_audio'; // 文生音频

/** 12 能力类型全集（顺序即业务定义顺序，as const 供类型推导） */
export const MODEL_CATEGORY_LIST = [
  MT_TEXT_TO_TEXT,
  MT_TEXT_EMBEDDING,
  MT_TEXT_RERANK,
  MT_IMAGE_EMBEDDING,
  MT_TEXT_TO_IMAGE,
  MT_AUDIO_TO_TEXT,
  MT_IMAGE_UNDERSTAND,
  MT_VIDEO_UNDERSTAND,
  MT_OCR,
  MT_IMAGE_TO_VIDEO,
  MT_TEXT_TO_VIDEO,
  MT_TEXT_TO_AUDIO,
] as const;

/** 能力类型联合类型（tb_model.category / 模型广场分类字段） */
export type ModelCategory = (typeof MODEL_CATEGORY_LIST)[number];

/** 能力类型中文名（与后端 MODEL_TYPE_LABELS 对齐） */
export const MODEL_CATEGORY_LABELS: Record<string, string> = {
  [MT_TEXT_TO_TEXT]: '文生文',
  [MT_TEXT_EMBEDDING]: '文本向量',
  [MT_TEXT_RERANK]: '文本重排',
  [MT_IMAGE_EMBEDDING]: '图片向量',
  [MT_TEXT_TO_IMAGE]: '文生图',
  [MT_AUDIO_TO_TEXT]: '音频转文字',
  [MT_IMAGE_UNDERSTAND]: '图片理解',
  [MT_VIDEO_UNDERSTAND]: '视频理解',
  [MT_OCR]: 'OCR',
  [MT_IMAGE_TO_VIDEO]: '图生视频',
  [MT_TEXT_TO_VIDEO]: '文生视频',
  [MT_TEXT_TO_AUDIO]: '文生音频',
};

/** 支持流式（astream）的能力类型（与后端 MODEL_TYPES_STREAMABLE 对齐） */
export const MODEL_TYPES_STREAMABLE: string[] = [
  MT_TEXT_TO_TEXT,
  MT_IMAGE_UNDERSTAND,
  MT_VIDEO_UNDERSTAND,
];

// ==================== 供应商 provider（common_model 实现的 3 家） ====================
export const PROVIDER_OPENAI = 'openai'; // 通用 OpenAI 兼容客户端（base_url 可配置）
export const PROVIDER_DASHSCOPE = 'dashscope'; // 通义千问
export const PROVIDER_ZHIPU = 'zhipu'; // 智谱

export const PROVIDER_LIST = [
  PROVIDER_OPENAI,
  PROVIDER_DASHSCOPE,
  PROVIDER_ZHIPU,
] as const;

export type ModelProviderKey = (typeof PROVIDER_LIST)[number];

export const PROVIDER_LABELS: Record<string, string> = {
  [PROVIDER_OPENAI]: 'OpenAI兼容',
  [PROVIDER_DASHSCOPE]: '通义千问',
  [PROVIDER_ZHIPU]: '智谱',
};

/** 选定厂商时自动填入的默认接口基础地址（base_url，端点由 common_model 子类自拼） */
export const PROVIDER_DEFAULT_BASE_URL: Record<string, string> = {
  [PROVIDER_OPENAI]: '',
  [PROVIDER_DASHSCOPE]: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  [PROVIDER_ZHIPU]: 'https://open.bigmodel.cn/api/paas/v4/',
};

// ==================== 工作流 /models/list 的分组下拉筛选维度 ====================
// 与后端 MODEL_TYPE_CATEGORY_MAP 对应；传细分 12 code 也支持（后端 {mt: [mt]} 兜底）。
export const MODEL_TYPE_TEXT = 'text'; // 对话族：文生文/图片理解/视频理解/OCR
export const MODEL_TYPE_TEXT_GEN = 'text_gen'; // 仅文生文
export const MODEL_TYPE_MULTIMODAL = 'multimodal'; // 多模态理解：图片/视频理解 + OCR
export const MODEL_TYPE_EMBEDDING = 'embedding'; // 向量：文本向量 + 图片向量
export const MODEL_TYPE_RERANK = 'rerank'; // 文本重排
export const MODEL_TYPE_IMAGE = 'image'; // 文生图
export const MODEL_TYPE_VIDEO = 'video'; // 文生视频 + 图生视频
export const MODEL_TYPE_AUDIO = 'audio'; // 文生音频 + 音频转文字

/**
 * LLM（大模型）节点可选能力类型（12 细分类型，直接作为 /models/list 的 type 值，
 * 先选能力，再按能力拉取有权模型；节点按所选模型登记的 category 分发调用）。
 */
export const LLM_TYPE_OPTIONS: { label: string; value: string }[] =
  MODEL_CATEGORY_LIST.map((code) => ({
    label: MODEL_CATEGORY_LABELS[code] as string,
    value: code,
  }));

/** 对话族节点（问题分类器 / 参数提取器）仅文生文 */
export const CHAT_TYPE_OPTIONS: { label: string; value: string }[] = [
  { label: MODEL_CATEGORY_LABELS[MT_TEXT_TO_TEXT] as string, value: MT_TEXT_TO_TEXT },
];
