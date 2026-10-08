<script lang="ts" setup>
import type { KbSaveReq, KbType, RuntimeConfig } from './api';

/**
 * 知识库创建 / 编辑弹窗。
 *
 * 为什么不用 fast-crud 表单：三类知识库的配置面板差异极大（文档型才有解析/分块/图谱，
 * 音视频型把问答模型换成媒体理解模型），条件分支配额远比 fast-crud 的列声明直观。
 * 所有可配项都来自后端 /runtime-config 的归一默认值（SPEC §13-2「不写死参数」），
 * 前端只按 kbType 决定显隐，不各自抄一份默认。
 *
 * 需求 15 的面板调整：重排序模型、问答/图谱抽取模型两栏从配置页撤掉
 * （doc 型不再用 chat_model_id 这一列，rerank 只按存量值透传）；原「解析与预处理」与
 * 「分块策略」合并成一页「解析配置」，开关和下拉按一行多列排，一屏放得下；
 * 知识图谱开关连同图谱抽取模型、图片理解模型一起放进这一页。
 */
import { computed, reactive, ref, watch } from 'vue';

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
  fetchUnderstandModels,
  fetchVideoModels,
  GetKbDetail,
  GetRuntimeConfig,
  KB_TYPE_AUDIO_VIDEO,
  KB_TYPE_DOC,
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
/** 媒体理解模型只给音视频型用；doc 型的图谱抽取、图片理解各走自己的一列 */
const chatOptions = ref<Array<{ label: string; value: number }>>([]);
const extractOptions = ref<Array<{ label: string; value: number }>>([]);
const imageOptions = ref<Array<{ label: string; value: number }>>([]);
/** doc 型库收到音频/视频时的转写模型（值存 parse_config，不占库表列） */
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
  // 重排序、问答这两栏已从配置页撤掉，值只从详情原样带回来，避免保存一次就把存量模型清零
  rerankModelId: 0,
  chatModelId: 0,
  extractModelId: 0,
  imageModelId: 0,
  parserEngine: 'native',
  parseConfig: {},
  chunkConfig: {},
  retrieveConfig: {},
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
function ensureRetrieve(): Record<string, any> {
  if (!form.retrieveConfig) form.retrieveConfig = {};
  return form.retrieveConfig;
}

const isDoc = computed(() => form.kbType === KB_TYPE_DOC);
const isAv = computed(() => form.kbType === KB_TYPE_AUDIO_VIDEO);

/** 引擎可选项（据 runtime.engines 的 available 置灰，mineru 未配置时不可选） */
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
 * 各引擎真读得动的文档格式（需求：页面要写清 native/docling/mineru 的覆盖范围）。
 *
 * 清单来自后端 /runtime-config.engineFormats，数据源是引擎类自己的 formats 声明：
 * 前端自己列一份，改一次支持面就会有一处不说实话（doc/ppt 就是这么从 docling 声明里被剔掉的）。
 * auto 不单列：它是「按文档复杂度在三个引擎间路由」的调度器，能吃的就是三者并集。
 */
