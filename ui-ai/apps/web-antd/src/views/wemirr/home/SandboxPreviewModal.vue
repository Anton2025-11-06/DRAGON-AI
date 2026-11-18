<script lang="ts" setup>
/**
 * 沙箱工作区文件预览弹窗（功能1 改版）。
 *
 * 原先预览画在右侧那条不到 400px 的工作区栏里，两个毛病：pdf / 表格 / 代码全被挤成一列；
 * office 文档压根没有渲染路径，只回一句「请下载查看」。这版：
 * - 渲染挪进独立弹窗，工作区栏只留文件列表，预览不再抢占列表空间；
 * - 办公文档交给 vue-files-preview 在浏览器里解析（平台早已声明该依赖但一直没启用）：
 *   docx 走 @vue-office/docx、表格走 @vue-office/excel(SheetJS)、pptx 走 pptx-preview。
 *   纯前端解析原始字节，不需要后端装 LibreOffice 转 PDF，也不依赖 Office 在线查看器
 *   ——那类方案都要求文件有公网可访问的地址，沙箱工作区里的文件恰恰给不出；
 * - 97-2003 的 .doc/.ppt 这类老二进制格式浏览器无解，继续占位 + 下载，文案与知识库
 *   解析侧「两个引擎都读不动，需先另存为 docx/pptx」同一口径；
 * - 文本类（md/code/html/csv/txt）不走预览库：平台的 MarkdownRenderer 内部已 sanitize，
 *   预览库的 md 是 markdown-it 直出。沙箱里的文件内容是模型写的，放开它就等于开一条 XSS。
 *
 * 标题行自带关闭按钮（closable=false）：antd 那个右上角的 X 是绝对定位在
 * .ant-modal-content 上的，位置在 header 的 padding-inline 之外还往里吃一大截，
 * 标题行里靠右摆的按钮躲不开它——「下载和 X 重叠」就是这么来的。与工作流对话弹窗同口径。
 *
 * 字节仍走下载口（api.SandboxFileBlob 带 token），与旧内嵌预览同一条链路，后端零改动。
 * 本组件由首页懒加载：预览库（codemirror/SheetJS/pptx-preview 等）只在真点开文件时才进包。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { VueFilesPreview } from 'vue-files-preview';
import 'vue-files-preview/es/style.css';

import { useAccessStore } from '@vben/stores';

import {
  CloseOutlined,
  CloudDownloadOutlined,
  FileTextFilled,
} from '@ant-design/icons-vue';
import { message, Modal } from 'ant-design-vue';

import { getTraceId } from '#/api/request';

import MarkdownRenderer from '../ai/workflow/components/debug/MarkdownRenderer.vue';
import * as api from './api';

const props = defineProps<{
  /** 文件名：预览方式按它的扩展名选 */
  name: string;
  open: boolean;
  /** 沙箱工作区绝对路径 */
  path: string;
  /** 列表带出的字节数；0 表示调用方不知道（输出物卡片），取回后再按实际大小判 */
  size?: number;
}>();

const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>();

const accessStore = useAccessStore();

type PreviewKind =
  | 'audio'
  | 'code'
  | 'csv'
  | 'html'
  | 'image'
  | 'legacy'
  | 'md'
  | 'pdf'
  | 'plugin'
  | 'text'
  | 'unknown'
  | 'video';

