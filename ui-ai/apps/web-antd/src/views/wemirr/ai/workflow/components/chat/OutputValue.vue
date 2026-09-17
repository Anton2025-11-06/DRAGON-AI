<script setup lang="ts">
/**
 * 对话窗口的通用取值渲染器。
 *
 * 输入是「任意 JSON 值」：可能是大模型正文、结构化 JSON、向量数组、重排分数组、
 * 文生图/视频/音频直链、文件对象或它们的数组，也可能是数字/布尔/空值。
 * 按值形态挑渲染方式：空值 > 标量 > 字符串（媒体/JSON/正文）> 数组（文件/媒体/向量）
 * > 对象（文件/媒体折叠/正文折叠/键值行）> JSON 树兜底，
 * 新增节点类型或模型能力类型时不需要改这里。
 */
import type { PropType } from 'vue';

import { computed } from 'vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';

import {
  CopyOutlined,
  LinkOutlined,
  PaperClipOutlined,
} from '@ant-design/icons-vue';
import { message, Tooltip } from 'ant-design-vue';

import MarkdownRenderer from '../debug/MarkdownRenderer.vue';
import {
  asFileArray,
  asFileObject,
  asFlatObject,
  asNumberArray,
  asUrlArray,
  collectMediaUrls,
  copyToClipboard,
  getMediaType,
  isUrlLike,
  parseJsonText,
  TEXT_VALUE_KEYS,
  toCopyText,
} from './chat-output';

interface ResolvedView {
  /** 传给对应分支使用的数据 */
  data: any;
  /** 值采用的渲染形态 */
  kind:
    | 'empty'
    | 'files'
    | 'kv'
    | 'markdown'
    | 'media'
    | 'pretty'
    | 'scalar'
    | 'vectors';
  /** kind=media 时的媒体类型 */
  media: 'audio' | 'image' | 'video';
  /** 被主体（媒体/正文）没吃掉的数据，跟在主体后面继续渲染 */
  rest?: any;
  /** 长数值数组的摘要文案 */
  summary: string;
  /** kind=markdown 且值不是字符串时，从对象里提出的正文 */
  text?: string;
  /** 媒体地址列表 */
  urls: string[];
}

const props = defineProps({
  /** 需要渲染的任意值 */
  value: { default: undefined, type: Object as PropType<any> },
  /** 变量名（outputs 的 key） */
  label: { default: '', type: String },
  /**
   * 是否挂自己的复制按钮。外层已有一块一个复制按钮时传 false，
   * 否则一个数据块里会出现两个语义不同的复制入口。
   */
  showCopy: { default: true, type: Boolean },
});

/**
 * 判定值形态。顺序即优先级：能明确识别的先识别，识别不出的一律退回 JSON 树，
 * 保证任何结构都渲染得出来（不丢数据）。
 */
function resolveView(value: any): ResolvedView {
  const base: ResolvedView = {
    data: value,
    kind: 'pretty',
    media: 'image',
    summary: '',
    urls: [],
  };

  if (value === null || value === undefined || value === '') {
    return { ...base, kind: 'empty' };
  }

  if (typeof value === 'boolean' || typeof value === 'number') {
    return { ...base, kind: 'scalar' };
  }

  if (typeof value === 'string') {
    const media = getMediaType(value);
    if (media) return { ...base, kind: 'media', media, urls: [value.trim()] };
    const json = parseJsonText(value);
    if (json) return { ...base, data: json.data, kind: 'pretty' };
    return { ...base, kind: 'markdown' };
  }

  if (Array.isArray(value)) {
    const files = asFileArray(value);
    if (files) {
      return {
        ...base,
        data: files,
        kind: 'files',
        urls: files.map((item) => item.url),
      };
    }
    const urls = asUrlArray(value);
    if (urls) {
      const media = getMediaType(urls[0]);
      if (media && urls.every((item) => getMediaType(item) === media)) {
        return { ...base, kind: 'media', media, urls };
      }
    }
    const numbers = asNumberArray(value);
    if (numbers) {
      // 向量/重排分：给维度与前若干数值即可，上千个数字不该铺满屏幕
      const head = numbers.slice(0, 6).map((item) => Number(item.toFixed(4)));
      return {
        ...base,
        kind: 'vectors',
        summary: `${numbers.length} 项 · [${head.join(', ')}${numbers.length > head.length ? ', …' : ''}]`,
      };
    }
    return base;
  }

  const singleFile = asFileObject(value);
  if (singleFile) {
    const media = getMediaType(singleFile.url);
    if (media) {
      return { ...base, kind: 'media', media, urls: [singleFile.url] };
    }
    return { ...base, data: [singleFile], kind: 'files' };
  }

  // 生成类节点会把同一条直链写进 output/url/urls/video_url 等多个键，
  // 先按值提出媒体地址，不能整块退化成 JSON 树
  const mediaView = resolveMediaObject(value as Record<string, any>);
  if (mediaView) return { ...base, ...mediaView };

  // 文本类节点的输出是 {output, text, usage} 这类重复包装，取正文、其余当补充数据
  const textView = resolveTextObject(value as Record<string, any>);
  if (textView) return { ...base, ...textView };

  // 少量纯标量的对象（如知识检索的 {query, count}）按行展示比 JSON 树易读
  const flat = asFlatObject(value);
  if (flat?.every(([, item]) => typeof item !== 'object' || item === null)) {
    return { ...base, data: flat, kind: 'kv' };
  }
  return base;
}