const engineCoverage = computed(() =>
  ['native', 'docling', 'mineru']
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
 * 媒体转写模型的下拉把「未选」回显成空而不是 0：后端归一后的默认值是 0，
 * 直接丢给 Select 会在框里显示一个选不存在的选项 0，也清不掉。
 */
function blankMediaModels() {
  const p = ensureParse();
  p.audio_model_id =
    Number(p.audio_model_id) > 0 ? Number(p.audio_model_id) : undefined;
  p.video_model_id =
    Number(p.video_model_id) > 0 ? Number(p.video_model_id) : undefined;
}

/**
 * 按类型加载模型下拉；换类型时清掉不再适用的选择。
 * 向量模型在编辑态锁定，不重新拉也不会影响回显。
 *
 * doc 型只拉解析配置页上用得着的四组：图谱抽取模型、图片理解模型、音频/视频解析模型；
 * 问答模型与重排序模型已退出配置页，不再拉列表。
 */
async function loadModelOptions(type: KbType) {
  const tasks: Promise<any>[] = [
    fetchEmbedModels(type).then((v) => (embedOptions.value = v)),
  ];
  if (isAv.value) {
    tasks.push(fetchUnderstandModels().then((v) => (chatOptions.value = v)));
  } else {
    chatOptions.value = [];
  }
  if (isDoc.value) {
    tasks.push(
      fetchExtractModels().then((v) => (extractOptions.value = v)),
      fetchImageModels().then((v) => (imageOptions.value = v)),
      fetchAudioModels().then((v) => (audioOptions.value = v)),
      fetchVideoModels().then((v) => (videoOptions.value = v)),
    );
  } else {
    extractOptions.value = [];
    imageOptions.value = [];
    audioOptions.value = [];
    videoOptions.value = [];
  }
  await Promise.all(tasks);
}

/** 用 runtime-config 的归一默认值填充一份完整配置（新建时调用） */
function applyDefaults() {
  const r = runtime.value;
  form.parseConfig = { ...r?.parseDefaults };
  form.chunkConfig = { ...r?.chunkDefaults };
  form.retrieveConfig = { ...r?.retrieveDefaults };
  blankMediaModels();
  if (!form.chunkConfig.separators) form.chunkConfig.separators = [];
  if (!form.chunkConfig.context_augment)
    form.chunkConfig.context_augment = {
      enabled: false,
      before: 120,
      after: 120,
    };
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
        retrieveConfig: { ...detail.retrieveConfig },
        enableGraph: !!detail.enableGraph,
      });
      // 存量库可能还存着已下线的「LangChain 递归分块」：回显成合并后的策略，与后端归一保持一致
      const legacyStrategy =
        CHUNK_STRATEGY_LEGACY[String(form.chunkConfig?.strategy || '')];
      if (legacyStrategy) form.chunkConfig!.strategy = legacyStrategy;
      blankMediaModels();
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
    await loadModelOptions(t);
  },
);

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
  if (isAv.value && !form.chatModelId) {
    activeTab.value = 'basic';
    message.warning('音视频知识库必须选择媒体理解模型');
    return;
  }
  if (isDoc.value && form.enableGraph && !form.extractModelId) {
    activeTab.value = 'parse';
    message.warning('开启知识图谱需要选择图谱抽取模型');
    return;
  }
  if (isDoc.value && ensureParse().image_understand && !form.imageModelId) {
    activeTab.value = 'parse';
    message.warning('开启图片解析增强需要选择图片理解模型');
    return;
  }
  // 开了增强却没选模型，解析层只会塞一句降级警告（不失败）：用户看到的仍是「已完成」，
  // 但正文里一张描述也没有——这一类「什么都没报错什么都没发生」在提交前就拦住
  if (
    isDoc.value &&
    ensureParse().audio_understand &&
    !Number(ensureParse().audio_model_id)
  ) {
    activeTab.value = 'parse';
    message.warning('开启音频解析增强需要选择音频解析模型');
    return;
  }
  if (
    isDoc.value &&
    ensureParse().video_understand &&
    !Number(ensureParse().video_model_id)
  ) {
    activeTab.value = 'parse';
    message.warning('开启视频解析增强需要选择视频解析模型');
    return;
  }

  const payload: KbSaveReq = {
    kbType: form.kbType,
    name: form.name.trim(),
    description: form.description?.trim() || undefined,
    embedModelId: form.embedModelId,
    // 这两栏已从配置页撤掉：值按详情原样透传，编辑一次不该顺手清零
    rerankModelId: form.rerankModelId || 0,
    chatModelId: form.chatModelId || 0,
    retrieveConfig: ensureRetrieve(),
    // 扁平快捷字段：后端以它们覆盖对应分组键，避免前端两处不同步
    topK: Number(ensureRetrieve().top_k) || undefined,
    scoreThreshold: ensureRetrieve().score_threshold,
  };
  if (isDoc.value) {
    payload.parserEngine = form.parserEngine;
    payload.parseConfig = ensureParse();
    payload.chunkConfig = ensureChunk();
    payload.chunkSize = Number(ensureChunk().chunk_size) || undefined;
    payload.chunkOverlap = Number(ensureChunk().chunk_overlap) || 0;
    payload.enableGraph = !!form.enableGraph;
    // 开关关掉就不存模型选择，免得上一个图谱抽取模型的配置挂在库里说不清
    payload.extractModelId = form.enableGraph ? form.extractModelId || 0 : 0;
    // 图片理解模型是公用配置（与开关无关）：就算没开文档内嵌图片增强，
    // 往 doc 型库里传一张 jpg 仍靠它转写成正文，跟着开关清零会顺手抹掉一个还在用的选择
    payload.imageModelId = form.imageModelId || 0;
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

          <!-- 需求 15：重排序模型、问答/图谱抽取模型两栏已撤掉；
               doc 型的模型选择改到解析配置页，这里只留音视频型必需的媒体理解模型 -->
          <FormItem v-if="isAv" label="媒体理解模型" required>
            <Select
              v-model:value="form.chatModelId"
              :options="chatOptions"
              placeholder="选择视频/音频理解模型（生成媒体摘要）"
            />
            <div class="hint">音视频库必需：整条媒体的综合描述由它生成</div>
          </FormItem>
        </TabPane>

        <!-- ==================== 解析配置（需求 15：解析与预处理 + 分块策略合并成一页） ==================== -->
        <TabPane v-if="isDoc" key="parse" tab="解析配置">
          <Row :gutter="16">
            <Col :span="8">
              <FormItem label="解析引擎">
                <Select
                  v-model:value="form.parserEngine"
                  :options="engineSelectOptions"
                />
              </FormItem>
            </Col>
            <Col :span="8">
              <FormItem label="分块策略">
                <Select
                  v-model:value="chunkStrategy"
                  :options="strategySelectOptions"
                />
              </FormItem>
            </Col>
            <Col :span="8">
              <FormItem label="单块长度（字符）">
                <InputNumber
                  v-model:value="ensureChunk().chunk_size"
                  :min="50"
                  :max="runtime?.maxChunkChars || 20000"
                  style="width: 100%"
                />
              </FormItem>
            </Col>
          </Row>

          <!-- 策略专有项只在选中该策略时出现，同一行排开，不再一人占一整屏 -->
          <Row :gutter="16">
            <Col
              v-if="['fixed', 'delimiter'].includes(chunkStrategy)"
              :span="6"
            >
              <FormItem label="重叠长度">
                <InputNumber
                  v-model:value="ensureChunk().chunk_overlap"
                  :min="0"
                  :max="runtime?.maxChunkChars || 20000"
                  style="width: 100%"
                />
              </FormItem>
            </Col>

            <Col v-if="chunkStrategy === 'title'" :span="6">
              <FormItem label="标题层级（1~3）">
                <InputNumber
                  v-model:value="ensureChunk().title_level"
                  :min="1"
                  :max="3"
                  style="width: 100%"
                />
              </FormItem>
            </Col>

            <Col v-if="chunkStrategy === 'semantic'" :span="8">
              <FormItem label="语义相似度断开阈值">
                <Slider
                  v-model:value="ensureChunk().semantic_threshold"
                  :max="1"
                  :min="0"
                  :step="0.05"
                />
              </FormItem>
            </Col>

            <Col v-if="chunkStrategy === 'regex'" :span="10">
              <FormItem label="正则表达式">
                <Input
                  v-model:value="ensureChunk().regex_pattern"
                  placeholder="例如：第[一二三四五六七八九十]+章"
                />
              </FormItem>
            </Col>
          </Row>

          <Row :gutter="16">
            <Col v-if="chunkStrategy === 'delimiter'" :span="12">
              <FormItem label="分隔符列表">
                <Select
                  v-model:value="separatorValue"
                  mode="tags"
                  placeholder="输入后回车添加分隔符"
                  style="width: 100%"
                  @change="onSeparatorsChange"
                />
              </FormItem>
            </Col>

            <Col v-if="chunkStrategy === 'excel'" :span="6">
              <FormItem label="每块强制附带表头">
                <Switch v-model:checked="ensureChunk().keep_table_header" />
              </FormItem>
            </Col>

            <Col :span="6">
              <FormItem label="标题面包屑前缀写入向量文本">
                <Switch v-model:checked="ensureChunk().title_path" />
              </FormItem>
            </Col>
          </Row>

          <div class="group-title">预处理开关</div>
          <Row :gutter="16">
            <Col :span="6">
              <FormItem label="移除目录标题">
                <Switch v-model:checked="ensureParse().remove_toc" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="移除页眉页脚">
                <Switch v-model:checked="ensureParse().remove_header_footer" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="表格转 Markdown 文本">
                <Switch v-model:checked="ensureParse().table_as_text" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="保留分页标记">
                <Switch v-model:checked="ensureParse().keep_page_break" />
              </FormItem>
            </Col>
          </Row>

          <!-- 需求：三个「解析增强」开关常显、不限引擎（开着才调模型补描述）；
               图片理解模型是公用配置，常驻这一页，不再跟着开关出现或消失。
               知识图谱开关仍在本页（需求 15），抽取模型仍跟着开关显示（需求 13） -->
          <div class="group-title">解析增强（文档内嵌的图片/音频/视频）</div>
          <Row :gutter="16">
            <Col :span="6">
              <FormItem label="图片解析增强">
                <Switch v-model:checked="ensureParse().image_understand" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="音频解析增强">
                <Switch v-model:checked="ensureParse().audio_understand" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="视频解析增强">
                <Switch v-model:checked="ensureParse().video_understand" />
              </FormItem>
            </Col>
            <Col :span="6">
              <FormItem label="知识图谱">
                <Switch v-model:checked="form.enableGraph" />
              </FormItem>
            </Col>
          </Row>
          <div class="hint">
            三个增强开关只决定「要不要调模型给媒体补一段描述」，与选哪个解析引擎无关。
            不论开关开闭，文档内嵌的媒体都会存进对象存储、并把地址回填到正文原位；
            开启后才会再调一次对应的模型，把描述写在地址的下面一行。关掉不漏媒体，
            只是正文里只有地址没有描述，检索也就搜不到这张图/这段音视频的内容。
          </div>
          <div class="hint">
            能取到哪些内嵌媒体由引擎决定：内嵌音频/视频只有 native
            取得到字节（docx/pptx 媒体部件）；图片三个引擎都能拿到（native
            抽内嵌部件，docling/minerU 用它们自己导出的图片，PDF
            内嵌图同样）。走 docling/minerU
            时内嵌音频/视频取不到字节，正文里也就不会出现它们的地址。
          </div>

          <Row :gutter="16">
            <Col :span="12">
              <FormItem label="图片理解模型（公用）">
                <Select
                  v-model:value="form.imageModelId"
                  :options="imageOptions"
                  allow-clear
                  placeholder="凡是要理解图片的地方都用这一枚模型"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
            <Col v-if="form.enableGraph" :span="12">
              <FormItem label="图谱抽取模型" required>
                <Select
                  v-model:value="form.extractModelId"
                  :options="extractOptions"
                  placeholder="选择抽取实体与关系的模型"
                />
              </FormItem>
            </Col>
          </Row>
          <div class="hint">
            图片理解模型是公用配置：只要解析过程要用图片理解（开图片解析增强、
            或上传的就是图片文件），就调这里选的模型发接口，关掉开关不清空选择。
            知识图谱开启后文档解析完成会自动抽取实体关系入库，也可在文档列表手动触发构建图谱
          </div>

          <FormItem
            v-if="ensureParse().image_understand"
            label="图片描述提示词"
          >
            <Textarea
              v-model:value="ensureParse().image_desc_prompt"
              :rows="2"
              placeholder="留空使用内置默认提示词"
            />
          </FormItem>

          <!-- 文档型库现在也收图片/音频/视频：解析引擎从这类文件里抽不出正文，
               只能先让模型转写成文字，后面的分块/向量化/图谱与普通文档同一条链路 -->
          <div class="group-title">媒体模型与媒体文件转写</div>
          <Row :gutter="16">
            <Col :span="12">
              <FormItem label="音频解析模型">
                <Select
                  v-model:value="ensureParse().audio_model_id"
                  :options="audioOptions"
                  allow-clear
                  placeholder="上传音频文件或开音频解析增强时必填"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
            <Col :span="12">
              <FormItem label="视频解析模型">
                <Select
                  v-model:value="ensureParse().video_model_id"
                  :options="videoOptions"
                  allow-clear
                  placeholder="上传视频文件或开视频解析增强时必填"
                  show-search
                  :filter-option="(i: string, o: any) => o.label.includes(i)"
                />
              </FormItem>
            </Col>
          </Row>
          <div class="hint">
            两枚模型同时服务两件事：上传独立媒体文件时把它们整条转写/描述成正文，
            开启对应的解析增强时把它们用于文档内嵌的同种媒体。
            当前可上传的媒体后缀：图片 {{ mediaExtText('image') || '—' }}、音频
            {{ mediaExtText('audio') || '—' }}、视频
            {{ mediaExtText('video') || '—' }}。没选对应模型的媒体文件会解析失败
            （文档格式不受影响），转写结果与文件名一起分块入库，检索到的就是转写出来的文字
          </div>

          <FormItem label="视频内容描述提示词">
            <Textarea
              v-model:value="ensureParse().video_desc_prompt"
              :rows="2"
              placeholder="留空使用内置默认提示词"
            />
          </FormItem>

          <!-- 需求：页面要写清 native/docling/mineru 覆盖的文件类型范围。
               清单一律取后端 runtime-config 下发的 engineFormats，前端不自己抄一份 -->
          <div class="group-title">解析引擎覆盖的文档格式</div>
          <div v-for="one in engineCoverage" :key="one.engine" class="hint">
            {{ one.label }}：{{ one.formats.join(' / ') || '无' }}
          </div>
          <div class="hint">
            上列是各引擎真读得动的文档格式（老版 Office 的 .doc/.ppt
            三个引擎都读不动，需先另存为 docx/pptx）。 auto
            不单列：它在三者间按文档复杂度路由，能吃的就是三者并集
          </div>

          <Row :gutter="16">
            <Col :span="14">
              <FormItem label="图表/表格上下文补齐">
                <Space>
                  <Switch
                    v-model:checked="ensureChunk().context_augment.enabled"
                  />
                  <template v-if="ensureChunk().context_augment?.enabled">
                    <span>前</span>
                    <InputNumber
                      v-model:value="ensureChunk().context_augment.before"
                      :min="0"
                      style="width: 90px"
                    />
                    <span>后</span>
                    <InputNumber
                      v-model:value="ensureChunk().context_augment.after"
                      :min="0"
                      style="width: 90px"
                    />
                  </template>
                </Space>
              </FormItem>
            </Col>
          </Row>
        </TabPane>

        <!-- ==================== 检索 ==================== -->
        <TabPane key="retrieve" tab="检索配置">
          <!-- 需求 15：重排序开关跟着重排序模型一起退出配置页（retrieve_config.rerank 按存量值透传），
               知识图谱开关移到解析配置页，这一页只剩三个召回参数 -->
          <Row :gutter="16">
            <Col :span="8">
              <FormItem label="召回条数 TopK">
                <InputNumber
                  v-model:value="ensureRetrieve().top_k"
                  :min="1"
                  :max="100"
                  style="width: 100%"
                />
              </FormItem>
            </Col>
            <Col :span="8">
              <FormItem label="相似度阈值">
                <Slider
                  v-model:value="ensureRetrieve().score_threshold"
                  :max="1"
                  :min="0"
                  :step="0.05"
                />
              </FormItem>
            </Col>
            <Col v-if="isDoc" :span="8">
              <FormItem label="混合检索向量路权重">
                <Slider
                  v-model:value="ensureRetrieve().vector_similarity_weight"
                  :max="1"
                  :min="0"
                  :step="0.05"
                />
              </FormItem>
            </Col>
          </Row>
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

.sep-tags {
  margin-top: 8px;
}
</style>
