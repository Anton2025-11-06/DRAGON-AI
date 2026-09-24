<script setup lang="ts">
import type {
  DynamicToolOption,
  ExecutableWorkflowOption,
  LLMNodeConfig,
  LlmToolBinding,
  LlmToolKind,
  McpServerOption,
  StructuredOutput,
  WorkflowVersionMode,
  WorkflowVersionResp,
} from '#/api/ai-workflow/types';

/**
 * LLM 节点配置表单（对齐 common_model 12 能力类型）
 * 依据所选模型登记的能力类型（category）+ 模型管理的能力位（supports_stream/supports_thinking）
 * 动态展示对应输入项；页面上没有展示的字段不会写进节点配置：
 * - 文生文：系统/用户提示词、流式、深度思考、Vision、工具、结构化输出
 * - 图片理解/视频理解/OCR：媒体输入变量 +（提示词）
 * 温度/max_tokens 等模型调用参数在 12 类上都不占节点字段：统一由表单底部
 * 「常用参数」编辑（默认取模型管理登记值），运行时合并进 model_params 透传厂商。
 * - 文本向量：文本输入；图片向量：图片输入
 * - 文本重排：查询变量 + 文档变量 + topN
 * - 文生图/文生视频/图生视频/文生音频/音频转文字：对应生成入参
 * 上游媒体/文本均以 {{节点.变量}} 引用，或直接粘贴 URL/文本（后端 _build_kwargs 解析）。
 */
import { computed, reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import {
  getWorkflowVersionHistory,
  listDynamicTools,
  listExecutableWorkflows,
  listMcpServers,
} from '#/api/ai-workflow';
import {
  LLM_TYPE_OPTIONS,
  MODEL_TYPES_MEMORY,
  MODEL_TYPES_STREAMABLE,
  MT_AUDIO_TO_TEXT,
  MT_IMAGE_EMBEDDING,
  MT_IMAGE_TO_VIDEO,
  MT_IMAGE_UNDERSTAND,
  MT_MULTIMODAL_EMBEDDING,
  MT_OCR,
  MT_TEXT_EMBEDDING,
  MT_TEXT_RERANK,
  MT_TEXT_TO_AUDIO,
  MT_TEXT_TO_IMAGE,
  MT_TEXT_TO_TEXT,
  MT_TEXT_TO_VIDEO,
  MT_VIDEO_UNDERSTAND,
} from '#/api/ai-workflow/const';
import { useAiWorkflowStore } from '#/store/ai-workflow';

import * as modelApi from '../../../model-plaza/api';
import ModelTryPanel from '../../../model-plaza/components/ModelTryPanel.vue';
import {
  invokeModel,
  parseExperienceResult,
} from '../../../model-plaza/experience/api';
import { ModelSelect } from '../model-select';
import { VariableInput, VariableSelector } from '../variable-selector';

// Props
interface Props {
  config: LLMNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: LLMNodeConfig): void;
}>();

const defaultStructuredOutput: StructuredOutput = {
  enabled: false,
  jsonSchema: '',
  description: '',
  strictMode: false,
};

const workflowStore = useAiWorkflowStore();

// 表单数据（含 12 能力类型全部入参字段）
const formData = reactive<
  {
    structuredOutput: StructuredOutput;
    // 配置里 tools 是可选项，但表单态必须始终有条数组可绑（空数组 = 没选工具）
    tools: LlmToolBinding[];
  } & LLMNodeConfig
>({
  modelId: undefined,
  modelName: '',
  modelType: MT_TEXT_TO_TEXT,
  systemPrompt: '',
  promptTemplate: '',
  thinking: false,
  streaming: true,
  outputVariable: '',
  inputVariable: '',
  imageVariable: '',
  audioVariable: '',
  videoVariable: '',
  queryVariable: '',
  documentsVariable: '',
  topN: undefined,
  size: '',
  imageN: undefined,
  voice: '',
  visionEnabled: false,
  imageVariables: [],
  structuredOutput: { ...defaultStructuredOutput },
  tools: [],
  emitToolResult: false,
  memoryEnabled: false,
  memoryLimit: 10,
  memoryScope: 'SELF',
  memoryNodes: [],
  memoryStrategy: 'DROP_OLDEST',
  memoryCompressModelId: undefined,
});

// ==================== 节点级常用参数（默认取模型管理登记的 common_params，可改值/新增/删除）====================
interface ParamRow {
  name: string;
  type: 'boolean' | 'integer' | 'number' | 'object' | 'string';
  value: any;
  desc?: string;
}

const PARAM_TYPE_OPTIONS: { label: string; value: ParamRow['type'] }[] = [
  { label: '布尔', value: 'boolean' },
  { label: '整数', value: 'integer' },
  { label: '浮点数', value: 'number' },
  { label: 'JSON', value: 'object' },
  { label: '字符串', value: 'string' },
];

const paramRows = ref<ParamRow[]>([]);

function addParam() {
  paramRows.value.push({ name: '', type: 'string', value: '', desc: '' });
  handleChange();
}
function removeParam(idx: number) {
  paramRows.value.splice(idx, 1);
  handleChange();
}
/** 切换参数类型时归一默认值，避免残留不匹配类型的旧值 */
function onParamTypeChange(p: ParamRow) {
  p.value =
    p.type === 'boolean' ? false : (p.type === 'object' ? '{}' : undefined);
  handleChange();
}
/** 按类型转换值（object 解析 JSON，数字转 number）；后端仅合并不再二次转型 */
function castParamRow(p: ParamRow): ParamRow {
  let value = p.value;
  if (p.type === 'object' && typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      /* 非法 JSON 保持字符串 */
    }
  } else if (
    (p.type === 'integer' || p.type === 'number') &&
    value !== '' &&
    value !== null &&
    value !== undefined
  ) {
    value = Number(value);
  }
  return {
    name: p.name?.trim() || '',
    type: p.type,
    value,
    desc: p.desc || '',
  };
}
// 所选模型在模型管理登记的能力位：开启了对应能力才展示开关，展示了才下发该参数
const modelCaps = ref({
  supportsFunctionCall: false,
  supportsStream: false,
  supportsThinking: false,
});
const capsLoading = ref(false);
/** 已发起过详情拉取的 modelId（config 回灌时避免重复请求） */
const capsRequestedFor = ref<number | undefined>(undefined);

