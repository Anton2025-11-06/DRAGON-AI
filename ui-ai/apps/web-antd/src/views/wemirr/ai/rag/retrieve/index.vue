<script lang="ts" setup name="RagKnowledgeRetrieve">
/**
 * 知识检索页（需求 4：从「知识库列表」行内按钮升级为独立菜单页）。
 *
 * 页面职责 = 「问一句话，看它到底从哪些库里捞回了什么」：
 * - 库可多选（空 = 全部可用库，后端按 ACL 白名单收敛，不会越权召回）；
 * - 三类知识库共用一个口子，召回逻辑由后端按 kb_type 自动切换，前端只按 chunkType 分模态渲染；
 * - 选了图片库才出现「图搜图」上传（先传公共存储换 URL，再随 query 一起发）；
 * - 检索方式按勾选给（需求 7）：只勾一路就是那一路 100%，两路都勾才按百分比分配权重，
 *   都不勾 = 让每个库沿用自己的配置；
 * - 重排模型按本次请求选（需求 8）：没选就明确传 rerank=false，不让库上的存量配置替用户做主；
 * - 图谱增强那一路（需求 9）只服务 doc 型，且必须配对话模型——它要先让大模型从问句里抽实体；
 * - 「图谱命中」页先把命中的实体按分数从高到低列出来，再画一张
 *   「实体—关系—实体」的小图，鼠标停在实体上（列表行或图上节点）就摊开那个实体的明细；
 *   小图按住任意处拖动 = 平移、滚轮 = 缩放，交互走 shared/graph-canvas（与图谱检索页同一份口径）；
 * - 选了对话模型就走流式问答，页面左右分栏：左边检索命中，右边模型逐字回答（不做记忆）。
 *
 * API 复用 ../doc/api.ts：那份文件是 RAG 模块契约的唯一来源（枚举、字段名都与
 * common_constants/rag_constant.py 静态对账），拆成两份只会让两边各漂一半。
 */
import type {
  KbOption,
  RetrieveHit,
  RetrieveReq,
  RetrieveResp,
} from '../doc/api';
import type { GraphHit } from '../shared/graph-canvas';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue';
import { useRoute } from 'vue-router';

import { useAccess } from '@vben/access';