/** 扩展名 → 本地渲染类别（与旧内嵌预览同源；office 那几项改走 plugin / legacy 两支） */
const EXT_KIND: Record<string, PreviewKind> = {
  apng: 'image',
  bmp: 'image',
  gif: 'image',
  ico: 'image',
  jpeg: 'image',
  jpg: 'image',
  png: 'image',
  svg: 'image',
  webp: 'image',
  pdf: 'pdf',
  flac: 'audio',
  m4a: 'audio',
  mp3: 'audio',
  ogg: 'audio',
  wav: 'audio',
  m4v: 'video',
  mov: 'video',
  mp4: 'video',
  webm: 'video',
  markdown: 'md',
  md: 'md',
  c: 'code',
  cc: 'code',
  cpp: 'code',
  cs: 'code',
  css: 'code',
  go: 'code',
  h: 'code',
  hpp: 'code',
  ini: 'code',
  java: 'code',
  js: 'code',
  json: 'code',
  jsx: 'code',
  less: 'code',
  mjs: 'code',
  php: 'code',
  ps1: 'code',
  py: 'code',
  rb: 'code',
  rs: 'code',
  scss: 'code',
  sh: 'code',
  sql: 'code',
  toml: 'code',
  ts: 'code',
  tsx: 'code',
  yaml: 'code',
  yml: 'code',
  csv: 'csv',
  tsv: 'csv',
  htm: 'html',
  html: 'html',
  conf: 'text',
  log: 'text',
  txt: 'text',
  xml: 'text',
};

/**
 * 交给预览库在浏览器里解析的格式：OOXML（Office 2007+）系列 + 电子书 + Outlook 邮件。
 * .xls 也放进来——SheetJS 读得了老版 BIFF8 工作簿，而 .doc/.ppt 没有对应的浏览器解析器。
 */
const PLUGIN_EXTS = new Set([
  'docx',
  'epub',
  'msg',
  'ppsm',
  'ppsx',
  'pptm',
  'pptx',
  'xls',
  'xlsm',
  'xlsx',
  'xlt',
  'xltm',
]);

/** 浏览器没有可靠解析方案的桌面格式：只能下载后用本地 Office 打开（或先另存成新格式） */
const LEGACY_EXTS = new Set([
  'doc',
  'dps',
  'et',
  'odp',
  'ods',
  'odt',
  'ppt',
  'rtf',
  'wps',
]);

/** 老格式另存为什么能进预览：给一句能照做的提示，而不是只说「不支持」 */
const LEGACY_SAVE_AS: Record<string, string> = {
  doc: 'docx',
  dps: 'pptx',
  et: 'xlsx',
  odp: 'pptx',
  ods: 'xlsx',
  odt: 'docx',
  ppt: 'pptx',
  rtf: 'docx',
  wps: 'docx',
};

/** 二进制媒体扩展名 → MIME（喂 <img>/<iframe>/<audio>/<video> 的 typed Blob） */
const MIME_BY_EXT: Record<string, string> = {
  apng: 'image/apng',
  bmp: 'image/bmp',
  gif: 'image/gif',
  ico: 'image/x-icon',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  png: 'image/png',
  svg: 'image/svg+xml',
  webp: 'image/webp',
  pdf: 'application/pdf',
  flac: 'audio/flac',
  m4a: 'audio/mp4',
  mp3: 'audio/mpeg',
  ogg: 'audio/ogg',
  wav: 'audio/wav',
  m4v: 'video/x-m4v',
  mov: 'video/quicktime',
  mp4: 'video/mp4',
  webm: 'video/webm',
};

/** 这几类包 typed Blob 交浏览器原生渲染，不经预览库（预览库对它们也只是包一层标签） */
const MEDIA_KINDS = new Set<PreviewKind>(['audio', 'image', 'pdf', 'video']);

/** 文本类本地显示上限（后端下载口不截断，这里只防超大文本卡渲染） */
const TEXT_MAX_CHARS = 2_000_000;

/**
 * 单次取回字节上限：预览要把整份文件落在浏览器里（Blob + 解析器），
 * 几百 MB 的输出物直接读会把标签页打死，超了只给下载。
 */
const MAX_PREVIEW_BYTES = 60 * 1024 * 1024;

const loading = ref(false);
const errMsg = ref('');
const text = ref('');
const truncated = ref(false);
const tooLarge = ref(false);
/** 预览库要的是 File/Blob，object URL 反而不行（它内部自己 fetch，不带 token） */
const blob = ref<Blob | null>(null);
const blobUrl = ref('');
/** 预览库解析失败（文件损坏 / 不是真 OOXML / 解析器版本不认）的补话，不静默变白屏 */
const pluginErr = ref('');