function rowsFromCommonParams(detail: modelApi.ModelDetailRep): ParamRow[] {
  return (detail.common_params || []).map((p) => ({
    name: p.name || '',
    type: (p.type || 'string') as ParamRow['type'],
    value: p.default,
    desc: p.desc || '',
  }));
}

/** 入参 params 与当前参数行等价 → 判定为自身 emit 的回灌，不重建行 */
function isSameParams(params: any[]): boolean {
  if (params.length !== paramRows.value.length) return false;
  return params.every((p, i) => {
    const row = castParamRow(paramRows.value[i] as ParamRow);
    return (
      row.name === (p.name || '') &&
      row.type === (p.type || 'string') &&
      JSON.stringify({ v: row.value }) === JSON.stringify({ v: p.value })
    );
  });
}

/**
 * 拉取所选模型详情：能力位（流式/思考）+ 登记的常用参数。
 * seedParams=true 时用登记值预置参数行（用户选模型 / 手动导入 / 节点尚无已存参数）；
 * 完成后重新下发一次配置（可见性变化 → 不该存在的字段要被清掉）。
 */
async function loadModelMeta(seedParams: boolean) {
  const id = Number(formData.modelId);
  capsRequestedFor.value = id || undefined;
  if (!id) {
    modelCaps.value = {
      supportsFunctionCall: false,
      supportsStream: false,
      supportsThinking: false,
    };
    paramRows.value = [];
    handleChange();
    return;
  }
  capsLoading.value = true;
  try {
    const detail = await modelApi.GetDetail(id);
    modelCaps.value = {
      supportsFunctionCall: !!detail.supports_function_call,
      supportsStream: !!detail.supports_stream,
      supportsThinking: !!detail.supports_thinking,
    };
    if (seedParams) paramRows.value = rowsFromCommonParams(detail);
  } catch {
    // 详情拉不到时按「未开启」处理：宁可不显示/不下发流式思考，也不要把默认值写进配置
    modelCaps.value = {
      supportsFunctionCall: false,
      supportsStream: false,
      supportsThinking: false,
    };
    if (seedParams) paramRows.value = [];
  } finally {
    capsLoading.value = false;
  }
  handleChange();
}

watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      modelId: config.modelId,
      modelName: config.modelName || '',
      modelType: config.modelType || MT_TEXT_TO_TEXT,
      systemPrompt: config.systemPrompt || '',
      promptTemplate: config.promptTemplate || '',
      thinking: config.thinking ?? false,
      streaming: config.streaming ?? true,
      outputVariable: config.outputVariable || '',
      inputVariable: config.inputVariable || '',
      imageVariable: config.imageVariable || '',
      audioVariable: config.audioVariable || '',
      videoVariable: config.videoVariable || '',
      queryVariable: config.queryVariable || '',
      documentsVariable: config.documentsVariable || '',
      topN: config.topN,
      size: config.size || '',
      imageN: config.imageN,
      voice: config.voice || '',
      visionEnabled: config.visionEnabled ?? false,
      imageVariables: config.imageVariables || [],
      structuredOutput: config.structuredOutput
        ? { ...defaultStructuredOutput, ...config.structuredOutput }
        : { ...defaultStructuredOutput },
      emitToolResult: config.emitToolResult ?? false,
      memoryEnabled: config.memoryEnabled ?? false,
      memoryLimit: config.memoryLimit ?? 10,
      memoryScope: config.memoryScope || 'SELF',
      memoryNodes: config.memoryNodes || [],
      memoryStrategy: config.memoryStrategy || 'DROP_OLDEST',
      memoryCompressModelId: config.memoryCompressModelId,
    });
    // 回填已存参数行；自身 emit 触发的回灌不重建（避免输入丢失焦点、也避免覆盖异步种子结果）
    const incoming = Array.isArray(config.params) ? config.params : [];
    if (!isSameParams(incoming)) {
      paramRows.value = incoming.map((p) => ({
        name: p.name || '',
        type: (p.type || 'string') as ParamRow['type'],
        value: p.value,
        desc: p.desc || '',
      }));
    }
    // 工具绑定同口径：自身 emit 的回灌不重建数组，否则正在展开的子工作流行会被替掉
    const incomingTools = Array.isArray(config.tools) ? config.tools : [];
    if (!isSameTools(incomingTools)) {
      formData.tools = incomingTools.map((t) => ({ ...t }));
    }
    // 尚未发起过该模型详情拉取（首次渲染 / 外部改了模型）：拉能力位，节点无已存参数时顺带预置
    if (
      formData.modelId &&
      capsRequestedFor.value !== Number(formData.modelId)
    ) {
      loadModelMeta(incoming.length === 0);
    }
  },
  { immediate: true, deep: true },
);

// ==================== 能力类型派生显示开关 ====================

const cat = computed(() => formData.modelType || MT_TEXT_TO_TEXT);
const isChat = computed(() => cat.value === MT_TEXT_TO_TEXT);
// 提示词输入：文生文/图片理解/视频理解/OCR/文生图/文生视频/图生视频/文生音频
const showPrompt = computed(() =>
  [
    MT_IMAGE_TO_VIDEO,
    MT_IMAGE_UNDERSTAND,
    MT_OCR,
    MT_TEXT_TO_AUDIO,
    MT_TEXT_TO_IMAGE,
    MT_TEXT_TO_TEXT,
    MT_TEXT_TO_VIDEO,
    MT_VIDEO_UNDERSTAND,
  ].includes(cat.value),
);
// 流式/深度思考：能力类型支持 且 模型管理开启对应能力位，才展示、才下发
const streamCapable = computed(() =>
  MODEL_TYPES_STREAMABLE.includes(cat.value),
);
const showStream = computed(
  () => streamCapable.value && modelCaps.value.supportsStream,
);
const showThinking = computed(
  () => streamCapable.value && modelCaps.value.supportsThinking,
);
const showVision = computed(() => isChat.value);
const showStructured = computed(() => isChat.value);
const showInput = computed(() =>
  [MT_MULTIMODAL_EMBEDDING, MT_TEXT_EMBEDDING].includes(cat.value),
);
const showImageVar = computed(() =>
  [
    MT_IMAGE_EMBEDDING,
    MT_IMAGE_TO_VIDEO,
    MT_IMAGE_UNDERSTAND,
    MT_MULTIMODAL_EMBEDDING,
    MT_OCR,
  ].includes(cat.value),
);
const showVideoVar = computed(() =>
  [MT_MULTIMODAL_EMBEDDING, MT_VIDEO_UNDERSTAND].includes(cat.value),
);
const showAudioVar = computed(() => cat.value === MT_AUDIO_TO_TEXT);
const showRerank = computed(() => cat.value === MT_TEXT_RERANK);
const showSize = computed(() =>
  [MT_IMAGE_TO_VIDEO, MT_TEXT_TO_IMAGE, MT_TEXT_TO_VIDEO].includes(cat.value),
);
const showImageN = computed(() => cat.value === MT_TEXT_TO_IMAGE);
const showVoice = computed(() => cat.value === MT_TEXT_TO_AUDIO);