/** 空值不参与补充数据：{a: '', b: null} 占行但没有信息 */
function isMeaningful(item: any): boolean {
  return item !== null && item !== undefined && item !== '';
}

/**
 * 对象里的媒体产物：带直链的键渲染成播放器，其余键只在都是标量时作为补充。
 * 补充数据里还有数组/对象时不折叠（那是一份独立业务数据，铺成 JSON 树更易读）。
 */
function resolveMediaObject(
  value: Record<string, any>,
): null | Partial<ResolvedView> {
  const mediaUrls: string[] = [];
  const rest: [string, any][] = [];
  for (const [key, item] of Object.entries(value)) {
    const found = collectMediaUrls(item);
    if (found.length > 0) mediaUrls.push(...found);
    else if (isMeaningful(item)) rest.push([key, item]);
  }
  const urls = [...new Set(mediaUrls)];
  if (urls.length === 0) return null;
  const media = getMediaType(urls[0]);
  if (!media || !urls.every((item) => getMediaType(item) === media))
    return null;
  if (rest.some(([, item]) => item !== null && typeof item === 'object')) {
    return null;
  }
  return {
    kind: 'media',
    media,
    rest: rest.length > 0 ? Object.fromEntries(rest) : undefined,
    urls,
  };
}

/** 对象里的正文：取第一个命中的常见键名，重复写到的同值键不再铺一遍 */
function resolveTextObject(
  value: Record<string, any>,
): null | Partial<ResolvedView> {
  let text = '';
  for (const key of TEXT_VALUE_KEYS) {
    const item = value[key];
    if (typeof item === 'string' && item.trim()) {
      text = item;
      break;
    }
  }
  if (!text) return null;
  const rest = Object.entries(value).filter(
    ([key, item]) =>
      !(TEXT_VALUE_KEYS.includes(key) && item === text) && isMeaningful(item),
  );
  return {
    data: text,
    kind: 'markdown',
    rest: rest.length > 0 ? Object.fromEntries(rest) : undefined,
    text,
  };
}

const view = computed(() => resolveView(props.value));

/** markdown 正文（字符串直接用，对象取提出来的正文键） */
const markdownText = computed(() =>
  typeof props.value === 'string' ? props.value : view.value.text || '',
);

/** 标量转展示文本（布尔/数字/空） */
function kvText(item: any): string {
  if (item === null || item === undefined || item === '') return '—';
  return String(item);
}

async function copyValue() {
  if (await copyToClipboard(toCopyText(props.value))) {
    message.success('已复制到剪贴板');
  } else {
    message.error('复制失败');
  }
}

/** 外层包了复制按钮时，本组件不再重复挂头 */
const copyable = computed(() => props.showCopy && view.value.kind !== 'empty');

async function copyText(text: string) {
  if (!text) return;
  if (await copyToClipboard(text)) {
    message.success('已复制地址');
  } else {
    message.error('复制失败');
  }
}

/** 地址文本：data URI 动辄几十 KB，折叠成头尾再展示 */
function urlText(url: string): string {
  return url.length > 90 ? `${url.slice(0, 46)}…${url.slice(-24)}` : url;
}
</script>