function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i === -1 || i === name.length - 1
    ? ''
    : name.slice(i + 1).toLowerCase();
}

const ext = computed(() => extOf(props.name));

const kind = computed<PreviewKind>(() => {
  const byExt = EXT_KIND[ext.value];
  if (byExt) return byExt;
  if (PLUGIN_EXTS.has(ext.value)) return 'plugin';
  if (LEGACY_EXTS.has(ext.value)) return 'legacy';
  return 'unknown';
});

const sizeText = computed(() => {
  // 取回后以实际字节为准（输出物卡片那份没带 size）；用 ?? 不用 ||：空文件 0 字节不该回落旧值
  const bytes = blob.value?.size ?? props.size ?? 0;
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
});

/**
 * 预览库自带滚动（正文高度固定，表格/pdf 在里面滚），弹窗体就不再滚，避免两层滚动条；
 * 文本/图片类反过来靠弹窗体滚，正文高度自然生长。
 */
const bodyStyle = computed(() => {
  if (kind.value === 'plugin') {
    return { height: '76vh', overflow: 'hidden', padding: '8px 12px' };
  }
  return { maxHeight: '76vh', overflow: 'auto', padding: '12px 16px' };
});

const legacyHint = computed(() => {
  const saveAs = LEGACY_SAVE_AS[ext.value];
  const upper = ext.value.toUpperCase();
  if (saveAs) {
    return `${upper} 是老版二进制办公格式，浏览器里没有可靠的解析方案。请另存为 ${saveAs.toUpperCase()} 再预览，或下载后用本地 Office 打开。`;
  }
  return upper
    ? `${upper} 格式暂不支持在线预览，请下载后查看`
    : '该文件类型暂不支持在线预览，请下载后查看';
});

/** 代码类文本包成带语言标注的 ``` 围栏，交给 MarkdownRenderer 高亮 */
const codeFence = computed(() => `\`\`\`${ext.value}\n${text.value}\n\`\`\``);

function release() {
  if (blobUrl.value) URL.revokeObjectURL(blobUrl.value);
  blobUrl.value = '';
  blob.value = null;
}

function reset() {
  release();
  loading.value = false;
  errMsg.value = '';
  text.value = '';
  truncated.value = false;
  tooLarge.value = false;
  pluginErr.value = '';
}

/** 取字节并按类别落地（预览失败只写 errMsg，弹窗不关，用户还能点下载） */
async function load() {
  reset();
  if (!props.path) return;
  loading.value = true;
  try {
    const data = await api.SandboxFileBlob(
      props.path,
      accessStore.accessToken,
      getTraceId(),
    );
    if (data.size > MAX_PREVIEW_BYTES) {
      tooLarge.value = true;
      return;
    }
    blob.value = data;
    // 预览库自己从 Blob 取 ArrayBuffer（docx/excel）或直接喂容器（pptx）
    if (kind.value === 'plugin') return;
    if (MEDIA_KINDS.has(kind.value)) {
      // 按扩展名重新包 typed Blob（下载口 Content-Type 可能是 octet-stream，不重包渲染不了）
      const mime =
        MIME_BY_EXT[ext.value] || data.type || 'application/octet-stream';
      const buf = await data.arrayBuffer();
      blobUrl.value = URL.createObjectURL(new Blob([buf], { type: mime }));
    } else {
      // 文本类直接解码字节，绕开后端 read_text_file 的 200KB 截断；超上限再本地截断
      let content = await data.text();
      if (content.length > TEXT_MAX_CHARS) {
        content = content.slice(0, TEXT_MAX_CHARS);
        truncated.value = true;
      }
      text.value = content;
    }
  } catch (error: any) {
    errMsg.value = error?.message || '预览失败，请下载后查看';
  } finally {
    loading.value = false;
  }
}