// ==================== 记忆（需求 1）====================

/**
 * 当前能力类型是否可注入历史
 * 向量/重排/音频转文字/文生音频没有可注入的文本位，不展示开关也不下发相关字段。
 */
const showMemory = computed(() => MODEL_TYPES_MEMORY.includes(cat.value));

const MEMORY_SCOPE_OPTIONS = [
  { label: '本节点', value: 'SELF' },
  { label: '指定节点', value: 'NODES' },
  { label: '整条工作流', value: 'WORKFLOW' },
];

const MEMORY_STRATEGY_OPTIONS = [
  { label: '丢弃最旧', value: 'DROP_OLDEST' },
  { label: '丢弃中间', value: 'DROP_MIDDLE' },
  { label: '丢弃最新', value: 'DROP_NEWEST' },
  { label: '自动压缩', value: 'COMPRESS' },
];

/** 超限策略的口径提示（与后端 apply_limit 一致） */
const memoryStrategyHint = computed(() => {
  switch (formData.memoryStrategy) {
    case 'COMPRESS': {
      return '把超出额度部分压缩成一条系统摘要（压缩失败自动降级为丢弃最旧）';
    }
    case 'DROP_MIDDLE': {
      return '保留最旧与最新的一半，砍掉中间（开场与当下都在）';
    }
    case 'DROP_NEWEST': {
      return '保留最早的几条，丢弃较新历史';
    }
    default: {
      return '保留最近的几条，丢弃更早历史';
    }
  }
});

/** 「指定节点」候选：画布上的其它大模型节点（只有大模型节点会写 llmMessages） */
const memoryNodeOptions = computed(() => {
  const canvas = workflowStore.canvasRef;
  const nodes = canvas ? canvas.getNodes() || [] : [];
  const options = nodes
    .filter(
      (node: any) => node.data?.nodeType === 'LLM' && node.id !== props.nodeId,
    )
    .map((node: any) => ({
      label: node.data?.label || node.id,
      value: node.id,
    }));
  // 已选但已被删除的节点保留展示，避免下拉回显为空、也让校验能报出丢节点
  (formData.memoryNodes || []).forEach((id) => {
    if (!options.some((opt) => opt.value === id)) {
      options.push({ label: `${id}（已删除）`, value: id });
    }
  });
  return options;
});

// ==================== 插入工具（仅文生文 + 模型登记了 supports_function_call）====================

/**
 * 工具区块是否可见。
 * 只有文生文有 function-call 通道，且要模型管理勾了能力位：两者缺一不展示也不下发，
 * 与流式/思考两个能力位同一口径（后端执行时还有一道闸门兼容历史图）。
 */
const showTools = computed(
  () => isChat.value && modelCaps.value.supportsFunctionCall,
);

/** 已选绑定的分组下发顺序（固定它，勾一下选不会把数组顺序换一遍） */
const TOOL_KIND_ORDER: LlmToolKind[] = ['TOOL', 'MCP', 'WORKFLOW'];

const mcpOptions = ref<McpServerOption[]>([]);
const functionToolOptions = ref<DynamicToolOption[]>([]);
const workflowOptions = ref<ExecutableWorkflowOption[]>([]);
const toolsLoading = ref(false);
const toolsLoadError = ref('');
/** 三类候选已拉过一轮：区块首次可见才拉，不能每打开一个 LLM 节点发三个请求 */
let toolOptionsLoaded = false;
/** 子工作流 id → 版本列表（只在某条选了「指定版本」才拉，接口连图快照一起返回） */
const versionCache = reactive<Record<string, WorkflowVersionResp[]>>({});
const versionsLoading = ref(false);

/** 已选工具数（控制「输出工具调用结果」开关只在有工具时出现） */
const hasTools = computed(() => formData.tools.length > 0);

async function loadToolOptions() {
  if (toolOptionsLoaded || toolsLoading.value) return;
  toolsLoading.value = true;
  toolsLoadError.value = '';
  try {
    const [mcps, tools, workflows] = await Promise.all([
      listMcpServers(),
      listDynamicTools(),
      listExecutableWorkflows(),
    ]);
    mcpOptions.value = mcps || [];
    functionToolOptions.value = tools || [];
    workflowOptions.value = workflows || [];
    toolOptionsLoaded = true;
  } catch (error: any) {
    toolsLoadError.value = error?.message || '工具候选加载失败';
  } finally {
    toolsLoading.value = false;
  }
}

// 能力位拉到后才试拉候选（模型详情是异步的，setup 阶段还看不出 showTools）
watch(showTools, (visible) => {
  if (visible) loadToolOptions();
});

/** 入参行与当前工具绑定等价 → 判定为自身 emit 的回灌，不重建数组 */
function isSameTools(tools: LlmToolBinding[]): boolean {
  if (tools.length !== formData.tools.length) return false;
  return tools.every((t, i) => {
    const row = formData.tools[i] as LlmToolBinding;
    return (
      row.kind === t.kind &&
      String(row.id) === String(t.id) &&
      row.apiKeyId === t.apiKeyId &&
      (row.versionMode || 'LATEST') === (t.versionMode || 'LATEST') &&
      row.version === t.version &&
      (row.workflowName || '') === (t.workflowName || '')
    );
  });
}

/** 子工作流的版本策略选项（语义同【工作流】节点表单） */
const VERSION_MODE_OPTIONS = [
  { label: '始终使用最新版本', value: 'LATEST' },
  { label: '指定版本', value: 'SPECIFIC' },
];

/** 按 id 取一条子工作流绑定（模板里的多个小下拉都读它） */
function workflowBinding(id: string): LlmToolBinding | undefined {
  return formData.tools.find(
    (t) => t.kind === 'WORKFLOW' && String(t.id) === id,
  );
}

function apiKeyOf(id: string): number | undefined {
  return workflowBinding(id)?.apiKeyId;
}

