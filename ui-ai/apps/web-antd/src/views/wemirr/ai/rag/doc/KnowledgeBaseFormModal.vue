<script lang="ts" setup>
import type { KbSaveReq, KbType, RuntimeConfig } from './api';

/**
 * 知识库创建 / 编辑弹窗。
 *
 * 为什么不用 fast-crud 表单：三类知识库的配置面板差异极大（文档型才有解析/分块/图谱，
 * 图片与音视频型只剩一枚「多模态描述增强」开关），条件分支配额远比 fast-crud 的列声明直观。
 * 所有可配项都来自后端 /runtime-config 的归一默认值（SPEC §13-2「不写死参数」），
 * 前端只按 kbType 决定显隐，不各自抄一份默认。
 *
 * 本轮（需求 4/5/6）的面板口径：
 * - 只剩两页。检索配置页整页撤掉：召回条数、阈值、权重、重排都是「这一次检索」的事，
 *   建库时填了也不生效（后端只在请求没给参数时拿库配置兜底），留在建库页只会误导用户；
 * - 音视频型不再有「媒体理解模型」这一位：整条媒体的描述改由视频理解 + 音频转文字两位
 *   分别产出（chatModelId 只剩存量透传，页面不给入口）；
 * - 解析配置页按需求 6 排成五行：①引擎与长度 ②分块策略及其动态项 ③预处理开关 ④模型
 *   ⑤增强开关。第五行的开关只在第四行配了对应模型时出现，开了也不给提示词输入框——
 *   提示词由后端内置（页面能改提示词等于把「同一篇文档两次解析结果不同」变成线上事故）。
 */
import { computed, reactive, ref, watch } from 'vue';

