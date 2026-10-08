<script lang="ts" setup name="RagKnowledgeRetrieve">
/**
 * 知识检索页（需求 4：从「知识库列表」行内按钮升级为独立菜单页）。
 *
 * 页面职责 = 「问一句话，看它到底从哪些库里捞回了什么」：
 * - 库可多选（空 = 全部可用库，后端按 ACL 白名单收敛，不会越权召回）；
 * - 三类知识库共用一个口子，召回逻辑由后端按 kb_type 自动切换，前端只按 chunkType 分模态渲染；
 * - 选了图片库才出现「图搜图」上传（先传公共存储换 URL，再随 query 一起发）；
 * - 选了文档库才出现「附带图谱实体」（图谱只服务 doc 型）。
 *
 * API 复用 ../doc/api.ts：那份文件是 RAG 模块契约的唯一来源（枚举、字段名都与
 * common_constants/rag_constant.py 静态对账），拆成两份只会让两边各漂一半。
 */
import type { KbOption, RetrieveHit, RetrieveResp } from '../doc/api';

import { computed, onMounted, ref } from 'vue';
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
  Checkbox,
  Empty,
  Input,
  InputNumber,
  message,
  Select,
  Slider,
  Space,
  Spin,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  KB_TYPE_AUDIO_VIDEO,
  KB_TYPE_DOC,
  KB_TYPE_IMAGE,
  KbOptions,
  RAG_AUDIO_EXTS,
  RETRIEVE_MODE_LABELS,
  RETRIEVE_MODES_ALL,
  Retrieve,
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
const vectorWeight = ref<number | undefined>();
const mode = ref<string | undefined>();
const withGraph = ref(false);
const uploading = ref(false);

const loading = ref(false);
const searched = ref(false);
const result = ref<RetrieveResp | null>(null);
const activeTab = ref('text');
// 展开完整正文的命中：按 ES 的 _id 语义拼 kbId_docId_chunkIndex，避免同文档多命中串在一起
const expanded = ref<Set<string>>(new Set());

const selectedOptions = computed(() =>
  options.value.filter((o) => kbIds.value.includes(o.id)),
);
const hasImageKb = computed(() =>
  selectedOptions.value.some((o) => o.kbType === KB_TYPE_IMAGE),
);
const hasDocKb = computed(() =>
  selectedOptions.value.some((o) => o.kbType === KB_TYPE_DOC),
);

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
  const ref = String(h.mediaUrl || '').split('?')[0];
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

async function onUploadImage(file: File) {
  uploading.value = true;
  try {
    imageUrl.value = await uploadQueryImage(file);
    message.success('查询图片已就绪');
  } catch (e: any) {
    message.error(e?.message || '图片上传失败');
  } finally {
    uploading.value = false;
  }
  return false;
}

async function doSearch() {
  if (!allowed.value) {
    return message.warning('没有「知识检索」的菜单权限（ai:kb:search）');
  }
  if (!query.value.trim() && !imageUrl.value) {
    return message.warning('请输入查询内容，或上传一张查询图片');
  }
  loading.value = true;
  result.value = null;
  expanded.value = new Set();
  try {
    const res = await Retrieve({
      kbIds: kbIds.value,
      query: query.value.trim() || undefined,
      imageUrl: imageUrl.value || undefined,
      topK: topK.value,
      mode: mode.value,
      scoreThreshold: scoreThreshold.value,
      vectorWeight: vectorWeight.value,
      withGraph: hasDocKb.value ? withGraph.value : false,
    });
    result.value = res;
    searched.value = true;
    // 哪个模态有结果就自动切过去，省得用户对着空白的「文本」页以为是检索坏了
    const first = textHits.value.length
      ? 'text'
      : imageHits.value.length
        ? 'image'
        : avHits.value.length
          ? 'av'
          : graphEntities.value.length
            ? 'graph'
            : 'text';
    activeTab.value = first;
  } catch (e: any) {
    message.error(e?.message || '检索失败');
  } finally {
    loading.value = false;
  }
}

function reset() {
  query.value = '';
  imageUrl.value = '';
  withGraph.value = false;
  result.value = null;
  searched.value = false;
  expanded.value = new Set();
}