function workflowNameOf(id: string): string | undefined {
  return workflowBinding(id)?.workflowName;
}

function versionModeOf(id: string): WorkflowVersionMode {
  return workflowBinding(id)?.versionMode === 'SPECIFIC'
    ? 'SPECIFIC'
    : 'LATEST';
}

function versionOf(id: string): number | undefined {
  return workflowBinding(id)?.version;
}

/** 切版本策略：选「指定版本」才按需拉该条的版本列表；切回来就掉旧版本号 */
function handleVersionModeChange(id: string, mode: WorkflowVersionMode) {
  patchWorkflowBinding(id, {
    version: mode === 'SPECIFIC' ? workflowBinding(id)?.version : undefined,
    versionMode: mode === 'SPECIFIC' ? 'SPECIFIC' : 'LATEST',
  });
  if (mode === 'SPECIFIC' && !versionCache[id]) loadVersions(id);
}

/** 新勾上的一条：子工作流只有一把可用 key 时直接预选（它是必填项） */
function blankBinding(kind: LlmToolKind, id: string): LlmToolBinding {
  if (kind !== 'WORKFLOW') return { id, kind };
  const wf = workflowOptions.value.find((item) => String(item.id) === id);
  const keys = wf?.apiKeys || [];
  return {
    apiKeyId: keys.length === 1 ? keys[0]?.id : undefined,
    id,
    kind: 'WORKFLOW',
    versionMode: 'LATEST',
    workflowName: wf?.name || '',
  };
}

/** 某一类来源的已选 id（多选控件的 value） */
function idsOfKind(kind: LlmToolKind): string[] {
  return formData.tools.filter((t) => t.kind === kind).map((t) => String(t.id));
}

/**
 * 多选变更：已存在的项按 id 保留（子工作流的 key/版本不能因为重选丢了），
 * 新勾的补一条空白绑定，已取消的直接掉出去。
 */
function setKindIds(kind: LlmToolKind, ids: string[]) {
  const sameKind = formData.tools.filter((t) => t.kind === kind);
  const next = ids.map(
    (id) => sameKind.find((t) => String(t.id) === id) || blankBinding(kind, id),
  );
  formData.tools = TOOL_KIND_ORDER.flatMap((k) =>
    k === kind ? next : formData.tools.filter((t) => t.kind === k),
  );
  handleChange();
}

/** 子工作流某条绑定就地改字段 */
function patchWorkflowBinding(
  id: string,
  patch: Partial<LlmToolBinding>,
): void {
  const item = formData.tools.find(
    (t) => t.kind === 'WORKFLOW' && String(t.id) === id,
  );
  if (!item) return;
  Object.assign(item, patch);
  handleChange();
}

async function loadVersions(id: number | string) {
  const key = String(id);
  if (versionCache[key]) return;
  versionsLoading.value = true;
  try {
    versionCache[key] = await getWorkflowVersionHistory(id);
  } catch {
    versionCache[key] = [];
  } finally {
    versionsLoading.value = false;
  }
}

function apiKeyOptionsOf(id: number | string) {
  const wf = workflowOptions.value.find(
    (item) => String(item.id) === String(id),
  );
  return (wf?.apiKeys || []).map((item) => ({
    label: `${item.name}（${
      item.rateLimit > 0 ? `${item.rateLimit} 次/分钟` : '不限流'
    }）`,
    value: item.id,
  }));
}

function versionOptionsOf(id: number | string) {
  // 只有已发布的版本能被调用（口径同【工作流】节点表单）
  return (versionCache[String(id)] || [])
    .filter((item) => item.published)
    .map((item) => ({ label: `v${item.version}`, value: item.version }));
}

/** 子工作流显示名：候选列表里没有（已下线/取消发布）时用选择时的快照名兜底 */
function workflowLabel(id: number | string, snapshotName?: string): string {
  const wf = workflowOptions.value.find(
    (item) => String(item.id) === String(id),
  );
  if (wf) return wf.name;
  return snapshotName
    ? `${snapshotName}（已不可调用）`
    : `#${id}（已不可调用）`;
}

/** 工具多选选项（带登记名，已选但不在列表里的补一条展示，不让下拉回显为空） */
const toolSelectOptions = computed(() => {
  const options = functionToolOptions.value.map((item) => ({
    label: item.description ? `${item.name} - ${item.description}` : item.name,
    value: String(item.id),
  }));
  idsOfKind('TOOL').forEach((id) => {
    if (!options.some((opt) => opt.value === id)) {
      options.push({ label: `#${id}（工具已删除）`, value: id });
    }
  });
  return options;
});

const mcpSelectOptions = computed(() => {
  const options = mcpOptions.value.map((item) => ({
    label: item.name,
    value: String(item.id),
  }));
  idsOfKind('MCP').forEach((id) => {
    if (!options.some((opt) => opt.value === id)) {
      options.push({ label: `#${id}（连接已删除）`, value: id });
    }
  });
  return options;
});

const workflowSelectOptions = computed(() => {
  const options = workflowOptions.value.map((item) => ({
    label: `${item.name}（v${item.currentVersion}）`,
    value: String(item.id),
  }));
  formData.tools
    .filter((t) => t.kind === 'WORKFLOW')
    .forEach((t) => {
      const id = String(t.id);
      if (!options.some((opt) => opt.value === id)) {
        options.push({
          label: `${t.workflowName || id}（已不可调用）`,
          value: id,
        });
      }
    });
  return options;
});

/**
 * 下发前归一：按 kind 只带该带的字段。
 * LATEST 下不留旧版本号：留着它，切回「始终最新」的图会带着旧版本号跑。
 */
function normalizedTools(): LlmToolBinding[] {
  return formData.tools
    .filter((t) => t && t.id !== undefined && t.id !== null && `${t.id}` !== '')
    .map((t) => {
      if (t.kind !== 'WORKFLOW') return { id: t.id, kind: t.kind };
      const versionMode: WorkflowVersionMode =
        t.versionMode === 'SPECIFIC' ? 'SPECIFIC' : 'LATEST';
      return {
        apiKeyId: t.apiKeyId,
        id: t.id,
        kind: 'WORKFLOW' as LlmToolKind,
        version: versionMode === 'SPECIFIC' ? t.version : undefined,
        versionMode,
        workflowName: t.workflowName || '',
      };
    });
}

