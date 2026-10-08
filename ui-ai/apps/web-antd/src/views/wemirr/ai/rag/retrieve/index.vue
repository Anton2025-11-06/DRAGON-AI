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
  RAG_AUDIO_EXTS,
  RECALL_CHUNK,
  RECALL_GRAPH,
  RECALL_LABELS,
  Retrieve,
  RETRIEVE_MODE_LABELS,
  RetrieveAsk,
  uploadQueryImage,
} from '../doc/api';

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
 * 这一条音视频命中是不是音频（据签名地址的后缀判）。
 *
 * 模态位只有 audio_video 一颗，文档内嵌音频与独立视频文件同一个桶：全用
 * <video> 渲染会把音频显示成一块没有画面的黑屏，播放器都找不着。
 */
function isAudioHit(h: RetrieveHit) {
  // split 取下标在 noUncheckedIndexedAccess 下是 string | undefined，补个空串兼平
  const ref = String(h.mediaUrl || '').split('?')[0] ?? '';
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

onMounted(() => {
  loadOptions();
  loadModelOptions();
});

// 离开页面时断流：不然后台还在读一个没人看的回答，连接也一直占着
onBeforeUnmount(stopAsk);
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
                  <div class="hit-head">
                    <Tag color="blue">{{ h.kbName || `库 ${h.kbId}` }}</Tag>
                    <span class="hit-doc">{{ h.docName }}</span>
                    <span v-if="h.titlePath" class="hit-path">{{
                      h.titlePath
                    }}</span>
                    <!-- 需求 9：两路叠加后的结果必须把路标出来，否则用户看不出这条为什么在这里 -->
                    <Tag
                      v-if="h.recall && h.recall !== RECALL_CHUNK"
                      color="purple"
                    >
                      {{ RECALL_LABELS[h.recall] || h.recall }}
                    </Tag>
                    <span v-if="h.graphEntity" class="hit-path">
                      实体：{{ h.graphEntity }}
                    </span>
                    <Tag color="green">score {{ h.score.toFixed(3) }}</Tag>
                    <Tag v-if="h.vectorScore">
                      向量 {{ h.vectorScore.toFixed(3) }}
                    </Tag>
                    <Tag v-if="h.keywordScore">
                      关键词 {{ h.keywordScore.toFixed(3) }}
                    </Tag>
                    <Tag v-if="h.pageNum">第 {{ h.pageNum }} 页</Tag>
                    <Tag v-if="h.sheetName">{{ h.sheetName }}</Tag>
                    <Tag>#{{ h.chunkIndex }}</Tag>
                    <Button size="small" type="link" @click="toggleDetail(h)">
                      {{ expanded.has(hitKey(h)) ? '收起' : '展开全文' }}
                    </Button>
                  </div>
                  <div
                    class="hit-body"
                    :class="{ clipped: !expanded.has(hitKey(h)) }"
                    v-html="highlight(h.content)"
                  ></div>
                </div>
              </div>
              <Empty v-else description="没有文本命中" />
            </TabPane>

            <TabPane key="image" :tab="`图片命中 (${imageHits.length})`">
              <div v-if="imageHits.length > 0" class="image-wall">
                <a
                  v-for="(h, i) in imageHits"
                  :key="`i${i}`"
                  :href="h.mediaUrl"
                  target="_blank"
                  rel="noopener"
                  class="image-hit"
                >
                  <img :src="h.mediaUrl" :alt="h.docName" />
                  <div class="hit-cap">
                    {{ h.kbName }} · {{ h.score.toFixed(3) }}
                    <span v-if="h.recall === RECALL_GRAPH">· 图谱关联</span>
                  </div>
                </a>
              </div>
              <Empty v-else description="没有图片命中" />
            </TabPane>

            <TabPane key="av" :tab="`音视频命中 (${avHits.length})`">
              <div v-if="avHits.length > 0" class="hit-group">
                <div v-for="(h, i) in avHits" :key="`a${i}`" class="av-hit">
                  <div class="hit-head">
                    <VideoCameraOutlined />
                    <span class="hit-doc">{{ h.docName }}</span>
                    <span v-if="h.mediaDuration" class="hit-path">
                      {{ formatDuration(h.mediaDuration) }}
                    </span>
                    <Tag
                      v-if="h.recall && h.recall !== RECALL_CHUNK"
                      color="purple"
                    >
                      {{ RECALL_LABELS[h.recall] || h.recall }}
                    </Tag>
                    <Tag color="green">score {{ h.score.toFixed(3) }}</Tag>
                  </div>
                  <audio
                    v-if="isAudioHit(h)"
                    :src="h.mediaUrl"
                    controls
                  ></audio>
                  <video v-else :src="h.mediaUrl" controls></video>
                  <div class="hit-body">{{ h.content }}</div>
                </div>
              </div>
              <Empty v-else description="没有音视频命中" />
            </TabPane>

            <TabPane
              v-if="result?.graph?.applied || (withGraph && graphAvailable)"
              key="graph"
              :tab="`图谱实体 (${graphEntities.length})`"
            >
              <div v-if="graphEntities.length > 0" class="hit-group">
                <Space wrap>
                  <Tag
                    v-for="(en, i) in graphEntities"
                    :key="`g${i}`"
                    color="purple"
                  >
                    {{ en.name }}（{{ en.entityType || en.type || '其他' }}）
                  </Tag>
                </Space>
                <Alert
                  class="mt-2"
                  type="info"
                  show-icon
                  message="要看完整关系网，去左侧菜单「图谱检索」——那页才有子图可视化与原文溯源。"
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

/* 命中多了先滚过顶部那两条提示：标签页（文本/图片/音视频/图谱实体）钉在
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

.hit-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.hit-doc {
  font-weight: 500;
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

.hit-cap {
  margin-top: 4px;
  font-size: 12px;
  color: #666;
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
</style>