onMounted(loadOptions);
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
        <!-- 参数区：库、模式、条数、阈值、向量权重一行放完，窄屏自动换行 -->
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
          <Select
            v-model:value="mode"
            :options="
              RETRIEVE_MODES_ALL.map((m) => ({
                label: RETRIEVE_MODE_LABELS[m],
                value: m,
              }))
            "
            placeholder="检索模式"
            allow-clear
            style="width: 120px"
          />
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
            title="混合模式下向量分的占比，留空 = 用知识库自己的配置；调低更偏关键词字面匹配"
          >
            <span class="param-label">向量权重</span>
          </Tooltip>
          <Slider
            v-model:value="vectorWeight"
            :max="1"
            :min="0"
            :step="0.05"
            style="width: 120px"
            tooltip-placement="top"
          />
          <Checkbox v-if="hasDocKb" v-model:checked="withGraph"
            >附带图谱实体</Checkbox
          >
        </Space>

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
            :loading="loading"
            type="primary"
            @click="doSearch"
          >
            检索
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
            <Button size="small" type="link" @click="imageUrl = ''"
              >移除</Button
            >
          </template>
        </Space>
      </Space>
    </Card>

    <Card :bordered="false" class="result-card">
      <Spin :spinning="loading">
        <Alert
          v-if="result"
          type="info"
          class="mb-2"
          :message="`模式 ${RETRIEVE_MODE_LABELS[result.mode] || result.mode} · 命中 ${result.total} 条 · 耗时 ${result.tookMs}ms`"
        />
        <Alert
          v-for="(w, i) in result?.warnings || []"
          :key="i"
          type="warning"
          :message="w"
          class="mb-2"
          show-icon
        />

        <Tabs v-if="result" v-model:activeKey="activeTab">
          <TabPane key="text" :tab="`文本命中 (${textHits.length})`">
            <div v-if="textHits.length" class="hit-group">
              <div v-for="(h, i) in textHits" :key="`t${i}`" class="text-hit">
                <div class="hit-head">
                  <Tag color="blue">{{ h.kbName || `库 ${h.kbId}` }}</Tag>
                  <span class="hit-doc">{{ h.docName }}</span>
                  <span v-if="h.titlePath" class="hit-path">{{
                    h.titlePath
                  }}</span>
                  <Tag color="green">score {{ h.score.toFixed(3) }}</Tag>
                  <Tag v-if="h.vectorScore"
                    >向量 {{ h.vectorScore.toFixed(3) }}</Tag
                  >
                  <Tag v-if="h.keywordScore"
                    >关键词 {{ h.keywordScore.toFixed(3) }}</Tag
                  >
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
            <div v-if="imageHits.length" class="image-wall">
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
                </div>
              </a>
            </div>
            <Empty v-else description="没有图片命中" />
          </TabPane>

          <TabPane key="av" :tab="`音视频命中 (${avHits.length})`">
            <div v-if="avHits.length" class="hit-group">
              <div v-for="(h, i) in avHits" :key="`a${i}`" class="av-hit">
                <div class="hit-head">
                  <VideoCameraOutlined />
                  <span class="hit-doc">{{ h.docName }}</span>
                  <span v-if="h.mediaDuration" class="hit-path">
                    {{ formatDuration(h.mediaDuration) }}
                  </span>
                  <Tag color="green">score {{ h.score.toFixed(3) }}</Tag>
                </div>
                <audio v-if="isAudioHit(h)" :src="h.mediaUrl" controls></audio>
                <video v-else :src="h.mediaUrl" controls></video>
                <div class="hit-body">{{ h.content }}</div>
              </div>
            </div>
            <Empty v-else description="没有音视频命中" />
          </TabPane>

          <TabPane
            v-if="withGraph && hasDocKb"
            key="graph"
            :tab="`图谱实体 (${graphEntities.length})`"
          >
            <div v-if="graphEntities.length" class="hit-group">
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

        <Empty v-else :description="searched ? '没有命中' : '输入查询开始检索'">
          <template #image>
            <FileSearchOutlined style="font-size: 48px; color: #bbb" />
          </template>
        </Empty>
      </Spin>
    </Card>
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