import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import {
  Col,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Radio,
  RadioGroup,
  Row,
  Select,
  Slider,
  Space,
  Switch,
  TabPane,
  Tabs,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';

import {
  CHUNK_STRATEGIES_ALL,
  CHUNK_STRATEGY_LABELS,
  CHUNK_STRATEGY_LEGACY,
  CreateKb,
  fetchAudioModels,
  fetchEmbedModels,
  fetchExtractModels,
  fetchImageModels,
  fetchOcrModels,
  fetchVideoModels,
  GetKbDetail,
  GetRuntimeConfig,
  KB_TYPE_AUDIO_VIDEO,
  KB_TYPE_DOC,
  KB_TYPE_IMAGE,
  KB_TYPE_LABELS,
  KB_TYPES_ALL,
  PARSE_ENGINE_LABELS,
  PARSE_ENGINES_ALL,
  RAG_VECTOR_DIM,
  UpdateKb,
} from './api';

const props = defineProps<{
  /** 新建时预选类型（编辑时以库实际类型为准并锁定） */
  defaultKbType?: KbType;
  /** 有值 = 编辑；无值 = 新建 */
  editId?: null | number;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void;
  (e: 'success'): void;
}>();

const isEdit = computed(() => !!props.editId);
const submitting = ref(false);

// ---------- 下拉数据源 ----------
const embedOptions = ref<Array<{ label: string; value: number }>>([]);
const extractOptions = ref<Array<{ label: string; value: number }>>([]);
const imageOptions = ref<Array<{ label: string; value: number }>>([]);
/** OCR 模型：与图片理解二选一（两个都配时后端优先用图片理解） */
const ocrOptions = ref<Array<{ label: string; value: number }>>([]);
/** 音频转文字 / 视频理解：值存 parse_config，不占库表列 */
const audioOptions = ref<Array<{ label: string; value: number }>>([]);
const videoOptions = ref<Array<{ label: string; value: number }>>([]);
const runtime = ref<null | RuntimeConfig>(null);

/**
 * 向量维度：只读展示，表单里没有它的输入框（提交体 KbSaveReq 也不带维度字段）。
 * 取值优先级：编辑态用这条库自己的 embeddingDim，新建态用 /runtime-config.vectorDim，
 * 两者都没回来才落到 RAG_VECTOR_DIM，保证这一行永远显示得出数字。
 */
const lockedDim = ref(0);
const vectorDim = computed(
  () => lockedDim.value || runtime.value?.vectorDim || RAG_VECTOR_DIM,
);

// ---------- 表单模型 ----------
const form = reactive<KbSaveReq>({
  kbType: KB_TYPE_DOC,
  name: '',
  description: '',
  embedModelId: 0,
  // 重排、对话这两栏已从建库页撤掉：值只从详情原样带回来，避免保存一次就把存量模型清零
  rerankModelId: 0,
  chatModelId: 0,
  extractModelId: 0,
  imageModelId: 0,
  parserEngine: 'native',
  parseConfig: {},
  chunkConfig: {},
  enableGraph: false,
});
const activeTab = ref('basic');

// 分块策略相关：把 chunkConfig 摊出来好绑定
const chunkStrategy = computed<string>({
  get: () => form.chunkConfig?.strategy || 'fixed',
  set: (v) => (ensureChunk().strategy = v),
});
function ensureChunk(): Record<string, any> {
  if (!form.chunkConfig) form.chunkConfig = {};
  return form.chunkConfig;
}
function ensureParse(): Record<string, any> {
  if (!form.parseConfig) form.parseConfig = {};
  return form.parseConfig;
}
/** 只读地取解析配置（computed 里不能调 ensureParse，它会写 form） */
const parseCfg = computed<Record<string, any>>(() => form.parseConfig || {});

const isDoc = computed(() => form.kbType === KB_TYPE_DOC);
const isImage = computed(() => form.kbType === KB_TYPE_IMAGE);
const isAv = computed(() => form.kbType === KB_TYPE_AUDIO_VIDEO);

/** 图片侧「有没有可用的描述模型」：图片理解与 OCR 二选一，配了任一就算有 */
const hasImageDesc = computed(
  () =>
    Number(form.imageModelId) > 0 || Number(parseCfg.value.ocr_model_id) > 0,
);
const hasAudioModel = computed(() => Number(parseCfg.value.audio_model_id) > 0);
const hasVideoModel = computed(() => Number(parseCfg.value.video_model_id) > 0);

/** 引擎可选项（据 runtime.engines 的 available 置灰，mineru 未部署时不可选） */
const engineSelectOptions = computed(() =>
  PARSE_ENGINES_ALL.map((e) => {
    const info = runtime.value?.engines?.[e];
    const disabled =
      (e === 'mineru' && runtime.value && !runtime.value.mineruConfigured) ||
      (info && info.available === false);
    return {
      disabled: !!disabled,
      label: `${PARSE_ENGINE_LABELS[e]}${disabled ? '（不可用）' : ''}`,
      value: e,
    };
  }),
);

/**
 * 各引擎真读得动的文档格式。
 *
 * 清单来自后端 /runtime-config.engineFormats，数据源是引擎类自己的 formats 声明：
 * 前端自己列一份，改一次支持面就会有一处不说实话（doc/ppt 就是这么从声明里被剔掉的）。
 * auto 不单列：它是「按文档复杂度在引擎间路由」的调度器，能吃的就是并集。
 */
const engineCoverage = computed(() =>
  ['native', 'mineru']
    .map((e) => ({
      engine: e,
      formats: runtime.value?.engineFormats?.[e] || [],
      label: PARSE_ENGINE_LABELS[e] || e,
    }))
    .filter((x) => x.formats.length > 0),
);

/** 媒体后缀清单（同样来自后端）：hint 里要把「这一开关管哪几类文件」说具体而不是举个例 */
function mediaExtText(kind: string) {
  return (runtime.value?.mediaExts?.[kind] || []).join('/');
}

/**
 * 分块策略的说明与适用场景（需求 6 第二行：每个策略都要写清「是什么」和「什么时候用」）。
 *
 * 这段文字只服务面板，后端不读它：策略的真实行为在 common_file_parser/chunkers 里，
 * 这里说错了不改变解析结果，但会让用户按错的预期配库。
 */
const STRATEGY_META: Record<string, { desc: string; scene: string }> = {
  delimiter: {
    desc: '按分隔符优先级（空行 → 换行 → 句号…）找断开点，再按目标长度合并。',
    scene: '有自然段落与句界的文章、手册；比固定长度更少把整句斩成两段。',
  },
  excel: {
    desc: '每一块都强制带上原始表头行。',
    scene:
      '表格数据（xls/xlsx/csv 专属）：离开表头谁也看不出那一列数字是什么。',
  },
  fixed: {
    desc: '按字符数硬切，相邻块之间保留一段重叠。',
    scene: '没有明显结构的纯文本、长段落；也是其它策略不适用时的兜底档。',
  },
  page: {
    desc: '一页一块，块与块不交叉。',
    scene: '幻灯片、每页自成一体的报告（PDF/PPT 专属）。',
  },
  regex: {
    desc: '按你写的正则表达式切，命中处就是块边界。',
    scene:
      '法条、合同这种「第X条」固定开头的文档，切出来每一条就是一个完整条款。',
  },
  semantic: {
    desc: '算相邻句子的向量相似度，在语义突变处断开（要多付一次向量调用）。',
    scene: '话题频繁切换的访谈记录、会议纪要。',
  },
  title: {
    desc: '按 1~3 级标题切，一块基本就是一节，配合「标题面包屑前缀」效果最好。',
    scene: '制度、教材、技术文档等有清晰标题层级的内容。',
  },
};
const strategyMeta = computed(
  () => STRATEGY_META[chunkStrategy.value] ?? { desc: '', scene: '' },
);

/**
 * 开关的 ? 说明（需求 6：每个开关都要有个问号说干啥用的）。
 *
 * 写的是「关掉会怎样」而不是「本开关用于…」：这几个开关的真实代价都藏在关掉那一侧
 * （关掉不是报错而是搜不到），只写名字用户看不出少了什么。
 */
const PARSE_TIPS: Record<string, string> = {
  audio_understand:
    '文档内嵌的音频：关掉只把音频地址回填到正文原位（不丢音频），开了再调一次音频转文字模型把里面的话转成文字写在地址下面，检索才搜得到音频内容。按段付模型调用。内嵌音频只有 native 引擎能从 docx/pptx 包里取出字节。',
  enable_graph:
    '文档解析完成后自动抽取实体与关系建图谱，检索页那一路「知识图谱增强」才有数据可走。抽取是按篇烧大模型调用的，库很大时建议先只对需要的文档在文档列表手动构建。',
  image_understand:
    '文档内嵌的图片：关掉只把图片地址回填到正文原位（不丢图），开了再调一次图片理解（或 OCR）模型在地址下面补一段描述，检索才搜得到图里的内容。按张付模型调用。',
  keep_page_break:
    '在每页正文前插一个「第 N 页」标记块，切片里就带着页边界。选了「按页分块」不需要它；用其它策略又要求命中能回跳到具体页时才有用。默认关闭：标记会占掉单块长度。',
  media_desc_enhance:
    '关掉 = 一条媒体只付一次向量调用，切片正文只有文件名，关键词那一路只能按文件名命中；开了 = 再调模型生成综合描述，正文变成「文件名 + 描述」，向量也落在这段合成文本上，按文件名和内容都能搜到。',
  remove_header_footer:
    '删掉每页重复的页眉页脚（公司名、页码、密级水印）。不删的话每一块里都藏着同一串噪声，既占单块长度，又抬高这些词的文档频率，把真实内容挤下去。',
  remove_toc:
    '丢弃文档开头的目录页与目录条目。目录里的行全是正文里出现过的标题，留着会让同一句话在索引里出现两遍，BM25 会把目录当高频内容反复召回。',
  table_as_text:
    '表格渲染成 Markdown 文本块（保留 | 与表头分隔行）。关掉不会丢数据：退化成「列名: 值」逐行文本，但行与列的对齐关系没了，模型和人都难还原表格。除非下游明确吃不进 Markdown，建议保持开启。',
  title_path:
    '把「一级标题 › 二级标题」这段面包屑拼到送去向量化的文本前面（只改向量文本，不动页面上展示的正文）。有用：切片只有几百字，脱离章节就不知道它在哪一节，「报销标准是多少」这类问题就召不回藏在「第三章 财务制度」下面的那一块。建议保持开启。',
  video_understand:
    '文档内嵌的视频：关掉只把视频地址回填到正文原位，开了再调一次视频理解模型给画面提一段描述。内嵌视频只有 native 引擎能取出字节，走 minerU 时没有可增强的对象。',
};

// 策略可选项（runtime 未回前用全集）
const strategySelectOptions = computed(() => {
  const list = runtime.value?.strategies?.length
    ? runtime.value.strategies
    : [...CHUNK_STRATEGIES_ALL];
  return list.map((s) => ({
    label: CHUNK_STRATEGY_LABELS[s] || s,
    value: s,
  }));
});

/** 分隔符标签编辑 */
const separatorValue = ref<string[]>([]);
function syncSeparators() {
  separatorValue.value = [...(ensureChunk().separators || [])];
}
function onSeparatorsChange(v: any) {
  ensureChunk().separators = v as string[];
}

/**
 * 媒体模型的下拉把「未选」回显成空而不是 0：后端归一后的默认值是 0，
 * 直接丢给 Select 会在框里显示一个选不存在的选项 0，也清不掉。
 */
function blankMediaModels() {
  const p = ensureParse();
  for (const key of ['audio_model_id', 'video_model_id', 'ocr_model_id']) {
    p[key] = Number(p[key]) > 0 ? Number(p[key]) : undefined;
  }
}

/**
 * 按类型加载模型下拉；换类型时清掉不再适用的选择。
 * 向量模型在编辑态锁定，不重新拉也不会影响回显。
 *
 * 建库页需要理解的模型只有四组：图谱抽取、图片理解、OCR、音频转文字、视频理解；
 * 重排与对话模型已退出建库页（需求 5/8），不再拉列表。
 */
async function loadModelOptions(type: KbType) {
  const tasks: Promise<any>[] = [
    fetchEmbedModels(type).then((v) => (embedOptions.value = v)),
  ];
  extractOptions.value = [];
  imageOptions.value = [];
  ocrOptions.value = [];
  audioOptions.value = [];
  videoOptions.value = [];
  if (type === KB_TYPE_DOC) {
    tasks.push(fetchExtractModels().then((v) => (extractOptions.value = v)));
  }
  if (type === KB_TYPE_DOC || type === KB_TYPE_IMAGE) {
    tasks.push(
      fetchImageModels().then((v) => (imageOptions.value = v)),
      fetchOcrModels().then((v) => (ocrOptions.value = v)),
    );
  }
  if (type === KB_TYPE_DOC || type === KB_TYPE_AUDIO_VIDEO) {
    tasks.push(
      fetchAudioModels().then((v) => (audioOptions.value = v)),
      fetchVideoModels().then((v) => (videoOptions.value = v)),
    );
  }
  await Promise.all(tasks);
}

/**
 * 补齐分块配置里「模板直接点进去」的两个嵌套结构。
 *
 * 新建态由 runtime 的 chunkDefaults 带全，但编辑态是从库里原样摊开：存量库（以及
 * 早于 context_augment 上线的库）的 chunkConfig 里没这个键时，第一行的开关绑定会
 * 直接读 undefined.enabled 报错在白屏上，所以两条路径都过一遍这个兜底。
 */
function withChunkFallbacks() {
  const c = ensureChunk();
  if (!c.separators) c.separators = [];
  if (!c.context_augment) {
    c.context_augment = { enabled: false, before: 120, after: 120 };
  }
  return c;
}

/** 用 runtime-config 的归一默认值填充一份完整配置（新建时调用） */
function applyDefaults() {
  const r = runtime.value;
  form.parseConfig = { ...r?.parseDefaults };
  form.chunkConfig = { ...r?.chunkDefaults };
  blankMediaModels();
  withChunkFallbacks();
  syncSeparators();
}

async function initOnce() {
  try {
    runtime.value = await GetRuntimeConfig();
  } catch {
    runtime.value = null;
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    activeTab.value = 'basic';
    if (!runtime.value) await initOnce();
    if (props.editId) {
      const detail = await GetKbDetail(props.editId);
      Object.assign(form, {
        kbType: detail.kbType,
        name: detail.name,
        description: detail.description || '',
        embedModelId: detail.embedModelId,
        rerankModelId: detail.rerankModelId || 0,
        chatModelId: detail.chatModelId || 0,
        extractModelId: detail.extractModelId || 0,
        imageModelId: detail.imageModelId || 0,
        parserEngine: detail.parserEngine || 'native',
        parseConfig: { ...detail.parseConfig },
        chunkConfig: { ...detail.chunkConfig },
        enableGraph: !!detail.enableGraph,
      });
      // 存量库可能还存着已下线的「LangChain 递归分块」：回显成合并后的策略，与后端归一保持一致
      const legacyStrategy =
        CHUNK_STRATEGY_LEGACY[String(form.chunkConfig?.strategy || '')];
      if (legacyStrategy) form.chunkConfig!.strategy = legacyStrategy;
      // 同理，存量库的 parser_engine 可能还写着已下线的 docling：后端按 native 跑，
      // 这里也回显成 native，免得上拉下回来一个选不存在的选项
      if (!PARSE_ENGINES_ALL.includes(form.parserEngine as any)) {
        form.parserEngine = 'native';
      }
      blankMediaModels();
      withChunkFallbacks();
      lockedDim.value = detail.embeddingDim || 0;
      await loadModelOptions(detail.kbType);
      syncSeparators();
    } else {
      Object.assign(form, {
        kbType: props.defaultKbType || KB_TYPE_DOC,
        name: '',
        description: '',
        embedModelId: 0,
        rerankModelId: 0,
        chatModelId: 0,
        extractModelId: 0,
        imageModelId: 0,
        parserEngine: 'native',
        enableGraph: false,
      });
      lockedDim.value = 0;
      applyDefaults();
      await loadModelOptions(form.kbType);
    }
  },
);

/** 切换类型：重载模型、清掉跨类型残留选择、非文档型关掉图谱 */
watch(
  () => form.kbType,
  async (t) => {
    if (isEdit.value) return; // 编辑态类型锁定，理论不会触发
    form.embedModelId = 0;
    form.rerankModelId = 0;
    form.chatModelId = 0;
    form.extractModelId = 0;
    form.imageModelId = 0;
    if (t !== KB_TYPE_DOC) form.enableGraph = false;
    const p = ensureParse();
    // 页面上一藏就看不见的开关必须同时置 false：只删 UI 不清值，用户上一次勾的增强
    // 会跟着类型切换静静存进库里，下次换回来一看「开关自己开着」
    if (t !== KB_TYPE_DOC) {
      p.image_understand = false;
      p.audio_understand = false;
      p.video_understand = false;
    }
    if (t === KB_TYPE_DOC) p.media_desc_enhance = false;
    await loadModelOptions(t);
  },
);

/**
 * 提交前把与当前类型无关的配置位抹掉（需求 4/6）。
 *
 * 不做这一步，一个图片库会带着 audio_model_id / keep_table_header 这些它用不着的键，
 * 排查时看不出到底哪个生效；开关同理，不显示就不该有值。
 */
function sanitizeForType() {
  const p = ensureParse();
  if (isDoc.value) {
    p.media_desc_enhance = false;
  } else {
    p.image_understand = false;
    p.audio_understand = false;
    p.video_understand = false;
    // 媒体型库不走的模型位直接清零（Select 清空给的是 undefined，归一后是 0）
    if (isImage.value) {
      p.audio_model_id = 0;
      p.video_model_id = 0;
    } else {
      p.ocr_model_id = 0;
      form.imageModelId = 0;
    }
  }
  blankMediaModels();
}

async function handleSubmit() {
  if (!form.name?.trim()) {
    activeTab.value = 'basic';
    message.warning('请输入知识库名称');
    return;
  }
  if (!form.embedModelId) {
    activeTab.value = 'basic';
    message.warning('请选择向量模型');
    return;
  }
  if (isDoc.value && form.enableGraph && !form.extractModelId) {
    activeTab.value = 'basic';
    message.warning('开启知识图谱需要选择图谱抽取模型');
    return;
  }
  // 开了增强却没选模型，解析层只会塞一句降级警告（不失败）：用户看到的仍是「已完成」，
  // 但正文里一段描述也没有——这一类「什么都没报错什么都没发生」在提交前就拦住
  if (isDoc.value && parseCfg.value.image_understand && !hasImageDesc.value) {
    activeTab.value = 'parse';
    message.warning(
      '开启图片解析增强需要选择图片理解模型或 OCR 模型（二选一）',
    );
    return;
  }
  if (isDoc.value && parseCfg.value.audio_understand && !hasAudioModel.value) {
    activeTab.value = 'parse';
    message.warning('开启音频解析增强需要选择音频转文字模型');
    return;
  }
  if (isDoc.value && parseCfg.value.video_understand && !hasVideoModel.value) {
    activeTab.value = 'parse';
    message.warning('开启视频解析增强需要选择视频理解模型');
    return;
  }
  if (
    !isDoc.value &&
    parseCfg.value.media_desc_enhance &&
    !(isImage.value
      ? hasImageDesc.value
      : hasAudioModel.value || hasVideoModel.value)
  ) {
    activeTab.value = 'parse';
    message.warning(
      isImage.value
        ? '开启多模态描述增强需要选择图片理解模型或 OCR 模型（二选一）'
        : '开启多模态描述增强需要选择视频理解模型或音频转文字模型（按库里的媒体至少选一个）',
    );
    return;
  }

  sanitizeForType();
  const payload: KbSaveReq = {
    kbType: form.kbType,
    name: form.name.trim(),
    description: form.description?.trim() || undefined,
    embedModelId: form.embedModelId,
    // 这两栏已从建库页撤掉：值按详情原样透传，编辑一次不该顺手清零
    rerankModelId: form.rerankModelId || 0,
    chatModelId: form.chatModelId || 0,
    // 检索配置不再在建库页给（需求 5）：不送这个键，后端直接用归一默认值落库
    parseConfig: ensureParse(),
  };
  // 图片理解模型是公用配置（与开关无关）：就算没开图片增强，往库里传一张 jpg
  // 仍靠它转写成正文，跟着开关清零会顺手抹掉一个还在用的选择
  if (isDoc.value || isImage.value) {
    payload.imageModelId = form.imageModelId || 0;
  }
  if (isDoc.value) {
    payload.parserEngine = form.parserEngine;
    payload.chunkConfig = ensureChunk();
    payload.chunkSize = Number(ensureChunk().chunk_size) || undefined;
    payload.chunkOverlap = Number(ensureChunk().chunk_overlap) || 0;
    payload.enableGraph = !!form.enableGraph;
    // 开关关掉就不存模型选择，免得上一个图谱抽取模型的配置挂在库里说不清
    payload.extractModelId = form.enableGraph ? form.extractModelId || 0 : 0;
  }

  submitting.value = true;
  try {
    if (isEdit.value && props.editId) {
      await UpdateKb(props.editId, payload);
      message.success('保存成功');
    } else {
      await CreateKb(payload);
      message.success('创建成功');
    }
    emit('update:open', false);
    emit('success');
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <Modal
    :open="open"
    :confirm-loading="submitting"
    :width="960"
    :title="isEdit ? '编辑知识库' : '新建知识库'"
    @update:open="emit('update:open', $event)"
    @ok="handleSubmit"
  >
    <Form layout="vertical">
      <Tabs v-model:active-key="activeTab">
        <!-- ==================== 基础信息 ==================== -->
        <TabPane key="basic" tab="基础信息">
          <FormItem label="知识库类型" required>
            <RadioGroup
              v-model:value="form.kbType"
              :disabled="isEdit"
              button-style="solid"
            >
              <Radio
                v-for="t in KB_TYPES_ALL"
                :key="t"
                :value="t"
                :disabled="isEdit && t !== form.kbType"
              >
                {{ KB_TYPE_LABELS[t] }}
              </Radio>
            </RadioGroup>
            <div v-if="isEdit" class="hint">类型创建后不可修改</div>
          </FormItem>

          <FormItem label="名称" required>
            <Input
              v-model:value="form.name"
              :maxlength="128"
              placeholder="请输入知识库名称"
            />
          </FormItem>

          <FormItem label="描述">
            <Textarea
              v-model:value="form.description"
              :rows="2"
              :maxlength="500"
              placeholder="可选，说明这个库的用途"
            />
          </FormItem>

          <FormItem label="向量模型" required>
            <Select
              v-model:value="form.embedModelId"
              :options="embedOptions"
              :disabled="isEdit"
              placeholder="选择向量模型"
              show-search
              :filter-option="(i: string, o: any) => o.label.includes(i)"
            />
            <div class="hint">
              {{
                isDoc
                  ? '文本向量或多模态向量皆可'
                  : '图片/音视频库必须多模态向量'
              }}{{ isEdit ? '；创建后锁定，换模型请新建库' : '' }}
            </div>
          </FormItem>

          <FormItem label="向量维度">
            <Space :size="8">
              <span class="dim-value">{{ vectorDim }} 维</span>
              <Tag color="blue">固定，不可修改</Tag>
            </Space>
            <div class="hint">
              ES 索引的维度建库即锁定为
              {{ vectorDim }} 维，换维度等于换向量空间、历史切片全部作废，
              因此页面不给修改入口；选了非
              {{ vectorDim }} 维的模型，后端建库时直接拒
            </div>
          </FormItem>

          <!-- 需求 4：音视频型的「媒体理解模型」已删（描述改由视频理解 + 音频转文字两位分别产出）；
               需求 6：知识图谱不在解析五行里，它是库级能力，留在基础信息 -->
          <FormItem v-if="isDoc">
            <template #label>
              知识图谱
              <Tooltip :title="PARSE_TIPS.enable_graph">
                <QuestionCircleOutlined class="help-icon" />
              </Tooltip>
            </template>
            <Switch v-model:checked="form.enableGraph" />
          </FormItem>

          <FormItem
            v-if="isDoc && form.enableGraph"
            label="图谱抽取模型"
            required
          >
            <Select
              v-model:value="form.extractModelId"
              :options="extractOptions"
              placeholder="选择抽取实体与关系的模型"
              show-search
              :filter-option="(i: string, o: any) => o.label.includes(i)"
            />
          </FormItem>
        </TabPane>

        <!-- ==================== 解析配置（需求 6：五行排版） ==================== -->
        <TabPane key="parse" tab="解析配置">
          <!-- 第一行：解析引擎、单分块最大长度、重叠长度、图片表格上下文填充 -->
          <Row v-if="isDoc" :gutter="16">
            <Col :span="5">
              <FormItem label="解析引擎">
                <Select
                  v-model:value="form.parserEngine"
                  :options="engineSelectOptions"
                />
              </FormItem>
            </Col>
            <Col :span="5">
              <FormItem label="单分块最大长度">
                <InputNumber
                  v-model:value="ensureChunk().chunk_size"
                  :min="50"
                  :max="runtime?.maxChunkChars || 20000"
                  style="width: 100%"
                />
              </FormItem>
            </Col>
            <Col :span="4">
              <FormItem>
                <template #label>
                  重叠长度
                  <Tooltip
                    title="相邻两块重复的字符数：上一块末尾这一段在下一块里再出现一次，避开一句话被切成两块后谁都搜不全。只对「固定长度 / 自定义分隔符」两种策略生效，取 0 就是不重叠。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <InputNumber
                  v-model:value="ensureChunk().chunk_overlap"
                  :min="0"
                  :max="runtime?.maxChunkChars || 20000"
                  style="width: 100%"
                />
              </FormItem>
            </Col>
            <Col :span="10">
              <FormItem>
                <template #label>
                  图片表格上下文填充
                  <Tooltip
                    title="图片块与表格块本身几乎没有可读文字（一张图就是一行地址、一张表就是一堆分隔符），单独拿去向量化基本搜不到。打开后按这里设的字数把它在正文里的前一段 / 后一段拼进向量文本（展示的正文不变），图与表就能跟着四周的「如下图所示…」「下表是…」被召回。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Space :size="6" align="center">
                  <Switch
                    v-model:checked="ensureChunk().context_augment.enabled"
                  />
                  <template v-if="ensureChunk().context_augment?.enabled">
                    <span class="mini-label">前</span>
                    <InputNumber
                      v-model:value="ensureChunk().context_augment.before"
                      :min="0"
                      style="width: 72px"
                    />
                    <span class="mini-label">后</span>
                    <InputNumber
                      v-model:value="ensureChunk().context_augment.after"
                      :min="0"
                      style="width: 72px"
                    />
                  </template>
                </Space>
              </FormItem>
            </Col>
          </Row>

          <!-- 第二行：分块策略 + 策略的动态展示 -->
          <template v-if="isDoc">
            <Row :gutter="16">
              <Col :span="6">
                <FormItem label="分块策略">
                  <Select
                    v-model:value="chunkStrategy"
                    :options="strategySelectOptions"
                  />
                </FormItem>
              </Col>
              <Col v-if="chunkStrategy === 'title'" :span="4">
                <FormItem label="标题层级（1~3）">
                  <InputNumber
                    v-model:value="ensureChunk().title_level"
                    :min="1"
                    :max="3"
                    style="width: 100%"
                  />
                </FormItem>
              </Col>
              <Col v-if="chunkStrategy === 'semantic'" :span="6">
                <FormItem
                  label="语义相似度断开阈值"
                  tooltip="相邻两句的向量相似度低于这个值就在此断开；调低块更大、调高切得更碎"
                >
                  <Slider
                    v-model:value="ensureChunk().semantic_threshold"
                    :max="1"
                    :min="0"
                    :step="0.05"
                  />
                </FormItem>
              </Col>
              <Col v-if="chunkStrategy === 'regex'" :span="8">
                <FormItem label="正则表达式">
                  <Input
                    v-model:value="ensureChunk().regex_pattern"
                    placeholder="例如：第[一二三四五六七八九十]+章"
                  />
                </FormItem>
              </Col>
              <Col v-if="chunkStrategy === 'delimiter'" :span="10">
                <FormItem label="分隔符列表（按优先级）">
                  <Select
                    v-model:value="separatorValue"
                    mode="tags"
                    placeholder="输入后回车添加分隔符"
                    style="width: 100%"
                    @change="onSeparatorsChange"
                  />
                </FormItem>
              </Col>
              <Col v-if="chunkStrategy === 'excel'" :span="5">
                <FormItem label="每块强制附带表头">
                  <Switch v-model:checked="ensureChunk().keep_table_header" />
                </FormItem>
              </Col>
            </Row>
            <div class="strategy-meta">
              <span>{{ strategyMeta.desc }}</span>
              <span class="meta-scene">适合：{{ strategyMeta.scene }}</span>
            </div>

            <!-- 第三行：预处理开关，每一个都带 ? 说明 -->
            <div class="group-title">预处理</div>
            <Row :gutter="12">
              <Col :span="5">
                <FormItem>
                  <template #label>
                    标题面包屑前缀
                    <Tooltip :title="PARSE_TIPS.title_path">
                      <QuestionCircleOutlined class="help-icon" />
                    </Tooltip>
                  </template>
                  <Switch v-model:checked="ensureChunk().title_path" />
                </FormItem>
              </Col>
              <Col :span="4">
                <FormItem>
                  <template #label>
                    移除目录
                    <Tooltip :title="PARSE_TIPS.remove_toc">
                      <QuestionCircleOutlined class="help-icon" />
                    </Tooltip>
                  </template>
                  <Switch v-model:checked="ensureParse().remove_toc" />
                </FormItem>
              </Col>
              <Col :span="4">
                <FormItem>
                  <template #label>
                    移除页眉页脚
                    <Tooltip :title="PARSE_TIPS.remove_header_footer">
                      <QuestionCircleOutlined class="help-icon" />
                    </Tooltip>
                  </template>
                  <Switch
                    v-model:checked="ensureParse().remove_header_footer"
                  />
                </FormItem>
              </Col>
              <Col :span="6">
                <FormItem>
                  <template #label>
                    表格转 Markdown
                    <Tooltip :title="PARSE_TIPS.table_as_text">
                      <QuestionCircleOutlined class="help-icon" />
                    </Tooltip>
                  </template>
                  <Switch v-model:checked="ensureParse().table_as_text" />
                </FormItem>
              </Col>
              <Col :span="5">
                <FormItem>
                  <template #label>
                    保留分页标记
                    <Tooltip :title="PARSE_TIPS.keep_page_break">
                      <QuestionCircleOutlined class="help-icon" />
                    </Tooltip>
                  </template>
                  <Switch v-model:checked="ensureParse().keep_page_break" />
                </FormItem>
              </Col>
            </Row>
          </template>

          <!-- 第四行：模型位（图片理解与 OCR 二选一、音频转文字、视频理解） -->
          <div class="group-title">解析模型</div>
          <Row :gutter="16">
            <Col v-if="isDoc || isImage" :span="6">
              <FormItem>
                <template #label>
                  图片理解模型
                  <Tooltip
                    title="与 OCR 二选一（两个都配时优先用图片理解）。它同时服务三件事：给文档内嵌图片提描述、给上传的图片文件转写成正文、给图片库的「多模态描述增强」生成综合描述。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Select
                  v-model:value="form.imageModelId"
                  :options="imageOptions"
                  allow-clear
                  placeholder="图片理解（image_understand）"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
            <Col v-if="isDoc || isImage" :span="6">
              <FormItem>
                <template #label>
                  OCR 模型
                  <Tooltip
                    title="没配图片理解模型时用它：只把图里的文字读出来当描述。与图片理解二选一，两个都配时后端优先走图片理解。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Select
                  v-model:value="ensureParse().ocr_model_id"
                  :options="ocrOptions"
                  allow-clear
                  placeholder="OCR（与图片理解二选一）"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
            <Col v-if="isDoc || isAv" :span="6">
              <FormItem>
                <template #label>
                  音频转文字模型
                  <Tooltip
                    title="上传音频文件时把整条转写成正文，也是音视频库「多模态描述增强」里音频那一路的依赖项。没有它，音频只能按文件名搜到。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Select
                  v-model:value="ensureParse().audio_model_id"
                  :options="audioOptions"
                  allow-clear
                  placeholder="音频转文字（audio_to_text）"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
            <Col v-if="isDoc || isAv" :span="6">
              <FormItem>
                <template #label>
                  视频理解模型
                  <Tooltip
                    title="上传视频文件时把画面描述成正文，也是音视频库「多模态描述增强」里视频那一路的依赖项。没有它，视频只能按文件名搜到。"
                  >
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Select
                  v-model:value="ensureParse().video_model_id"
                  :options="videoOptions"
                  allow-clear
                  placeholder="视频理解（video_understand）"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
          </Row>

          <!-- 第五行：增强开关（按第四行配了什么模型条件显示；打开不给提示词输入框） -->
          <div class="group-title">解析增强</div>
          <Row :gutter="16">
            <Col v-if="isDoc && hasImageDesc" :span="6">
              <FormItem>
                <template #label>
                  图片解析增强
                  <Tooltip :title="PARSE_TIPS.image_understand">
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Switch v-model:checked="ensureParse().image_understand" />
              </FormItem>
            </Col>
            <Col v-if="isDoc && hasAudioModel" :span="6">
              <FormItem>
                <template #label>
                  音频解析增强
                  <Tooltip :title="PARSE_TIPS.audio_understand">
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Switch v-model:checked="ensureParse().audio_understand" />
              </FormItem>
            </Col>
            <Col v-if="isDoc && hasVideoModel" :span="6">
              <FormItem>
                <template #label>
                  视频解析增强
                  <Tooltip :title="PARSE_TIPS.video_understand">
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Switch v-model:checked="ensureParse().video_understand" />
              </FormItem>
            </Col>
            <Col v-if="!isDoc" :span="6">
              <FormItem>
                <template #label>
                  多模态描述增强
                  <Tooltip :title="PARSE_TIPS.media_desc_enhance">
                    <QuestionCircleOutlined class="help-icon" />
                  </Tooltip>
                </template>
                <Switch v-model:checked="ensureParse().media_desc_enhance" />
              </FormItem>
            </Col>
          </Row>
          <div
            v-if="isDoc && !hasImageDesc && !hasAudioModel && !hasVideoModel"
            class="hint"
          >
            上面配了对应的模型，这里才会出现相应开关：没有可调用的模型时不给开关，
            免得一勾看起来生效了实际什么也没变。描述提示词由后端内置，不开放配置。
          </div>
          <div v-else-if="!isDoc" class="hint">
            不论开关开闭，关键词那一路都能按文件名命中（媒体切片的正文里永远带着文件名）；
            开了才会再调一次模型把综合描述拼在文件名后面，向量也落在这段合成文本上。
            描述提示词由后端内置，不开放配置。可上传的后缀：图片
            {{ mediaExtText('image') || '—' }}、音频
            {{ mediaExtText('audio') || '—' }}、视频
            {{ mediaExtText('video') || '—' }}。
          </div>

          <!-- 引擎能力边界（清单取后端 engineFormats，前端不抄一份） -->
          <template v-if="isDoc">
            <div class="hint">
              能取到哪些内嵌媒体由引擎决定：内嵌音频/视频只有 native
              取得到字节（docx/pptx 媒体部件）；图片两个引擎都能拿到（native
              抽内嵌部件，minerU 用它自己导出的图片）。走 minerU
              时内嵌音频/视频取不到字节，正文里也就不会出现它们的地址。
              当前可上传的媒体后缀：图片
              {{ mediaExtText('image') || '—' }}、音频
              {{ mediaExtText('audio') || '—' }}、视频
              {{
                mediaExtText('video') || '—'
              }}，没选对应模型的媒体文件会解析失败
              （文档格式不受影响），转写结果与文件名一起分块入库。
            </div>

            <div class="group-title">解析引擎覆盖的文档格式</div>
            <div v-for="one in engineCoverage" :key="one.engine" class="hint">
              {{ one.label }}：{{ one.formats.join(' / ') || '无' }}
            </div>
            <div class="hint">
              上列是各引擎真读得动的文档格式（老版 Office 的 .doc/.ppt
              两个引擎都读不动，需先另存为 docx/pptx）。auto
              不单列：它按文档复杂度在引擎间路由，能吃的就是并集。
            </div>
          </template>
        </TabPane>
      </Tabs>
    </Form>
  </Modal>
</template>

<style scoped>
.group-title {
  margin: 4px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color-secondary, #666);
}

.hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

.dim-value {
  font-size: 15px;
  font-weight: 600;
}

.help-icon {
  margin-left: 4px;
  color: #8c8c8c;
}

.mini-label {
  font-size: 12px;
  color: var(--text-color-secondary, #888);
}

/* 策略说明跟在下拉后头：不写清楚用户只能靠名字猜，猜错了要整库重跑 */
.strategy-meta {
  margin: -4px 0 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-color-secondary, #999);
}

.strategy-meta .meta-scene {
  margin-left: 8px;
}
</style>