<template>
  <div class="chat-output-value">
    <div v-if="label || copyable" class="value-head">
      <span v-if="label" class="value-label">{{ label }}</span>
      <Tooltip v-if="copyable" title="复制">
        <a class="copy-btn" @click="copyValue"><CopyOutlined /></a>
      </Tooltip>
    </div>

    <div class="value-body">
      <span v-if="view.kind === 'empty'" class="value-empty">—</span>

      <code v-else-if="view.kind === 'scalar'" class="value-scalar">{{
        String(value)
      }}</code>

      <span v-else-if="view.kind === 'vectors'" class="value-vectors">{{
        view.summary
      }}</span>

      <div v-else-if="view.kind === 'markdown'" class="value-markdown">
        <MarkdownRenderer :content="markdownText" />
      </div>

      <div v-else-if="view.kind === 'media'" class="value-media">
        <template v-for="(url, index) in view.urls" :key="`${url}-${index}`">
          <img
            v-if="view.media === 'image'"
            class="media-image"
            :src="url"
            alt=""
          />
          <video
            v-else-if="view.media === 'video'"
            class="media-video"
            controls
          >
            <source :src="url" />
          </video>
          <audio v-else class="media-audio" controls>
            <source :src="url" />
          </audio>
        </template>
        <!-- 媒体下方保留地址本身：直链就是产物，用户可能要拿走外用 -->
        <div class="media-urls">
          <div
            v-for="(url, index) in view.urls"
            :key="index"
            class="media-url-row"
          >
            <a
              class="media-url"
              :href="url"
              :title="url"
              target="_blank"
              rel="noopener"
            >
              <LinkOutlined />
              {{ urlText(url) }}
            </a>
            <Tooltip title="复制地址">
              <a class="copy-btn" @click="copyText(url)"><CopyOutlined /></a>
            </Tooltip>
          </div>
        </div>
      </div>

      <div v-else-if="view.kind === 'files'" class="value-files">
        <div v-for="file in view.data" :key="file.url" class="value-file-item">
          <PaperClipOutlined />
          <a :href="file.url" target="_blank" rel="noopener">{{ file.name }}</a>
        </div>
      </div>

      <div v-else-if="view.kind === 'kv'" class="value-kv">
        <div v-for="[key, item] in view.data" :key="key" class="kv-row">
          <span class="kv-key">{{ key }}</span>
          <a
            v-if="isUrlLike(item)"
            class="kv-url"
            :href="item"
            target="_blank"
            rel="noopener"
          >
            <LinkOutlined />
            {{ item }}
          </a>
          <span v-else class="kv-val">{{ kvText(item) }}</span>
        </div>
      </div>

      <div v-else class="value-json">
        <VueJsonPretty :data="view.data" :deep="3" :show-length="true" />
      </div>

      <!-- 主体（播放器/正文）没吃掉的数据不丢，跟在后面按同一口径渲染 -->
      <div v-if="view.rest" class="value-rest">
        <OutputValue :show-copy="false" :value="view.rest" />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.chat-output-value {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: flex-start;
  min-width: 0;

  .value-head {
    display: flex;
    flex-shrink: 0;
    gap: 4px;
    align-items: center;

    .value-label {
      min-width: 56px;
      font-size: 12px;
      color: #8c8c8c;
    }
  }

  .copy-btn {
    font-size: 12px;
    color: #bfbfbf;
    cursor: pointer;

    &:hover {
      color: #1890ff;
    }
  }

  .value-body {
    flex: 1;
    min-width: 0;
  }

  .value-empty {
    color: #bfbfbf;
  }

  .value-scalar {
    padding: 1px 6px;
    font-size: 13px;
    color: #1d39c4;
    background: #f5f7fa;
    border-radius: 4px;
  }

  .value-vectors {
    display: inline-block;
    padding: 2px 8px;
    font-size: 12px;
    color: #595959;
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 4px;
  }

  .value-media {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    gap: 8px;
    min-width: 0;

    .media-image {
      max-width: 220px;
      max-height: 220px;
      object-fit: contain;
      border: 1px solid #f0f0f0;
      border-radius: 6px;
    }

    .media-video {
      max-width: 100%;
      border-radius: 6px;
    }

    .media-audio {
      width: 100%;
      min-width: 240px;
    }

    .media-urls {
      display: flex;
      flex: 1 1 100%;
      flex-direction: column;
      gap: 4px;
    }

    .media-url-row {
      display: flex;
      gap: 6px;
      align-items: center;
      min-width: 0;
    }

    .media-url {
      flex: 1;
      min-width: 0;
      font-size: 12px;
      color: #8c8c8c;
      overflow-wrap: anywhere;

      &:hover {
        color: #1890ff;
      }
    }
  }

  .value-files {
    display: flex;
    flex-direction: column;
    gap: 4px;

    .value-file-item {
      display: flex;
      gap: 6px;
      align-items: center;
      font-size: 13px;
    }
  }

  .value-kv {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 13px;

    .kv-row {
      display: flex;
      gap: 8px;
      align-items: baseline;
    }

    .kv-key {
      min-width: 72px;
      font-size: 12px;
      color: #8c8c8c;
    }

    .kv-url {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .value-json {
    max-width: 100%;
    overflow-x: auto;
  }

  .value-rest {
    width: 100%;
    padding-top: 6px;
    margin-top: 6px;
    border-top: 1px dashed #f0f0f0;
  }
}
</style>