// ==================== 模型测试台（复用 ModelTryPanel，不影响配置字段保存）====================
const testOpen = ref(false);
const testLoading = ref(false);
const testRunning = ref(false);
const testDetail = ref<modelApi.ModelDetailRep | null>(null);
/** 当前用户在该模型上被授权的虚拟 api-key：测试只能以它走网关，管理端密钥不进浏览器 */
const testUserKey = ref('');
const testResult = ref<modelApi.ModelTestRep | null>(null);
const testError = ref<null | string>(null);
const getTestContainer = () =>
  typeof document === 'undefined' ? undefined : document.body;

/**
 * 打开测试台：详情只用来取能力位与登记的常用参数（不再取凭据），
 * 调用凭据取当前用户自己审批通过的虚拟 key。
 */
async function openModelTest() {
  const id = Number(formData.modelId);
  if (!id) {
    message.warning('请先选择模型');
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  testError.value = null;
  testUserKey.value = '';
  try {
    const [detail, myKeys] = await Promise.all([
      modelApi.GetDetail(id),
      modelApi.GetMyKeys(),
    ]);
    testDetail.value = detail;
    // my-keys 按申请倒序返回，取该模型最新一条已通过的 key
    testUserKey.value =
      (myKeys || []).find((k) => k.model_id === id)?.api_key || '';
    if (!testUserKey.value) {
      message.warning(
        '你还没有该模型的授权 API Key，请先在模型广场申请并通过审批',
      );
      return;
    }
    testOpen.value = true;
  } catch (error: any) {
    message.error(error?.message || '模型详情加载失败');
  } finally {
    testLoading.value = false;
  }
}

/**
 * 测试台提交：走网关 /api/model + 用户自己的授权 key，不碰 /models/test
 * （那个接口只服务于模型新增/编辑时的未保存凭据试跑，且已收归模型管理权限）。
 * 面板产出的 inputs 键（prompt/input/image_url/query/documents/…）就是网关 body 的原生字段。
 */
async function onTryRun(payload: {
  inputs: Record<string, any>;
  params: Record<string, any>;
  stream: boolean;
  thinking: boolean;
}) {
  const d = testDetail.value;
  const userKey = testUserKey.value;
  if (!d || !userKey) return;
  testRunning.value = true;
  testError.value = null;
  testResult.value = null;
  let accContent = '';
  let accReasoning = '';
  try {
    const res = await invokeModel(
      {
        apiKey: userKey,
        model: d.model_name,
        body: {
          ...payload.inputs,
          stream: payload.stream,
          thinking: payload.thinking,
          params: payload.params,
        },
      },
      (chunk) => {
        // 流式增量：逐块累积并回填结果区（面板读 result.data.content/reasoning 渲染）
        if (chunk.content) accContent += chunk.content;
        if (chunk.reasoningContent) accReasoning += chunk.reasoningContent;
        if (accContent || accReasoning) {
          testResult.value = {
            success: true,
            message: '输出中…',
            data: { content: accContent, reasoning: accReasoning || undefined },
          };
        }
      },
    );
    // 流式时网关不回填 raw，用累积正文兜底；非流式时 raw 即完整响应
    const parsed = parseExperienceResult(d.category, res.raw);
    const content = parsed.text || accContent;
    const reasoning = parsed.reasoning || accReasoning || undefined;
    testResult.value = {
      success: true,
      message: res.usage?.total_tokens
        ? `调用成功 · tokens ${res.usage.total_tokens}`
        : '调用成功',
      data: {
        content,
        reasoning,
        urls: parsed.urls,
        vectors: parsed.vectors,
        scores: parsed.scores,
      },
    };
  } catch (error: any) {
    testResult.value = null;
    testError.value = error?.message || '测试请求失败，请稍后重试';
  } finally {
    testRunning.value = false;
  }
}

/**
 * 下发节点配置：仅在页面上展示的字段才写入 config
 * （未展示 = 该能力类型/该模型不具备，保存时不该带这个参数）。
 * 温度/max_tokens 等调用参数不在此列：全部走底部「常用参数」（config.params）。
 */
function handleChange() {
  const config: LLMNodeConfig = {
    modelId: formData.modelId,
    modelName: formData.modelName,
    modelType: formData.modelType,
    outputVariable: formData.outputVariable,
    promptTemplate: formData.promptTemplate,
    systemPrompt: formData.systemPrompt,
    thinking: showThinking.value ? formData.thinking : undefined,
    streaming: showStream.value ? formData.streaming : undefined,
    inputVariable: formData.inputVariable,
    imageVariable: formData.imageVariable,
    audioVariable: formData.audioVariable,
    videoVariable: formData.videoVariable,
    queryVariable: formData.queryVariable,
    documentsVariable: formData.documentsVariable,
    topN: formData.topN,
    size: formData.size || undefined,
    imageN: formData.imageN,
    voice: formData.voice || undefined,
    visionEnabled: showVision.value ? formData.visionEnabled : undefined,
    imageVariables:
      showVision.value && formData.visionEnabled
        ? formData.imageVariables
        : undefined,
    structuredOutput: formData.structuredOutput.enabled
      ? { ...formData.structuredOutput }
      : undefined,
    // 工具只在文生文 + 模型开了工具调用能力时下发；一条没选也不下发（清掉旧图的残留）
    tools: showTools.value && hasTools.value ? normalizedTools() : undefined,
    // 「输出工具结果」只在真有工具时有意义：没工具时留着它只会让人以为它控制了正文输出
    emitToolResult:
      showTools.value && hasTools.value ? formData.emitToolResult : undefined,
    // 未开记忆时整组不下发：否则图里会多一堆用不上的开关字段
    memoryEnabled: showMemory.value ? formData.memoryEnabled : undefined,
    memoryLimit:
      showMemory.value && formData.memoryEnabled
        ? formData.memoryLimit
        : undefined,
    memoryScope:
      showMemory.value && formData.memoryEnabled
        ? formData.memoryScope
        : undefined,
    memoryNodes:
      showMemory.value &&
      formData.memoryEnabled &&
      formData.memoryScope === 'NODES'
        ? formData.memoryNodes
        : undefined,
    memoryStrategy:
      showMemory.value && formData.memoryEnabled
        ? formData.memoryStrategy
        : undefined,
    memoryCompressModelId:
      showMemory.value &&
      formData.memoryEnabled &&
      formData.memoryStrategy === 'COMPRESS'
        ? formData.memoryCompressModelId
        : undefined,
    params: paramRows.value.map((p) => castParamRow(p)),
  };
  emit('update:config', config);
}

/**
 * Vision 图像变量：从【选变量】弹层追加一条上游引用（去重），与 tags 手动输入并存。
 * 引用格式由 VariableSelector 统一生成（{{inputs.x}} / {{nodes.id.x}}），后端 ctx.resolve 解析。
 */
function addImageVariable(reference: string) {
  const list = formData.imageVariables || [];
  if (!reference || list.includes(reference)) return;
  formData.imageVariables = [...list, reference];
  handleChange();
}

// 类型/模型切换：清空无关输入 + 按新模型登记的能力位与常用参数重新拉取
async function handleTypeChange() {
  handleChange();
  await loadModelMeta(true);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-name="formData.modelName"
      v-model:model-type="formData.modelType"
      :type-options="LLM_TYPE_OPTIONS"
      @change="handleTypeChange"
    />

    <div class="test-bar">
      <a-button size="small" :loading="testLoading" @click="openModelTest">
        测试该模型
      </a-button>
      <span class="form-hint">按当前所选模型试跑（不影响配置保存）</span>
    </div>

    <!-- 文本输入（text_embedding） -->
    <a-form-item v-if="showInput" label="文本输入变量">
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        placeholder="待向量化的文本，如 {{start.text}}"
        @change="handleChange"
      />
      <div class="form-hint">可引用上游变量，或直接填写文本</div>
    </a-form-item>

    <!-- 重排：查询 + 文档 -->
    <template v-if="showRerank">
      <a-form-item label="查询变量（Query）">
        <VariableInput
          v-model="formData.queryVariable"
          :current-node-id="nodeId"
          placeholder="重排查询语句，如 {{start.query}}"
          @change="handleChange"
        />
      </a-form-item>
      <a-form-item label="文档变量（Documents）">
        <VariableInput
          v-model="formData.documentsVariable"
          :current-node-id="nodeId"
          placeholder="待重排文档数组变量，如 {{retrieval.documents}}"
          @change="handleChange"
        />
        <div class="form-hint">
          引用一个字符串数组变量（如知识检索的文档列表）
        </div>
      </a-form-item>
      <a-form-item label="保留数量 Top N">
        <a-input-number
          v-model:value="formData.topN"
          :min="1"
          placeholder="默认返回全部"
          style="width: 100%"
          @change="handleChange"
        />
      </a-form-item>
    </template>

    <!-- 图片输入变量 -->
    <a-form-item v-if="showImageVar" label="图片输入变量">
      <VariableInput
        v-model="formData.imageVariable"
        :current-node-id="nodeId"
        placeholder="图片 URL 或引用，如 {{start.image}}"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 视频输入变量 -->
    <a-form-item v-if="showVideoVar" label="视频输入变量">
      <VariableInput
        v-model="formData.videoVariable"
        :current-node-id="nodeId"
        placeholder="视频 URL 或引用，如 {{start.video}}"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 音频输入变量 -->
    <a-form-item v-if="showAudioVar" label="音频输入变量">
      <VariableInput
        v-model="formData.audioVariable"
        :current-node-id="nodeId"
        placeholder="音频 URL 或引用，如 {{start.audio}}"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 提示词（含文生文/理解/生成类） -->
    <a-form-item
      v-if="showPrompt"
      :label="isChat ? '用户提示词模板' : '提示词'"
    >
      <VariableInput
        v-model="formData.promptTemplate"
        :current-node-id="nodeId"
        placeholder="使用 {{变量名}} 引用上游节点输出"
        :multiline="true"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 生成参数 -->
    <a-form-item v-if="showSize" label="生成尺寸 size">
      <a-input
        v-model:value="formData.size"
        placeholder="如 1024x1024 / 1280*720，留空用模型默认"
        @change="handleChange"
      />
    </a-form-item>
    <a-form-item v-if="showImageN" label="生成图片数量">
      <a-input-number
        v-model:value="formData.imageN"
        :min="1"
        :max="4"
        placeholder="默认 1"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>
    <a-form-item v-if="showVoice" label="音色 voice">
      <a-input
        v-model:value="formData.voice"
        placeholder="如 Cherry / 中文女声，留空用模型默认"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 文生文：系统提示词 -->
    <a-form-item v-if="isChat" label="系统提示词">
      <a-textarea
        v-model:value="formData.systemPrompt"
        :rows="3"
        placeholder="设置 AI 的角色和行为"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 增强功能（流式 / 思考 / Vision：按模型能力位展示，不展示则不下发） -->
    <a-divider
      v-if="showStream || showThinking || showVision"
      orientation="left"
      style="font-size: 12px; margin: 16px 0 12px"
    >
      增强功能
    </a-divider>
    <a-row :gutter="16">
      <a-col v-if="showStream" :span="8">
        <a-form-item label="流式输出">
          <a-switch
            v-model:checked="formData.streaming"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col v-if="showThinking" :span="8">
        <a-form-item>
          <template #label>
            <span>
              深度思考
              <a-tooltip
                title="启用后模型返回推理过程（reasoning），仅模型管理开启思考能力的模型可选"
              >
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch
            v-model:checked="formData.thinking"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
      <a-col v-if="showVision" :span="8">
        <a-form-item>
          <template #label>
            <span>
              Vision
              <a-tooltip title="启用后可处理图像输入，需要模型支持视觉能力">
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch
            v-model:checked="formData.visionEnabled"
            @change="handleChange"
          />
        </a-form-item>
      </a-col>
    </a-row>

    <!-- Vision 配置（后端 _build_messages 拼多模态 content：text + image_url） -->
    <template v-if="showVision && formData.visionEnabled">
      <a-form-item label="图像变量">
        <div class="image-var-row">
          <a-select
            v-model:value="formData.imageVariables"
            mode="tags"
            class="image-var-select"
            placeholder="选上游图像变量，或粘贴图片 URL 回车"
            @change="handleChange"
          />
          <VariableSelector
            :current-node-id="nodeId"
            button-text="选变量"
            @select="addImageVariable"
          />
        </div>
        <div class="form-hint">
          列表只列上游节点输出的变量（开始节点文件参数、文生图输出等），可选多个
        </div>
      </a-form-item>
    </template>

    <!-- 工具：仅文生文且模型登记了 supports_function_call 时展示。
         三条来源都只「选上就算插入」、不配任何参数：工具入参定义、MCP 连接的
         tools/list、子工作流的开始节点入参都在引擎执行时才读（只有单独使用
         工作流/MCP/工具节点时才需要手配入参） -->
    <template v-if="showTools">
      <a-divider
        orientation="left"
        style="font-size: 12px; margin: 16px 0 12px"
      >
        工具
      </a-divider>
      <a-alert
        v-if="toolsLoadError"
        type="error"
        show-icon
        :message="toolsLoadError"
        style="margin-bottom: 12px"
      />

      <a-form-item label="MCP 连接">
        <a-select
          :value="idsOfKind('MCP')"
          mode="multiple"
          :loading="toolsLoading"
          :options="mcpSelectOptions"
          option-filter-prop="label"
          placeholder="选择一个或多个 MCP 连接"
          @change="(v: any) => setKindIds('MCP', (v || []).map(String))"
        />
        <div class="form-hint">
          插入粒度是整条连接：执行时读它的 tools/list，连接里有几个工具就算几个
        </div>
      </a-form-item>

      <a-form-item label="工具">
        <a-select
          :value="idsOfKind('TOOL')"
          mode="multiple"
          :loading="toolsLoading"
          :options="toolSelectOptions"
          option-filter-prop="label"
          placeholder="选择一个或多个已登记的工具"
          @change="(v: any) => setKindIds('TOOL', (v || []).map(String))"
        />
        <div class="form-hint">
          参数取工具登记里的定义，执行时现读，不需要在这里配
        </div>
      </a-form-item>

      <a-form-item label="工作流">
        <a-select
          :value="idsOfKind('WORKFLOW')"
          mode="multiple"
          :loading="toolsLoading"
          :options="workflowSelectOptions"
          option-filter-prop="label"
          placeholder="选择可调用的子工作流（已发布且配了可用 API Key）"
          @change="(v: any) => setKindIds('WORKFLOW', (v || []).map(String))"
        />
        <div class="form-hint">
          子执行在 workflow 服务内部直跑；子流程卡在审批会带着本节点一起挂起
        </div>
      </a-form-item>

      <!-- 每条子工作流的凭证与版本策略（apiKeyId / versionMode / version 只对这一类有意义） -->
      <div
        v-for="id in idsOfKind('WORKFLOW')"
        :key="`llm-tool-wf-${id}`"
        class="sub-workflow-row"
      >
        <div class="sub-workflow-name">
          {{ workflowLabel(id, workflowNameOf(id)) }}
        </div>
        <a-row :gutter="8">
          <a-col :span="12">
            <a-form-item
              label="执行用 API Key"
              :validate-status="apiKeyOf(id) ? '' : 'error'"
              :help="apiKeyOf(id) ? '' : '必选：决定用哪套凭证与限流额度'"
            >
              <a-select
                :value="apiKeyOf(id)"
                :options="apiKeyOptionsOf(id)"
                placeholder="选择 API Key"
                @change="(v: any) => patchWorkflowBinding(id, { apiKeyId: v })"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="版本">
              <a-select
                :value="versionModeOf(id)"
                :options="VERSION_MODE_OPTIONS"
                @change="(v: any) => handleVersionModeChange(id, v)"
              />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item v-if="versionModeOf(id) === 'SPECIFIC'" label="指定版本号">
          <a-select
            :value="versionOf(id)"
            :loading="versionsLoading"
            :options="versionOptionsOf(id)"
            placeholder="选择已发布的版本"
            @change="(v: any) => patchWorkflowBinding(id, { version: v })"
          />
        </a-form-item>
      </div>

      <a-form-item v-if="hasTools">
        <template #label>
          <span>
            输出工具调用结果
            <a-tooltip
              title="打开后每次工具调用的结果会作为内容推给客户端；关闭时只记节点输出与调试事件"
            >
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
            </a-tooltip>
          </span>
        </template>
        <a-switch
          v-model:checked="formData.emitToolResult"
          @change="handleChange"
        />
      </a-form-item>
    </template>

    <!-- 结构化输出配置（仅文生文） -->
    <template v-if="showStructured">
      <a-divider
        orientation="left"
        style="font-size: 12px; margin: 16px 0 12px"
      >
        结构化输出
      </a-divider>
      <a-form-item>
        <template #label>
          <span>
            启用结构化输出
            <a-tooltip title="启用后 LLM 将按照 JSON Schema 格式输出结构化数据">
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
            </a-tooltip>
          </span>
        </template>
        <a-switch
          v-model:checked="formData.structuredOutput.enabled"
          @change="handleChange"
        />
      </a-form-item>
      <template v-if="formData.structuredOutput.enabled">
        <a-form-item label="输出描述">
          <a-input
            v-model:value="formData.structuredOutput.description"
            placeholder="描述期望的输出格式"
            @change="handleChange"
          />
        </a-form-item>
        <a-form-item label="JSON Schema">
          <a-textarea
            v-model:value="formData.structuredOutput.jsonSchema"
            :rows="6"
            placeholder='{"type": "object", "properties": {...}}'
            @change="handleChange"
          />
          <div class="form-hint">
            定义输出的 JSON Schema，LLM 将严格按照此格式输出
          </div>
        </a-form-item>
        <a-form-item>
          <template #label>
            <span>
              严格模式
              <a-tooltip
                title="启用后将强制 LLM 严格遵循 Schema，可能影响输出质量"
              >
                <QuestionCircleOutlined
                  style="margin-left: 4px; color: #8c8c8c"
                />
              </a-tooltip>
            </span>
          </template>
          <a-switch
            v-model:checked="formData.structuredOutput.strictMode"
            @change="handleChange"
          />
        </a-form-item>
      </template>
    </template>

    <!-- 记忆：历史写在 node_states[节点id].llmMessages，同一个执行 id（即会话）内跨轮生效 -->
    <template v-if="showMemory">
      <a-divider
        orientation="left"
        style="margin: 16px 0 12px; font-size: 12px"
      >
        记忆
      </a-divider>
      <a-form-item>
        <template #label>
          <span>
            启用记忆
            <a-tooltip
              title="同一执行 id（等同于会话 id）内的历史问答自动带入下一轮"
            >
              <QuestionCircleOutlined
                style="margin-left: 4px; color: #8c8c8c"
              />
            </a-tooltip>
          </span>
        </template>
        <a-switch
          v-model:checked="formData.memoryEnabled"
          @change="handleChange"
        />
      </a-form-item>
      <template v-if="formData.memoryEnabled">
        <a-form-item label="记忆条数上限">
          <a-input-number
            v-model:value="formData.memoryLimit"
            :min="1"
            :max="100"
            style="width: 100%"
            @change="handleChange"
          />
          <div class="form-hint">
            按消息条数计（一轮 = 提问+回答 两条），上限 100
          </div>
        </a-form-item>
        <a-form-item label="记忆范围">
          <a-select
            v-model:value="formData.memoryScope"
            :options="MEMORY_SCOPE_OPTIONS"
            @change="handleChange"
          />
        </a-form-item>
        <a-form-item v-if="formData.memoryScope === 'NODES'" label="记忆节点">
          <a-select
            v-model:value="formData.memoryNodes"
            mode="multiple"
            :options="memoryNodeOptions"
            placeholder="选择取哪些大模型节点的历史"
            @change="handleChange"
          />
          <div class="form-hint">
            只有大模型节点会写入记忆，候选只列画布上的其它大模型节点
          </div>
        </a-form-item>
        <a-form-item label="超限策略">
          <a-select
            v-model:value="formData.memoryStrategy"
            :options="MEMORY_STRATEGY_OPTIONS"
            @change="handleChange"
          />
          <div class="form-hint">{{ memoryStrategyHint }}</div>
        </a-form-item>
        <template v-if="formData.memoryStrategy === 'COMPRESS'">
          <ModelSelect
            v-model:model-value="formData.memoryCompressModelId"
            :type-options="[]"
            :default-type="MT_TEXT_TO_TEXT"
            placeholder="选择压缩模型（仅文生文）"
            @change="handleChange"
          />
          <div class="form-hint">
            用一个文生文模型把被丢掉的旧历史摘要成一条；需先申请到该模型的调用权限
          </div>
        </template>
      </template>
    </template>

    <!-- 常用参数：默认取模型管理登记值（可改值/新增/删除）；运行时合并进 model_params -->
    <a-divider orientation="left" style="font-size: 12px; margin: 16px 0 12px">
      常用参数
    </a-divider>
    <div class="param-bar">
      <a-button
        size="small"
        :loading="capsLoading"
        :disabled="!formData.modelId"
        @click="loadModelMeta(true)"
      >
        从模型管理导入
      </a-button>
      <span class="form-hint">
        {{
          formData.modelId
            ? '全部调用参数（温度 / max_tokens 等）在此设置：默认沿用模型管理登记值，可改值或新增'
            : '选择模型后自动带出模型管理登记的常用参数'
        }}
      </span>
    </div>
    <div class="param-edit">
      <div class="param-edit__head">
        <span class="pc-name">参数名</span>
        <span class="pc-type">类型</span>
        <span class="pc-val">值</span>
        <span class="pc-desc">说明</span>
        <span class="pc-op"></span>
      </div>
      <div v-for="(p, i) in paramRows" :key="i" class="param-edit__row">
        <a-input
          v-model:value="p.name"
          class="pc-name"
          size="small"
          :maxlength="64"
          placeholder="如 temperature"
          @change="handleChange"
        />
        <a-select
          v-model:value="p.type"
          class="pc-type"
          size="small"
          :options="PARAM_TYPE_OPTIONS"
          @change="onParamTypeChange(p)"
        />
        <span class="pc-val">
          <a-switch
            v-if="p.type === 'boolean'"
            v-model:checked="p.value"
            size="small"
            @change="handleChange"
          />
          <a-input-number
            v-else-if="p.type === 'integer' || p.type === 'number'"
            v-model:value="p.value"
            size="small"
            :precision="p.type === 'integer' ? 0 : undefined"
            style="width: 100%"
            @change="handleChange"
          />
          <a-input
            v-else
            v-model:value="p.value"
            size="small"
            :placeholder="p.type === 'object' ? 'JSON' : '值'"
            @change="handleChange"
          />
        </span>
        <a-input
          v-model:value="p.desc"
          class="pc-desc"
          size="small"
          :maxlength="255"
          placeholder="参数说明"
          @change="handleChange"
        />
        <span class="pc-op">
          <a-button type="text" danger size="small" @click="removeParam(i)">
            <template #icon>
              <DeleteOutlined />
            </template>
          </a-button>
        </span>
      </div>
      <a-button
        type="dashed"
        size="small"
        block
        style="margin-top: 8px"
        @click="addParam"
      >
        <template #icon>
          <PlusOutlined />
        </template>
        添加参数
      </a-button>
      <div class="form-hint" style="margin-top: 6px">
        留空则使用模型登记的默认参数；此处可修改取值（覆盖模型默认）或新增模型未登记的参数，保存后在节点真实运行时生效
      </div>
    </div>

    <a-divider style="margin: 16px 0 12px" />

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: llm_output"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 模型测试台：按所选模型 detail 的 supports_*/common_params 驱动，试跑走网关 + 用户授权 key -->
    <a-modal
      :open="testOpen"
      title="模型测试"
      width="680px"
      :footer="null"
      :destroy-on-close="true"
      :get-container="getTestContainer"
      @cancel="testOpen = false"
    >
      <ModelTryPanel
        v-if="testDetail"
        :category="testDetail.category"
        :supports-stream="!!testDetail.supports_stream"
        :supports-thinking="!!testDetail.supports_thinking"
        :common-params="testDetail.common_params || []"
        :running="testRunning"
        :show-result="true"
        :result="testResult"
        :result-error="testError"
        submit-text="运行测试"
        @run="onTryRun"
      />
    </a-modal>
  </a-form>
</template>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .test-bar {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 16px;
  }

  .image-var-row {
    display: flex;
    gap: 4px;
    align-items: flex-start;

    .image-var-select {
      flex: 1;
      min-width: 0;
    }
  }

  .param-bar {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;
  }

  // 插入工具：每条子工作流的凭证/版本单独一块，与上方的多选拉开一点距离
  .sub-workflow-row {
    padding: 8px 10px 0;
    margin-bottom: 12px;
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 6px;

    .sub-workflow-name {
      margin-bottom: 6px;
      font-size: 12px;
      font-weight: 500;
      color: #262626;
      word-break: break-all;
    }

    :deep(.ant-form-item) {
      margin-bottom: 10px;
    }
  }

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }
}

.param-edit {
  width: 100%;
  padding: 8px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}

.param-edit__head,
.param-edit__row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1.4fr 1.6fr 40px;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.param-edit__head {
  font-size: 12px;
  color: #8c8c8c;
}

.pc-op {
  text-align: center;
}
</style>