/** 预览库渲染失败：它把异常原样抛出（多为文件本身不是合法 OOXML 包） */
function onPluginError(error: any) {
  pluginErr.value = error?.message || String(error || '解析失败');
}

/** 关闭：标题行自己那颗按钮走这里（Esc / 点遮罩仍由 Modal 自己管） */
function close() {
  emit('update:open', false);
}

/** 下载当前文件：已取回的字节直接落地，超大那份没取回则回落下载口 */
async function download() {
  try {
    const data =
      blob.value ??
      (await api.SandboxFileBlob(
        props.path,
        accessStore.accessToken,
        getTraceId(),
      ));
    const url = URL.createObjectURL(data);
    const a = document.createElement('a');
    a.href = url;
    a.download = props.name;
    a.click();
    // 延迟释放，避免部分浏览器在下载开始前 URL 失效
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {
    message.error('下载失败，请检查网络');
  }
}

// 换文件 / 重新打开都要重取：关掉只清状态（弹窗体 destroy-on-close，字节留着没用）
watch(
  () => [props.open, props.path],
  ([open]) => {
    if (open) {
      void load();
    } else {
      reset();
    }
  },
);

onBeforeUnmount(release);
</script>

<template>
  <Modal
    :body-style="bodyStyle"
    :closable="false"
    :open="open"
    :footer="null"
    destroy-on-close
    width="92%"
    class="sbx-preview-modal"
    @update:open="emit('update:open', $event)"
  >
    <template #title>
      <div class="sbx-title">
        <FileTextFilled class="sbx-title-icon" />
        <span class="sbx-title-name" :title="path">{{ name }}</span>
        <span v-if="sizeText" class="sbx-title-size">{{ sizeText }}</span>
        <!-- 按钮收进这一组靠右摆，与文件名/大小同一条 flex 行，位置由布局保证 -->
        <div class="sbx-title-actions">
          <a-button ghost size="small" type="primary" @click="download">
            <template #icon><CloudDownloadOutlined /></template>
            下载
          </a-button>
          <a-tooltip title="关闭（Esc 或点遮罩也能关）">
            <a-button size="small" type="text" @click="close">
              <template #icon><CloseOutlined /></template>
            </a-button>
          </a-tooltip>
        </div>
      </div>
    </template>

    <a-spin :spinning="loading" wrapper-class-name="sbx-spin">
      <div v-if="errMsg" class="sbx-tip sbx-tip-err">{{ errMsg }}</div>
      <div v-else-if="tooLarge" class="sbx-tip">
        文件超过
        {{ Math.round(MAX_PREVIEW_BYTES / 1024 / 1024) }}
        MB，整份读进浏览器会卡死，请用右上角「下载」取回后本地打开
      </div>

      <!-- 办公文档 / 电子书 / 邮件：预览库按扩展名自己选解析器 -->
      <div v-else-if="kind === 'plugin'" class="sbx-plugin">
        <div v-if="pluginErr" class="sbx-tip sbx-tip-err">
          预览库没能解析这份 {{ ext.toUpperCase() }} 文件：{{ pluginErr }}
          <span class="sbx-tip-sub">
            文件损坏或不是真正的 OOXML 包（比如把 .doc 直接改名成
            .docx），请下载后用本地 Office 打开
          </span>
        </div>
        <VueFilesPreview
          v-else-if="blob"
          :file="blob"
          :name="name"
          height="100%"
          @error="onPluginError"
        />
      </div>

      <img
        v-else-if="kind === 'image'"
        :alt="name"
        :src="blobUrl"
        class="sbx-img"
      />
      <iframe
        v-else-if="kind === 'pdf'"
        :src="blobUrl"
        class="sbx-frame"
        title="pdf 预览"
      ></iframe>
      <audio
        v-else-if="kind === 'audio'"
        :src="blobUrl"
        class="sbx-media"
        controls
      ></audio>
      <video
        v-else-if="kind === 'video'"
        :src="blobUrl"
        class="sbx-video"
        controls
      ></video>
      <!-- markdown / 代码走平台渲染器（内部已 sanitize），不交预览库的 markdown-it -->
      <MarkdownRenderer
        v-else-if="kind === 'md'"
        :content="text"
        class="sbx-md"
      />
      <MarkdownRenderer
        v-else-if="kind === 'code'"
        :content="codeFence"
        class="sbx-md"
      />
      <!-- 模型产出的 html 一律空 sandbox：不给它脚本与同源能力，只当静态版式看 -->
      <iframe
        v-else-if="kind === 'html'"
        :srcdoc="text"
        class="sbx-frame"
        sandbox=""
        title="html 预览"
      ></iframe>
      <pre v-else-if="kind === 'text' || kind === 'csv'" class="sbx-pre">{{
        text
      }}</pre>
      <div v-else class="sbx-tip">{{ legacyHint }}</div>

      <div v-if="!loading && truncated" class="sbx-truncated">
        内容过长已截断，仅显示前 {{ text.length }} 字符，下载查看完整内容
      </div>
    </a-spin>
  </Modal>
</template>

<style lang="less" scoped>
.sbx-title {
  display: flex;
  gap: 10px;
  align-items: center;

  .sbx-title-icon {
    flex-shrink: 0;
    color: var(--primary-color, #1677ff);
  }

  .sbx-title-name {
    overflow: hidden;
    flex: 1;
    min-width: 0;
    font-size: 14px;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sbx-title-size {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--text-color-3, #8a9099);
  }

  // flex-shrink: 0 保证窄屏（或文件名很长）时按钮组不被压扁，省略号落在名字上
  .sbx-title-actions {
    display: flex;
    flex-shrink: 0;
    gap: 6px;
    align-items: center;
  }
}

.sbx-plugin {
  // 定高写死在这里，不靠 100% 往上传：中间隔了 a-spin 的两层 div 高度都是 auto，
  // 百分比高度落到它们身上算不出来，excel/pdf 容器会直接塌成 0 高（看着就是一片白）。
  // 减掉 bodyStyle 给 plugin 分支的上下 8px padding：写满 76vh 会把最底下那 16px
  // （excel 最后一行 / pptx 页脚）顶到 overflow:hidden 的框外面裁掉
  height: calc(76vh - 16px);
  overflow: hidden;
  background: #fff;

  // 预览库内部容器（docx 页边距、excel 表头、pdf 页与页间距）自带样式，
  // 这里只把它放进弹窗给的定高盒子里，不覆盖它的排版
  :deep(.vue-files-preview) {
    height: 100%;
  }

  :deep(.docx-wrapper) {
    padding: 12px 0;
    background: #fff;
  }
}

.sbx-tip {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  padding: 20px;
  font-size: 13px;
  color: var(--text-color-3, #8a9099);
  text-align: center;

  .sbx-tip-sub {
    font-size: 12px;
  }
}

.sbx-tip-err {
  color: #d4380d;
}

.sbx-img {
  display: block;
  max-width: 100%;
  max-height: 72vh;
  margin: 0 auto;
}

.sbx-frame {
  width: 100%;
  // 同样避开 body 的上下 12px padding，多留 4px 余量，pdf 自身带滚动就不套第二层滚动条
  height: calc(76vh - 28px);
  border: none;
  background: #fff;
}

.sbx-media {
  width: 100%;
}

.sbx-video {
  max-height: 72vh;
  width: 100%;
  background: #000;
}

.sbx-pre {
  margin: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.sbx-truncated {
  margin-top: 10px;
  padding: 6px 10px;
  font-size: 12px;
  color: #ad6800;
  background: rgba(250, 173, 20, 0.1);
  border-radius: 6px;
}
</style>