import {
  FileSearchOutlined,
  PictureOutlined,
  SearchOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons-vue';
import {
  Alert,
  Button,
  Card,
  CheckboxGroup,
  Empty,
  Input,
  InputNumber,
  message,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  fetchChatModels,
  fetchRerankModels,
  KB_TYPE_AUDIO_VIDEO,
  KB_TYPE_DOC,
  KB_TYPE_IMAGE,
  KbOptions,
  KG_DEPTH_MAX,
  KG_DEPTH_MIN,
  KG_SCALE_MAX,
  KG_SCALE_MIN,
  kgEntityColor,
  RAG_AUDIO_EXTS,
  RECALL_CHUNK,
  RECALL_GRAPH,
  RECALL_LABELS,
  Retrieve,
  RETRIEVE_MODE_LABELS,
  RetrieveAsk,
  uploadQueryImage,
} from '../doc/api';
import { attachCanvasInteractions } from '../shared/graph-canvas';

const route = useRoute();
const { hasPermission } = useAccess();
// 页面准入与后端 retrieval_router 上的 @has_permission("ai:kb:search") 同一位；
// 没这个权限就不发检索请求，免得一搜就是一个 403 红条拼成谜
const allowed = computed(() => hasPermission('ai:kb:search'));

const options = ref<KbOption[]>([]);
const kbIds = ref<number[]>([]);
const query = ref('');
const imageUrl = ref('');
const topK = ref(5);
const scoreThreshold = ref<number | undefined>();
const uploading = ref(false);

/**
 * 需求 7：检索方式 = 勾选召回路，而不是选一个模式。
 * 后端只认 mode + vectorWeight 两个参数，这里按勾选结果推：单勾一路就是那一路 100%
 * （mode=KEYWORD/VECTOR，权重不用传），两路都勾才用百分比（mode=HYBRID + 权重），
 * 都不勾就两个都不传，让每个库沿用自己的 retrieve_config。
 */
const MODE_KEYWORD = 'KEYWORD';
const MODE_VECTOR = 'VECTOR';
const MODE_HYBRID = 'HYBRID';
const recallModes = ref<string[]>([MODE_KEYWORD, MODE_VECTOR]);
/** 向量那一侧的百分比（关键词侧 = 100 - 它），只在两路都勾时可编辑 */
const vectorPct = ref(70);

/** 需求 8：重排模型按本次请求选，空 = 不重排（不拿库上的存量配置替用户做主） */
const rerankModelId = ref<number | undefined>();
const rerankOptions = ref<Array<{ label: string; value: number }>>([]);
/** 需求 9：选了对话模型才有右栏流式回答；开图谱增强时它必填 */
const chatModelId = ref<number | undefined>();
const chatOptions = ref<Array<{ label: string; value: number }>>([]);
const withGraph = ref(false);
const graphDepth = ref(2);
const graphScale = ref(10);
// 需求 9（选 A）：默认只出精炼的实体+关系，不让图谱反查原文 chunk（会把上下文撑大、模型失焦）
const graphSourceChunks = ref(false);

const loading = ref(false);
const searched = ref(false);
const result = ref<null | RetrieveResp>(null);
const activeTab = ref('text');
// 展开完整正文的命中：按 ES 的 _id 语义拼 kbId_docId_chunkIndex，避免同文档多命中串在一起
const expanded = ref<Set<string>>(new Set());

// 右栏（流式回答）：每次检索从零开始——需求 9 明确「不做记忆」，累加历史只会让人
// 以为模型记得上一句，实际上后端每帧都是独立提问
const answering = ref(false);
const answer = ref('');
const answerError = ref('');
const answerInfo = ref<null | { citations?: number; tookMs?: number }>(null);
const answerBox = ref<HTMLElement | null>(null);
let abortCtl: AbortController | null = null;

const selectedOptions = computed(() =>
  options.value.filter((o) => kbIds.value.includes(o.id)),
);
const hasImageKb = computed(() =>
  selectedOptions.value.some((o) => o.kbType === KB_TYPE_IMAGE),
);
const hasDocKb = computed(() =>
  selectedOptions.value.some((o) => o.kbType === KB_TYPE_DOC),
);

/** 两路都勾 = 混合（才给百分比）；单勾 = 那一路；都不勾 = 交给库配置 */
const bothRecall = computed(
  () =>
    recallModes.value.includes(MODE_KEYWORD) &&
    recallModes.value.includes(MODE_VECTOR),
);
const onlyKeyword = computed(
  () =>
    recallModes.value.length === 1 && recallModes.value.includes(MODE_KEYWORD),
);
const onlyVector = computed(
  () =>
    recallModes.value.length === 1 && recallModes.value.includes(MODE_VECTOR),
);
const modeSent = computed<string | undefined>(() => {
  if (bothRecall.value) return MODE_HYBRID;
  if (onlyKeyword.value) return MODE_KEYWORD;
  if (onlyVector.value) return MODE_VECTOR;
  return undefined;
});
/** 百分比条上的两个数：单选时是锁死的 100/0，都不选时压根不显示（各库配置不同，不编数字） */
const weights = computed<null | { keyword: number; vector: number }>(() => {
  if (onlyKeyword.value) return { keyword: 100, vector: 0 };
  if (onlyVector.value) return { keyword: 0, vector: 100 };
  if (bothRecall.value) {
    return { keyword: 100 - vectorPct.value, vector: vectorPct.value };
  }
  return null;
});
const recallModeOptions = computed(() =>
  [MODE_KEYWORD, MODE_VECTOR].map((m) => ({
    label: RETRIEVE_MODE_LABELS[m],
    value: m,
  })),
);
/** 选了对话模型就走流式问答，并把展示区拆成左右两栏 */
const hasAnswerPane = computed(() => !!chatModelId.value);
/**
 * 图谱增强这一路可不可用：勾了库时只看有没有文档型库，一个库都没勾 = 全部可用库
 * （里面必然可能有 doc 型），不能让留空这个合法用法反而把开关藏掉。
 */
const graphAvailable = computed(
  () => selectedOptions.value.length === 0 || hasDocKb.value,
);
/** 开了图谱增强但没选对话模型：后端也会拒，这里提前把话说在按钮前头 */
const graphWithoutChat = computed(() => withGraph.value && !chatModelId.value);

const textHits = computed(() => hitsOf('text'));
const imageHits = computed(() => hitsOf(KB_TYPE_IMAGE));
const avHits = computed(() => hitsOf(KB_TYPE_AUDIO_VIDEO));
const graphEntities = computed<Record<string, any>[]>(
  () => result.value?.graph?.entities ?? [],
);
/**
 * 图谱命中：实体按分数从高到低列出，关系另画一张「实体—关系—实体」的小图，
 * 鼠标停在实体上（列表行与图上节点都算）就把接口回的那份明细摊开。
 */
const graphHitsSorted = computed<Record<string, any>[]>(() =>
  graphEntities.value.toSorted(
    (a, b) => Number(b.score || 0) - Number(a.score || 0),
  ),
);
/** 后端给的结构化关系；没头没尾的纯描述型关系在图上没有落点，不画 */
const kgEdges = computed<Record<string, any>[]>(() =>
  (result.value?.graph?.edges || []).filter((e: any) => e?.head && e?.tail),
);

function hitsOf(chunkType: string) {
  return (result.value?.hits || []).filter((h) => h.chunkType === chunkType);
}

const kbSelectOptions = computed(() =>
  options.value.map((o) => ({
    label: `${o.name}（${o.kbTypeLabel}）`,
    value: o.id,
  })),
);

function hitKey(h: RetrieveHit) {
  return `${h.kbId}_${h.docId}_${h.chunkIndex}`;
}

function toggleDetail(h: RetrieveHit) {
  const key = hitKey(h);
  const next = new Set(expanded.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expanded.value = next;
}

/**
 * 命中正文里的关键词高亮。
 *
 * 必须先转义再插 <mark>：切片正文是用户上传文档里的原文，直接 v-html 等于把
 * XSS 从文档内容直通到检索页。
 */
function highlight(text: string) {
  const kw = query.value.trim();
  const escaped = (text || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  if (!kw) return escaped;
  const parts = escaped.split(new RegExp(`(${escapeRegExp(kw)})`, 'gi'));
  return parts
    .map((p) =>
      p.toLowerCase() === kw.toLowerCase() ? `<mark>${p}</mark>` : p,
    )
    .join('');
}

function escapeRegExp(value: string) {
  return value.replaceAll(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`);
}

function formatDuration(sec: number) {
  if (!sec) return '';
  const m = Math.floor(sec / 60);
  return `${m}:${String(sec % 60).padStart(2, '0')}`;
}

/**
 * 命中分数摊成字段行时的取值口径。
 *
 * 某一侧没参与召回就是 0，缺字段才用「—」占位：字段整块消失会让「综合/向量/关键词」
 * 三栏对不上，用户读不出是哪一路没跑。
 */
function scoreText(value?: number) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(4);
}

/**
 * 命中行的字段清单：知识库 / 文件名 /〔分块号〕/ 综合分数 / 向量分数 / 关键词分数，
 * 后面才补章节、页码这些只在有值时出现的出处信息。
 *
 * 交出去的是「字段 + 值 + 配色」而不是一整串文本：每个字段要用各自颜色的胶囊圈起来，
 * 拼成字符串的话模板里就没法一项一个色。六个字段挤成一行纯文本时，找「向量分数」得从
 * 行首读到行中，各定一色之后色本身就是定位符。
 *
 * cls 得一项一个值：模板里拿它当 v-for 的 key，页码和工作表共用一个 cls 的话，同一行同
 * 时出现两项就会撞 key。
 *
 * 章节只在文本命中里给：图片、音视频这两页展示的是媒体本身，挂个 titlePath 只会让人
 * 以为一张图也分章节。
 */
function hitFields(h: RetrieveHit, isText = false) {
  const list: Array<{ cls: string; text: string }> = [
    { cls: 'kb', text: `知识库：${h.kbName || `库 ${h.kbId}`}` },
    { cls: 'doc', text: `文件名：${h.docName || `文档 ${h.docId}`}` },
  ];
  if (isText) list.push({ cls: 'chunk', text: `分块号：${h.chunkIndex}` });
  list.push(
    { cls: 'score', text: `综合分数：${scoreText(h.score)}` },
    { cls: 'vector', text: `向量分数：${scoreText(h.vectorScore)}` },
    { cls: 'keyword', text: `关键词分数：${scoreText(h.keywordScore)}` },
  );
  if (isText && h.titlePath) {
    list.push({ cls: 'path', text: `章节：${h.titlePath}` });
  }
  if (h.graphEntity) {
    list.push({ cls: 'entity', text: `命中实体：${h.graphEntity}` });
  }
  if (h.pageNum) list.push({ cls: 'page', text: `页码：${h.pageNum}` });
  if (h.sheetName) list.push({ cls: 'sheet', text: `工作表：${h.sheetName}` });
  if (h.mediaDuration) {
    list.push({
      cls: 'duration',
      text: `时长：${formatDuration(h.mediaDuration)}`,
    });
  }
  return list;
}

/**
 * 「分块内容：」与正文拼成同一份 HTML 再交给 v-html。
 *
 * .hit-body 是 pre-wrap，模板里并排放两个 span 的话，它们之间的换行会被当成
 * 正文里的空行；拼进一个字符串里就只占正文开头的几个字。
 */
function contentHtml(h: RetrieveHit) {
  return `<span class="content-label">分块内容：</span>${highlight(h.content)}`;
}

/**
 * 这一条命中的全部媒体地址。
 *
 * 一条切片可以覆盖多个媒体块（按页分块的一页三张图、标题分块的一节里插了几张），
 * 后端因此给的是列表；老数据与单媒体卡片只给 mediaUrl，这里回落成一条。
 */
function mediaList(h: RetrieveHit) {
  const list = (h.mediaUrls ?? []).filter(Boolean);
  if (list.length > 0) return list;
  return h.mediaUrl ? [h.mediaUrl] : [];
}

/**
 * 某一条媒体地址是音频还是视频（据签名地址的后缀判）。
 *
 * 模态位只有 audio_video 一颗，文档内嵌音频与独立视频文件同一个桶：全用
 * <video> 渲染会把音频显示成一块没有画面的黑屏，播放器都找不着。
 * 同一条切片里既嵌音频又嵌视频时也得各按各的后缀渲染，不能拿第一条定全部。
 */
function isAudioUrl(url: string) {
  const ref = String(url || '').split('?')[0] ?? '';
  const ext = ref.includes('.')
    ? ref.slice(ref.lastIndexOf('.') + 1).toLowerCase()
    : '';
  return RAG_AUDIO_EXTS.includes(ext);
}

async function loadOptions() {
  try {
    options.value = await KbOptions();
    // 从知识维护页带 kbId 跳过来时预选中那个库（列表侧栏已不再放检索按钮，靠路由传）
    const preset = Number(route.query.kbId || 0);
    if (preset && options.value.some((o) => o.id === preset)) {
      kbIds.value = [preset];
    }
  } catch {
    options.value = [];
  }
}

/** 重排与对话两档模型：拉不回来只是下拉为空，不阻断普通检索 */
async function loadModelOptions() {
  try {
    rerankOptions.value = await fetchRerankModels();
  } catch {
    rerankOptions.value = [];
  }
  try {
    chatOptions.value = await fetchChatModels();
  } catch {
    chatOptions.value = [];
  }
}

async function onUploadImage(file: File) {
  uploading.value = true;
  try {
    imageUrl.value = await uploadQueryImage(file);
    message.success('查询图片已就绪');
  } catch (error: any) {
    message.error(error?.message || '图片上传失败');
  } finally {
    uploading.value = false;
  }
  return false;
}

/** 本次检索的完整参数（普通检索与流式问答共用，避免两处传参传漏一个字段） */
function buildPayload(): RetrieveReq {
  return {
    kbIds: kbIds.value,
    query: query.value.trim() || undefined,
    imageUrl: imageUrl.value || undefined,
    topK: topK.value,
    mode: modeSent.value,
    scoreThreshold: scoreThreshold.value,
    // 两路都勾才谈百分比；后端 vectorWeight 是 0~1 的小数，页面按百分比展示得换算一次
    vectorWeight: bothRecall.value
      ? Number((vectorPct.value / 100).toFixed(2))
      : undefined,
    // 需求 8：没选重排模型就显式关掉，免得库上的存量 rerank 配置悄悄生效
    rerank: !!(rerankModelId.value ?? 0),
    rerankModelId: rerankModelId.value,
    withGraph: graphAvailable.value ? withGraph.value : false,
    chatModelId: chatModelId.value,
    graphDepth: withGraph.value ? graphDepth.value : undefined,
    graphScale: withGraph.value ? graphScale.value : undefined,
    graphSourceChunks: withGraph.value ? graphSourceChunks.value : false,
  };
}

function applyResult(res: RetrieveResp) {
  result.value = res;
  searched.value = true;
  // 哪个模态有结果就自动切过去，省得用户对着空白的「文本」页以为是检索坏了
  const order: Array<[string, number]> = [
    ['text', textHits.value.length],
    ['image', imageHits.value.length],
    ['av', avHits.value.length],
    ['graph', graphEntities.value.length],
  ];
  activeTab.value = order.find(([, n]) => n > 0)?.[0] ?? 'text';
}

function modeText(label: string) {
  // 多库参数不一致时后端统一给 MIXED：直译一个英文枚举给用户在页面上猜没意义
  return label === 'MIXED'
    ? '各库参数不一'
    : RETRIEVE_MODE_LABELS[label] || label;
}

function stopAsk() {
  abortCtl?.abort();
  abortCtl = null;
  answering.value = false;
}

function clearAnswer() {
  answer.value = '';
  answerError.value = '';
  answerInfo.value = null;
}

async function doSearch() {
  if (!allowed.value) {
    return message.warning('没有「知识检索」的菜单权限（ai:kb:search）');
  }
  if (!query.value.trim() && !imageUrl.value) {
    return message.warning('请输入查询内容，或上传一张查询图片');
  }
  if (chatModelId.value && !query.value.trim()) {
    return message.warning(
      '知识库问答需要输入问题文本：只上传图片没法让模型作答',
    );
  }
  if (graphWithoutChat.value) {
    return message.warning(
      '开启知识图谱增强必须选择对话模型：图谱那一路要先让大模型从问题里抽出实体和关系',
    );
  }
  if (withGraph.value && !graphAvailable.value) {
    // 图谱只服务 doc 型（非文档库的支持列恒为 0）：勾了也是白勾，直接不传并说明一句
    message.warning(
      '所选知识库里没有文档型库，图谱增强只对文档知识库生效，本次已忽略',
    );
  }
  stopAsk();
  result.value = null;
  expanded.value = new Set();
  clearAnswer();
  const payload = buildPayload();

  if (!chatModelId.value) {
    loading.value = true;
    try {
      applyResult(await Retrieve(payload));
    } catch (error: any) {
      message.error(error?.message || '检索失败');
    } finally {
      loading.value = false;
    }
    return;
  }

  // 需求 9：一条连接先回检索结果（meta 帧）再逐字回回答，左右两栏拿的是同一份召回集；
  // 分两次请求会出现两套召回集，两栏对不上时谁也说不清哪边错
  const ctl = new AbortController();
  abortCtl = ctl;
  answering.value = true;
  loading.value = true;
  try {
    await RetrieveAsk(payload, {
      signal: ctl.signal,
      onMeta: (resp) => {
        applyResult(resp);
        loading.value = false;
      },
      onDelta: (piece) => {
        answer.value += piece;
      },
      onDone: (info) => {
        answerInfo.value = info;
      },
      onError: (msg) => {
        answerError.value = msg;
      },
    });
  } catch (error: any) {
    if (!ctl.signal.aborted)
      answerError.value = error?.message || '问答连接中断';
  } finally {
    answering.value = false;
    loading.value = false;
    abortCtl = null;
    // 首帧之前就连不上（网关 5xx）也得把页面从「输入查询开始检索」里拉回来，
    // 否则用户只看到一个空页面，读不到上面那条错误文案是不是本次检索的结果
    if (!result.value) searched.value = true;
  }
}

function reset() {
  stopAsk();
  query.value = '';
  imageUrl.value = '';
  withGraph.value = false;
  graphSourceChunks.value = false;
  result.value = null;
  searched.value = false;
  expanded.value = new Set();
  clearAnswer();
}

// 逐字输出时跟着流滚到底：不自动的话回答一长就得手动拖滚动条看后半句
watch(answer, async () => {
  await nextTick();
  const el = answerBox.value;
  if (el) el.scrollTop = el.scrollHeight;
});

// ===== 图谱命中的关系小图与 hover 明细 =====
/** 小图高度写死：在 TabPane 里靠 flex 去量只会量到 0，G6 就得画在一块 0×0 的画布上 */
const KG_MINI_HEIGHT = 280;
/** 明细浮卡按这个宽度夹在容器里，别让它在右边探出画布 */
const KG_TIP_WIDTH = 230;
const graphBox = ref<HTMLDivElement | null>(null);
/** 当前 hover 到的实体（列表行自己走 Tooltip，这里只服务图上的节点） */
const tipEntity = ref<null | Record<string, any>>(null);
const tipPos = ref({ left: 0, top: 0 });
let kgGraph: any = null;
/** 小图那个容器的 DOM 监听拆口句柄：切页不拆，下次建图就叠上第二套按下/松手 */
let detachKgCanvas: (() => void) | null = null;
/** 实体名 → 接口回的那份明细，hover 回调在 DOM 监听里，拿不到 renderKg 的局部变量 */
let kgDetails = new Map<string, Record<string, any>>();

const kgTipStyle = computed(() => ({
  left: `${tipPos.value.left}px`,
  top: `${tipPos.value.top}px`,
}));

/**
 * 实体明细：接口回什么就摊开什么（名称/类型/知识库/分数/别名/摘要）。
 *
 * 给逐行数组而不是一段 JSON 文本：字段名翻成中文，用户不用一边看一边猜 score 是谁的分。
 */
function entityDetail(en?: null | Record<string, any>) {
  if (!en) return [];
  const aliases = Array.isArray(en.aliases) ? en.aliases.filter(Boolean) : [];
  return [
    `名称：${en.name || ''}`,
    `类型：${en.type || '其他'}`,
    `知识库：${en.kbId ?? en.kb_id ?? '-'}`,
    `分数：${Number(en.score || 0).toFixed(6)}`,
    `别名：${aliases.join('、') || '无'}`,
    `摘要：${en.summary || '无'}`,
  ];
}

/** 把明细浮卡钉到鼠标旁边（坐标换成容器内的，再夹进画布范围） */
function showTipAt(en: Record<string, any>, clientX: number, clientY: number) {
  const el = graphBox.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  tipPos.value = {
    left: Math.max(
      4,
      Math.min(clientX - rect.left + 12, rect.width - KG_TIP_WIDTH - 4),
    ),
    top: Math.max(4, Math.min(clientY - rect.top + 12, rect.height - 40)),
  };
  tipEntity.value = en;
}

/**
 * 停在实体上就把明细浮卡摊到鼠标旁，从命中带里出去就收掉。
 *
 * 不听 G6 的 node:mouseenter：它只认形状自己的包围盒，对视后那盒子在屏幕上就是几个像素，
 * 反馈便是「要挪得很准才出明细」；改走 shared/graph-canvas 的 hover 回调后，命中范围与
 * 平移、与图谱检索页都是同一份判定。
 */
function showTipOnHit(hit: GraphHit | null, event: MouseEvent | null) {
  const detail = hit && event ? kgDetails.get(hit.id) : undefined;
  if (!detail || !event) {
    tipEntity.value = null;
    return;
  }
  showTipAt(detail, event.clientX, event.clientY);
}

function destroyKg() {
  // 监听先拆：容器 DOM 是同一个，不拆就会叠上第二套按下/松手（拆口顺手把光标类名也清掉）
  detachKgCanvas?.();
  detachKgCanvas = null;
  if (kgGraph && !kgGraph.destroyed) kgGraph.destroy();
  kgGraph = null;
  kgDetails = new Map();
  tipEntity.value = null;
}

/**
 * 把整张小图缩放进容器。
 *
 * 用节点模型坐标算包围盒，不用 G6 的 getCanvasBBox：力导的形状位置比模型坐标晚落地，
 * 拿那个滞后的盒对视会算出放大好几倍（图谱检索页实测过同样的坑）。
 */
function fitKg(width: number) {
  const g = kgGraph;
  if (!g || g.destroyed) return;
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  g.getNodes().forEach((item: any) => {
    const model = item.getModel();
    if (!Number.isFinite(model?.x) || !Number.isFinite(model?.y)) return;
    const size = Array.isArray(model.size)
      ? model.size
      : [model.size, model.size];
    // 标签画在节点下方，各方向留 26px 余量
    const half = (Number(size[0]) || 16) / 2 + 26;
    minX = Math.min(minX, model.x - half);
    maxX = Math.max(maxX, model.x + half);
    minY = Math.min(minY, model.y - half);
    maxY = Math.max(maxY, model.y + half);
  });
  const boxW = maxX - minX;
  const boxH = maxY - minY;
  if (!(boxW > 0) || !(boxH > 0) || !(width > 0)) return;
  const ratio = Math.min(
    1.4,
    Math.max(0.2, Math.min((width - 24) / boxW, (KG_MINI_HEIGHT - 24) / boxH)),
  );
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  g.get('group').setMatrix([
    ratio,
    0,
    0,
    0,
    ratio,
    0,
    width / 2 - ratio * cx,
    KG_MINI_HEIGHT / 2 - ratio * cy,
    0,
    0,
    1,
  ]);
  g.autoPaint();
}

/**
 * 命中实体 + 结构化关系 → 一张「实体—关系—实体」小图。
 *
 * 只在「图谱命中」这页可见时建：TabPane 藏起来时容器宽度量到 0，建出来的图就画在
 * 0×0 的画布上（与图谱检索页同一个坑），而这里没有 ResizeObserver 兜着。
 * 关系里的邻居也一并上画布：它们不在命中实体里，只能给名字与所属库，明细照常摊开。
 */
async function renderKg() {
  destroyKg();
  const el = graphBox.value;
  if (!el || kgEdges.value.length === 0) return;
  // 容器里只要还留着 canvas 就一律摘掉：叠了两层时，上面那张不再更新的旧图会把新图整个
  // 盖住，表现正是「拖着不动、明细还钉在老位置上」
  el.querySelectorAll('canvas').forEach((c) => c.remove());
  const G6 = await import('@antv/g6');
  // 同名实体可能来自两个库：按名合并留分最高的那个（graphHitsSorted 已按分降序）
  const hits = new Map<string, Record<string, any>>();
  graphHitsSorted.value.forEach((e) => {
    const name = String(e?.name || '');
    if (name && !hits.has(name)) hits.set(name, { ...e, hit: true });
  });
  const idSet = new Set<string>(hits.keys());
  const kbByName = new Map<string, number>();
  kgEdges.value.forEach((e) => {
    [String(e.head), String(e.tail)].forEach((name) => {
      idSet.add(name);
      if (!kbByName.has(name)) kbByName.set(name, Number(e.kb_id || 0));
    });
  });
  // 明细表提到模块级：hover 由 DOM 监听驱动（不在本次闭包里），只认这份 name → 明细
  kgDetails = new Map<string, Record<string, any>>(
    [...idSet].map((name) => [
      name,
      hits.get(name) || {
        name,
        hit: false,
        kb_id: kbByName.get(name) || 0,
      },
    ]),
  );
  const nodes = [...idSet].map((name) => {
    const hit = hits.get(name);
    const color = hit ? kgEntityColor(hit.type) : '#D9D9D9';
    return {
      id: name,
      label: name,
      // 命中实体按分数放大、邻居一律小一圈：一眼分得出哪几个是本次真命中的
      size: hit ? 20 + Math.min(22, Number(hit.score || 0) * 24) : 16,
      style: { fill: color, stroke: color, lineWidth: hit ? 2 : 1 },
    };
  });
  const edges = kgEdges.value.map((e, i) => ({
    id: `k${i}`,
    source: String(e.head),
    target: String(e.tail),
    label: String(e.relation || ''),
  }));
  const width = Math.max(240, Math.round(el.getBoundingClientRect().width));
  // 交互监听先挂上再建图（理由见 attachCanvasInteractions 的注释尾段）
  detachKgCanvas = attachCanvasInteractions(el, {
    getGraph: () => kgGraph,
    onHover: showTipOnHit,
    // 这张小图没有「点实体出详情」，也没有 Shift 摆单个节点，所以 Shift 也照样用来平移
    reserveShiftForNodeDrag: false,
  });
  kgGraph = new G6.Graph({
    container: el,
    width,
    height: KG_MINI_HEIGHT,
    fitView: false,
    // 布局与对视都同步做完：animate 开着的话 render 那一帧的位置只是起点
    animate: false,
    minZoom: 0.2,
    maxZoom: 3,
    // 只把滚轮缩放留给 G6。平移原先是 drag-canvas，而它的 allowDragOnItem 默认 false
    // （g6-pc/lib/behavior/drag-canvas.js:25/291）：按在节点、边或标签上想推图一律被
    // return false。这张小图本来就被节点铺满，能起手的地方只剩几点空白 = 「拖着不动」；
    // 平移与 hover 现在都走 shared/graph-canvas 听 DOM，与图谱检索页共用一份命中判定。
    modes: { default: ['zoom-canvas'] } as any,
    layout: {
      type: 'force',
      animate: false,
      preventOverlap: true,
      nodeSize: 56,
      linkDistance: 110,
      nodeStrength: -200,
      edgeStrength: 0.3,
    },
    defaultNode: {
      type: 'circle',
      labelCfg: {
        position: 'bottom',
        offset: 4,
        style: { fontSize: 11, fill: '#333' },
      },
      style: { cursor: 'pointer' },
    },
    defaultEdge: {
      type: 'quadratic',
      style: {
        stroke: '#d5d5d5',
        lineWidth: 1,
        endArrow: { path: G6.Arrow.triangle(5, 7, 0), fill: '#d5d5d5' },
      },
      labelCfg: {
        autoRotate: true,
        style: {
          fontSize: 10,
          fill: '#999',
          background: { fill: '#fff', padding: [2, 4, 2, 4], radius: 2 },
        },
      },
    },
  });
  kgGraph.data({ nodes, edges });
  kgGraph.render();
  fitKg(width);
}

// 换结果或切回这一页都重建小图；离开这一页就拆掉（容器一隐藏宽度就归 0）
watch([activeTab, graphEntities], async () => {
  if (activeTab.value !== 'graph') {
    destroyKg();
    return;
  }
  await nextTick();
  await renderKg();
});

onMounted(() => {
  loadOptions();
  loadModelOptions();
});

// 离开页面时断流并拆图：不然后台还在读一个没人看的回答，连接与 G6 实例都占着
onBeforeUnmount(() => {
  stopAsk();
  destroyKg();
});
</script>

<template>
  <div class="page-container">
    <Alert
      v-if="!allowed"
      type="warning"
      show-icon
      class="mb-2"
      message="没有「知识检索」的菜单权限（ai:kb:search），本页不可用。"
    />

    <Card :bordered="false" class="param-card mb-2">
      <Space direction="vertical" :size="10" style="width: 100%">
        <!-- 参数行 1：库 + 检索方式（需求 7：勾选召回路，两路都勾才按百分比分配） -->
        <Space wrap :size="12">
          <Select
            v-model:value="kbIds"
            mode="multiple"
            :options="kbSelectOptions"
            placeholder="知识库（留空 = 全部可用库）"
            style="min-width: 300px"
            :max-tag-count="2"
            option-filter-prop="label"
            allow-clear
          />
          <Tooltip
            title="只勾一路就只走那一路（那一路 100%）；两路都勾才按下面的百分比融合；都不勾 = 各库沿用自己的检索配置"
          >
            <span class="param-label">检索方式</span>
          </Tooltip>
          <CheckboxGroup
            v-model:value="recallModes"
            :options="recallModeOptions"
          />
          <!-- 权重条：两段加起来永远是 100%，滑块只在两路都勾时可拖 -->
          <div v-if="weights" class="weight-box">
            <div class="weight-track">
              <div
                class="seg seg-kw"
                :style="{ width: `${weights.keyword}%` }"
              ></div>
              <div
                class="seg seg-vec"
                :style="{ width: `${weights.vector}%` }"
              ></div>
            </div>
            <Space :size="8" align="center">
              <span class="weight-num">关键词 {{ weights.keyword }}%</span>
              <Slider
                v-if="bothRecall"
                v-model:value="vectorPct"
                :max="100"
                :min="0"
                :step="5"
                style="width: 140px"
                tooltip-placement="top"
              />
              <span v-else class="weight-lock">单选一路，不可分配</span>
              <span class="weight-num">向量 {{ weights.vector }}%</span>
            </Space>
          </div>
          <span v-else class="param-label">
            未勾检索方式：各库沿用自己的配置
          </span>
        </Space>

        <!-- 参数行 2：条数与阈值、（需求 8）重排模型、（需求 9）对话模型与图谱增强 -->
        <Space wrap :size="12">
          <span class="param-label">TopK</span>
          <InputNumber
            v-model:value="topK"
            :min="1"
            :max="100"
            style="width: 78px"
          />
          <Tooltip
            title="低于该相似度的命中直接丢掉，留空 = 用知识库自己的配置"
          >
            <span class="param-label">相似度阈值</span>
          </Tooltip>
          <InputNumber
            v-model:value="scoreThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            placeholder="默认"
            style="width: 92px"
          />
          <Tooltip
            title="选了才重排，不选就不重排：这一项只对本次请求生效，不沿用库上的重排配置"
          >
            <span class="param-label">重排模型</span>
          </Tooltip>
          <Select
            v-model:value="rerankModelId"
            :options="rerankOptions"
            placeholder="不重排"
            allow-clear
            show-search
            :filter-option="(i: string, o: any) => o.label.includes(i)"
            style="min-width: 200px"
          />
          <Tooltip
            title="选了才有右栏「模型回答」（流式、不做记忆）；不选就只列检索结果。开启知识图谱增强时必填"
          >
            <span class="param-label">对话模型</span>
          </Tooltip>
          <Select
            v-model:value="chatModelId"
            :options="chatOptions"
            placeholder="只列检索结果，不问答"
            allow-clear
            show-search
            :filter-option="(i: string, o: any) => o.label.includes(i)"
            style="min-width: 200px"
          />
          <template v-if="graphAvailable">
            <Tooltip
              title="叠加图谱检索那一路：先让大模型从问题里抽出实体和关系，再按实体、关系各查一路向量，按深度扩邻域、按规模取数，带出的原文叠加到上面的召回结果里（LightRAG 式）。开启必须选对话模型"
            >
              <span class="param-label">知识图谱增强</span>
            </Tooltip>
            <Switch v-model:checked="withGraph" />
            <template v-if="withGraph">
              <span class="param-label">深度</span>
              <InputNumber
                v-model:value="graphDepth"
                :min="KG_DEPTH_MIN"
                :max="KG_DEPTH_MAX"
                style="width: 66px"
              />
              <span class="param-label">规模</span>
              <InputNumber
                v-model:value="graphScale"
                :min="KG_SCALE_MIN"
                :max="KG_SCALE_MAX"
                style="width: 78px"
              />
              <Tooltip
                title="默认关：图谱那一路只贡献精炼的实体+关系，原文交给关键词+向量那一路——反查 chunk 会把上下文撑大、容易让模型失焦。打开才顺命中实体把原文切片叠进结果（recall=图谱）"
              >
                <span class="param-label">反查原文</span>
              </Tooltip>
              <Switch v-model:checked="graphSourceChunks" size="small" />
            </template>
          </template>
        </Space>
        <div v-if="graphWithoutChat" class="warn-line">
          开启知识图谱增强必须选择对话模型：图谱那一路要先让大模型从问题里抽出实体和关系
        </div>

        <Space.Compact style="width: 100%">
          <Input
            v-model:value="query"
            size="large"
            placeholder="输入查询内容，回车即检索…"
            @press-enter="doSearch"
          >
            <template #prefix><SearchOutlined /></template>
          </Input>
          <Button
            size="large"
            :loading="loading || answering"
            type="primary"
            @click="doSearch"
          >
            {{ chatModelId ? '检索并问答' : '检索' }}
          </Button>
          <Button size="large" @click="reset">清空</Button>
        </Space.Compact>

        <!-- 图搜图：选了图片库才出现，上传后回显缩略图，点缩略图旁「移除」可撤掉 -->
        <Space v-if="hasImageKb" align="center">
          <Upload
            :before-upload="onUploadImage"
            :show-upload-list="false"
            accept="image/*"
          >
            <Button :loading="uploading">
              <template #icon><PictureOutlined /></template>
              上传查询图片（图搜图）
            </Button>
          </Upload>
          <template v-if="imageUrl">
            <img :src="imageUrl" alt="query" class="query-thumb" />
            <Button size="small" type="link" @click="imageUrl = ''">
              移除
            </Button>
          </template>
        </Space>
      </Space>
    </Card>

    <div class="result-wrap">
      <Card :bordered="false" class="result-card">
        <Spin :spinning="loading">
          <Alert
            v-if="result"
            type="info"
            class="mb-2"
            :message="`模式 ${modeText(result.mode)} · 命中 ${result.total} 条 · 耗时 ${result.tookMs}ms`"
          />
          <Alert
            v-for="(w, i) in result?.warnings || []"
            :key="i"
            type="warning"
            :message="w"
            class="mb-2"
            show-icon
          />

          <Tabs v-if="result" v-model:active-key="activeTab">
            <TabPane key="text" :tab="`文本命中 (${textHits.length})`">
              <div v-if="textHits.length > 0" class="hit-group">
                <div v-for="(h, i) in textHits" :key="`t${i}`" class="text-hit">
                  <div class="hit-fields">
                    <span
                      v-for="f in hitFields(h, true)"
                      :key="f.cls"
                      :class="`hf hf-${f.cls}`"
                    >
                      {{ f.text }}
                    </span>
                    <!-- 需求 9：两路叠加后的结果必须把路标出来，否则用户看不出这条为什么在这里 -->
                    <Tag
                      v-if="h.recall && h.recall !== RECALL_CHUNK"
                      color="purple"
                    >
                      {{ RECALL_LABELS[h.recall] || h.recall }}
                    </Tag>
                    <Button size="small" type="link" @click="toggleDetail(h)">
                      {{ expanded.has(hitKey(h)) ? '收起' : '展开全文' }}
                    </Button>
                  </div>
                  <div
                    class="hit-body"
                    :class="{ clipped: !expanded.has(hitKey(h)) }"
                    v-html="contentHtml(h)"
                  ></div>
                </div>
              </div>
              <Empty v-else description="没有文本命中" />
            </TabPane>

            <TabPane key="image" :tab="`图片命中 (${imageHits.length})`">
              <div v-if="imageHits.length > 0" class="image-wall">
                <!-- 一条切片可以覆盖多张图：全部放出来，只画第一张等于其余的图在页面上不存在 -->
                <div
                  v-for="(h, i) in imageHits"
                  :key="`i${i}`"
                  class="image-hit"
                >
                  <div class="media-strip">
                    <a
                      v-for="(m, mi) in mediaList(h)"
                      :key="mi"
                      :href="m"
                      target="_blank"
                      rel="noopener"
                    >
                      <img :src="m" :alt="h.docName" />
                    </a>
                  </div>
                  <div class="hit-fields">
                    <span
                      v-for="f in hitFields(h)"
                      :key="f.cls"
                      :class="`hf hf-${f.cls}`"
                    >
                      {{ f.text }}
                    </span>
                    <Tag v-if="h.recall === RECALL_GRAPH" color="purple">
                      {{ RECALL_LABELS[h.recall] || '图谱关联' }}
                    </Tag>
                  </div>
                </div>
              </div>
              <Empty v-else description="没有图片命中" />
            </TabPane>

            <TabPane key="av" :tab="`音视频命中 (${avHits.length})`">
              <div v-if="avHits.length > 0" class="hit-group">
                <div v-for="(h, i) in avHits" :key="`a${i}`" class="av-hit">
                  <div class="hit-fields">
                    <VideoCameraOutlined />
                    <span
                      v-for="f in hitFields(h)"
                      :key="f.cls"
                      :class="`hf hf-${f.cls}`"
                    >
                      {{ f.text }}
                    </span>
                    <Tag
                      v-if="h.recall && h.recall !== RECALL_CHUNK"
                      color="purple"
                    >
                      {{ RECALL_LABELS[h.recall] || h.recall }}
                    </Tag>
                  </div>
                  <div
                    v-for="(m, mi) in mediaList(h)"
                    :key="`m${mi}`"
                    class="media-line"
                  >
                    <audio v-if="isAudioUrl(m)" :src="m" controls></audio>
                    <video v-else :src="m" controls></video>
                  </div>
                  <div class="hit-body">{{ h.content }}</div>
                </div>
              </div>
              <Empty v-else description="没有音视频命中" />
            </TabPane>

            <TabPane
              v-if="result?.graph?.applied || (withGraph && graphAvailable)"
              key="graph"
              :tab="`图谱命中 (${graphEntities.length})`"
            >
              <div v-if="graphEntities.length > 0" class="hit-group">
                <!-- 先把命中的实体按分数高低说清楚，再画关系 -->
                <div class="block-title">命中实体（按分数从高到低）</div>
                <div class="entity-list">
                  <Tooltip v-for="(en, i) in graphHitsSorted" :key="`g${i}`">
                    <template #title>
                      <div
                        v-for="(line, li) in entityDetail(en)"
                        :key="li"
                        class="tip-line"
                      >
                        {{ line }}
                      </div>
                    </template>
                    <div class="entity-row">
                      <span
                        class="dot"
                        :style="{ background: kgEntityColor(en.type) }"
                      ></span>
                      <span class="entity-name ellipsis">{{ en.name }}</span>
                      <Tag v-if="en.type">{{ en.type }}</Tag>
                      <span v-if="en.aliases?.length" class="hit-path">
                        别名 {{ en.aliases.length }}
                      </span>
                      <span class="entity-score">
                        {{ Number(en.score || 0).toFixed(4) }}
                      </span>
                    </div>
                  </Tooltip>
                </div>

                <div class="block-title mt-2">
                  实体关系图谱 · 按住任意处平移 · 滚轮缩放 · 停在实体上出明细
                </div>
                <div v-if="kgEdges.length > 0" class="kg-wrap">
                  <div ref="graphBox" class="kg-canvas"></div>
                  <div v-if="tipEntity" class="kg-tip" :style="kgTipStyle">
                    <div
                      v-for="(line, li) in entityDetail(tipEntity)"
                      :key="li"
                      class="tip-line"
                    >
                      {{ line }}
                    </div>
                    <div v-if="tipEntity.hit === false" class="tip-note">
                      只是关系里带出的邻居，不在本次命中的实体里
                    </div>
                  </div>
                </div>
                <Empty
                  v-else
                  :image="Empty.PRESENTED_IMAGE_SIMPLE"
                  description="本次没有可画的关系：图谱那一路只命中了实体，没匹配到关系也没扩出邻域边"
                />
                <Alert
                  class="mt-2"
                  type="info"
                  show-icon
                  message="要看完整关系网与原文溯源，去左侧菜单「图谱检索」——那页才能按深度扩邻域。"
                />
              </div>
              <Empty v-else description="本次没有召回图谱实体" />
            </TabPane>
          </Tabs>

          <Empty
            v-else
            :description="searched ? '没有命中' : '输入查询开始检索'"
          >
            <template #image>
              <FileSearchOutlined style="font-size: 48px; color: #bbb" />
            </template>
          </Empty>
        </Spin>
      </Card>

      <!-- 需求 9：选了对话模型才出现右栏，与左栏同一次召回集；不做记忆 -->
      <Card v-if="hasAnswerPane" :bordered="false" class="answer-card">
        <template #title>
          模型回答
          <Tag v-if="answering" color="processing">生成中</Tag>
          <Tag v-if="answerInfo">
            引用 {{ answerInfo.citations ?? 0 }} 条 ·
            {{ answerInfo.tookMs ?? 0 }}ms
          </Tag>
        </template>
        <template #extra>
          <Button v-if="answering" size="small" @click="stopAsk">停止</Button>
        </template>
        <Alert
          v-if="answerError"
          type="error"
          show-icon
          class="mb-2"
          :message="answerError"
        />
        <!-- 模型输出一律走纯文本插值：回答里会原样引用用户文档的内容，上 v-html
             等于把文档里的脚本一路到页面执行（左栏那几处已经先转义了） -->
        <div ref="answerBox" class="answer-text">
          <span v-if="answer">{{ answer }}</span>
          <span v-else-if="!answerError" class="answer-empty">
            {{
              answering
                ? '正在依据检索到的资料作答…'
                : '还没有回答（每次提问独立，不记得上一句）'
            }}
          </span>
        </div>
      </Card>
    </div>
  </div>
</template>

<style scoped>
/* 定高不能用 height:100%：布局链的根是 min-h-full（不是 h-full），<main> 的高度由内容
   决定，命中一多整条百分比高度链就塌陷、改由整页滚动 —— 搜索栏跟着被滚出屏幕，
   内部滚动条也永远不出现。这里用视口高减去头部容器与页脚（框架已以 CSS 变量写在
   :root 上），拿到不依赖自测量的确定高度。 */
.page-container {
  display: flex;
  flex-direction: column;
  height: calc(
    100vh - var(--vben-header-height, 90px) - var(--vben-footer-height, 32px)
  );
  padding: 8px;

  /* 整页不再上下滚（原来是 auto，一滚就把搜索栏一并滚出屏幕）：
     页高钉死，剩下的空间全给结果区，滚动条就在那一块里 */
  overflow: hidden;
}

/* 搜索那一块钉在顶上：flex: none 免得被结果区挤矮 */
.param-card {
  flex: none;
}

.result-card {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 卡体才是真正的滚动容器（min-height: 0 同左：flex 项默认不收缩） */
:deep(.result-card .ant-card-body) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

/* 命中多了先滚过顶部那两条提示：标签页（文本/图片/音视频/图谱命中）钉在
   滚动区顶上，不然切个模态还得先滚回去 */
:deep(.result-card .ant-tabs-nav) {
  position: sticky;
  top: 0;
  z-index: 3;
  background: var(--component-background, #fff);
}

.param-label {
  font-size: 12px;
  color: var(--text-color-secondary, #888);
}

/* 需求 9：选了对话模型就左右分栏。两栏各自滚（卡体是自己的滚动容器，同左栏原来的规则），
   不然一边长就把另一边顶高，整块又得回到整页滚动 */
.result-wrap {
  display: flex;
  flex: 1;
  gap: 8px;
  min-height: 0;
}

.result-wrap .result-card {
  flex: 1.15;
}

.answer-card {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

:deep(.answer-card .ant-card-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.answer-text {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.answer-empty {
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

/* 需求 7：关键词/向量的占比条 —— 比一个光泳滑块更能说明「总和 100%」 */
.weight-box {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 260px;
}

.weight-track {
  display: flex;
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--border-color, #eee);
}

.weight-track .seg {
  height: 100%;
  transition: width 0.2s;
}

.seg-kw {
  background: #91caff;
}

.seg-vec {
  background: #b7eb8f;
}

.weight-num {
  font-size: 12px;
  color: var(--text-color-secondary, #666);
  white-space: nowrap;
}

.weight-lock {
  font-size: 12px;
  color: var(--text-color-secondary, #aaa);
}

.warn-line {
  font-size: 12px;
  color: #d46b08;
}

.query-thumb {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border: 1px solid #eee;
  border-radius: 4px;
}

.hit-group {
  margin-bottom: 12px;
}

.text-hit {
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--component-background-light, #fafafa);
  border: 1px solid var(--border-color, #eee);
  border-radius: 6px;
}

/* 命中行的每个字段一枚彩色胶囊：知识库/文件名/分块号/三个分数得一眼扫完，全挤成一行
   纯文本时找「向量分数」要从行首读到行中，各定一色之后色本身就是定位符。
   立体感靠「上浅下深的渐变 + 同色描边 + 一层薄投影」，不做实底深字：整片命中行都变成
   色块会把正文压下去。 */
.hit-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  color: #555;
}

.hf {
  max-width: 100%;
  padding: 1px 9px;
  line-height: 18px;
  overflow-wrap: anywhere;
  border: 1px solid transparent;
  border-radius: 999px;
  box-shadow:
    0 1px 2px rgb(15 23 42 / 12%),
    inset 0 1px 0 rgb(255 255 255 / 60%);
}

.hf-kb {
  color: #0958d9;
  background: linear-gradient(180deg, #fff, #e6f4ff);
  border-color: #91caff;
}

.hf-doc {
  color: #08979c;
  background: linear-gradient(180deg, #fff, #e6fffb);
  border-color: #87e8de;
}

.hf-chunk {
  color: #722ed1;
  background: linear-gradient(180deg, #fff, #f9f0ff);
  border-color: #d3adf7;
}

.hf-score {
  color: #cf1322;
  background: linear-gradient(180deg, #fff, #fff1f0);
  border-color: #ffa39e;
}

.hf-vector {
  color: #2f54eb;
  background: linear-gradient(180deg, #fff, #f0f5ff);
  border-color: #adc6ff;
}

.hf-keyword {
  color: #c41d7f;
  background: linear-gradient(180deg, #fff, #fff0f6);
  border-color: #ffadd2;
}

.hf-path {
  color: #389e0d;
  background: linear-gradient(180deg, #fff, #f6ffed);
  border-color: #b7eb8f;
}

.hf-entity {
  color: #d46b08;
  background: linear-gradient(180deg, #fff, #fff7e6);
  border-color: #ffd591;
}

/* 页码/工作表/时长这几项只是补充出处，不该跟六个主字段抢色 */
.hf-page,
.hf-sheet,
.hf-duration {
  color: #595959;
  background: linear-gradient(180deg, #fff, #f0f0f0);
  border-color: #d9d9d9;
}

.hit-path {
  font-size: 12px;
  color: #999;
}

.hit-body {
  font-size: 13px;
  line-height: 1.7;
  color: #444;
  word-break: break-all;
  white-space: pre-wrap;
}

/* 默认只露三行，「展开全文」再放开：一条切片可能几千字，不截断会把整页顶长 */
.hit-body.clipped {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.hit-body :deep(mark) {
  padding: 0 2px;
  color: #d4380d;
  background: #fff2e8;
  border-radius: 2px;
}

/* 正文开头的「分块内容：」只做引导，不该比正文抢眼 */
.hit-body :deep(.content-label) {
  color: var(--text-color-secondary, #999);
}

.image-wall {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}

.image-hit img {
  width: 100%;
  height: 130px;
  object-fit: cover;
  border-radius: 6px;
}

/* 一条切片的几张图平铺在同一张卡里：单张时 flex-grow 把它撑满卡宽，与改前的整格一致 */
.media-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.media-strip > a {
  flex: 1 1 calc(50% - 4px);
  min-width: 0;
}

/* 同一条切片里的多条音视频各占一行：并排挤在一起时第二个播放器都按不到 */
.media-line {
  margin-bottom: 6px;
}

/* 图片墙一格只占 160px：字段行靠自然换行堆叠，不留行高余量会把卡片撑得比图还高 */
.image-hit .hit-fields {
  margin-top: 4px;
}

.av-hit {
  margin-bottom: 12px;
}

.av-hit video {
  width: 360px;
  max-height: 220px;
  background: #000;
  border-radius: 6px;
}

/* ===== 图谱命中 ===== */
.block-title {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

/* 实体列表钉住最大高度：命中实体最多 50 个，不锁住会把关系图顶到屏幕外 */
.entity-list {
  max-height: 240px;
  overflow-y: auto;
}

.entity-row {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 3px 6px;
  border-radius: 4px;
}

.entity-row:hover {
  background: hsl(var(--primary) / 10%);
}

.dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.entity-name {
  flex: 1;
  min-width: 0;
}

.entity-score {
  flex: none;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kg-wrap {
  position: relative;
}

.kg-canvas {
  width: 100%;
  height: 280px;
  cursor: grab;
  background: var(--component-background-light, #fafcff);
  border: 1px solid var(--border-color, #eee);
  border-radius: 6px;
}

/* 光标跟着命中带走（带比节点形状本身宽，与图谱检索页同一套）：g-base hover 到带 cursor
   的形状时会把 cursor 直写到 canvas 元素的行内样式上，盖不过行内只能 !important。 */
.kg-canvas.pointing,
.kg-canvas.pointing :deep(canvas) {
  cursor: pointer !important;
}

/* 拖动过程中给个「正在推图」的手势反馈（排在 pointing 之后，拖的时候它赢） */
.kg-canvas.grabbing,
.kg-canvas.grabbing :deep(canvas) {
  cursor: grabbing !important;
}

/* hover 到实体时的明细浮卡：跟着鼠标钉在画布里，pointer-events:none 免得自己挡住下一个命中 */
.kg-tip {
  position: absolute;
  z-index: 3;
  box-sizing: border-box;
  max-width: 230px;
  padding: 6px 8px;
  font-size: 12px;
  line-height: 1.6;
  pointer-events: none;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 12%);
}

.tip-line {
  word-break: break-all;
}

.tip-note {
  margin-top: 2px;
  color: var(--text-color-secondary, #999);
}
</style>
