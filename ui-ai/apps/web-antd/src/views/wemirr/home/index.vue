<script lang="ts">
import {
  computed,
  defineComponent,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
} from 'vue';

import { useAccessStore } from '@vben/stores';

import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DoubleLeftOutlined,
  DoubleRightOutlined,
  FileTextFilled,
  FolderFilled,
  LoadingOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { getTraceId } from '#/api/request';

import MarkdownRenderer from '../ai/workflow/components/debug/MarkdownRenderer.vue';
import * as api from './api';

/** 时间线片段：assistant 消息按到达顺序拆成思考/工具/文本三类块，各自可折叠 */
type Segment =
  | { kind: 'thinking'; id: string; text: string; active?: boolean }
  | { kind: 'text'; id: string; text: string }
  | {
      kind: 'tool';
      id: string;
      tool: string;
      arguments: string;
      done: boolean;
      /** 模型在这次调用入参里写的用途（工具行标题首选文案，可能缺失） */
      purpose?: string;
    };

/** 工具技术名 → 中文动作名：模型没写 purpose 时的次级回落标题 */
const TOOL_KIND_LABELS: Record<string, string> = {
  AskUser: '向你提问',
  Bash: '执行命令',
  Edit: '编辑文件',
  Glob: '查找文件',
  Grep: '搜索内容',
  PowerShell: '执行命令',
  Read: '读取文件',
  TaskCreate: '新建任务',
  TaskGet: '查看任务',
  TaskList: '查看任务清单',
  TaskUpdate: '更新任务',
  Write: '写入文件',
  kb_search: '检索知识库',
};

/** 入参里最能说明「这次在对什么动手」的字段，按优先级取第一个非空串 */
const TOOL_ARG_HINT_KEYS = [
  'file_path',
  'path',
  'pattern',
  'query',
  'question',
  'command',
  'url',
];

/** 一行放得下的字数：标题超宽会把整条时间线挤乱 */
const TOOL_LABEL_MAX = 80;
const TOOL_HINT_MAX = 60;
const THINKING_HINT_MAX = 48;

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 工具入参 JSON → 对象（非法/未收全返回 null，由调用方回落原文） */
function parseToolArgs(raw: string): null | Record<string, unknown> {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null; // 入参可能还在流式拼装中，不是错误
  }
  return null;
}

/** 路径只留最后一段：/workspace/data/sales.csv → sales.csv（整条路径放不进一行标题） */
function shortPath(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? value;
}

/** 模型没写 purpose 时从入参派生一句关键信息（文件名 / 命令 / 查询词） */
function toolArgHint(argumentsText: string): string {
  const args = parseToolArgs(argumentsText);
  if (!args) return '';
  for (const key of TOOL_ARG_HINT_KEYS) {
    const value = args[key];
    if (typeof value !== 'string' || !value.trim()) continue;
    const text =
      key === 'file_path' || key === 'path'
        ? shortPath(value.trim())
        : value.trim();
    return clip(text, TOOL_HINT_MAX);
  }
  return '';
}

/**
 * 工具行标题：模型写的用途 → 入参派生 → 中文动作名。
 * 三级回落是为了任何一条工具行都有人话标题——purpose 是选填的（写进 required
 * 会让模型漏填时整个调用被 schema 校验打回重试），缺了不能只显示 Edit 这种生硬名字。
 */
function toolLabel(seg: Segment): string {
  if (seg.kind !== 'tool') return '';
  const purpose = (seg.purpose || '').trim();
  if (purpose) return clip(purpose, TOOL_LABEL_MAX);
  const kind = TOOL_KIND_LABELS[seg.tool] || seg.tool || '调用工具';
  const hint = toolArgHint(seg.arguments);
  return hint ? `${kind} · ${hint}` : kind;
}

/** 折叠里的入参：剔掉 purpose（已当标题显示）并格式化，命令行一眼可读 */
function toolArgsView(seg: Segment): string {
  if (seg.kind !== 'tool') return '';
  const args = parseToolArgs(seg.arguments);
  if (!args) return seg.arguments; // 非 JSON（非法或未收全）原样给排障看
  const rest = { ...args };
  delete rest.purpose;
  if (Object.keys(rest).length === 0) return '';
  return JSON.stringify(rest, null, 2);
}

/** 思考块折叠时的一行摘要：末行非空文本，不点开也能看到模型在想什么 */
function thinkingHint(text: string): string {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return clip(lines.at(-1) ?? '', THINKING_HINT_MAX);
}

type PreviewKind =
  | 'audio'
  | 'code'
  | 'csv'
  | 'html'
  | 'image'
  | 'md'
  | 'office'
  | 'pdf'
  | 'text'
  | 'unknown'
  | 'video';

/** 扩展名 → 预览类别（image/pdf/audio/video 走 typed Blob；md/code/csv/html/text 走文本解码；office/unknown 占位） */
const PREVIEW_EXT_KIND: Record<string, PreviewKind> = {
  apng: 'image', bmp: 'image', gif: 'image', ico: 'image', jpeg: 'image',
  jpg: 'image', png: 'image', svg: 'image', webp: 'image',
  pdf: 'pdf',
  flac: 'audio', m4a: 'audio', mp3: 'audio', ogg: 'audio', wav: 'audio',
  m4v: 'video', mov: 'video', mp4: 'video', webm: 'video',
  markdown: 'md', md: 'md',
  c: 'code', cc: 'code', cpp: 'code', cs: 'code', css: 'code', go: 'code',
  h: 'code', hpp: 'code', ini: 'code', java: 'code', js: 'code', json: 'code',
  jsx: 'code', less: 'code', mjs: 'code', php: 'code', ps1: 'code', py: 'code',
  rb: 'code', rs: 'code', scss: 'code', sh: 'code', sql: 'code', toml: 'code',
  ts: 'code', tsx: 'code', yaml: 'code', yml: 'code',
  csv: 'csv', tsv: 'csv',
  htm: 'html', html: 'html',
  conf: 'text', log: 'text', txt: 'text', xml: 'text',
  doc: 'office', docx: 'office', ppt: 'office', pptx: 'office',
  xls: 'office', xlsx: 'office',
};

/** 二进制媒体扩展名 → MIME（喂 <img>/<iframe>/<audio>/<video> 的 typed Blob） */
const PREVIEW_MIME: Record<string, string> = {
  apng: 'image/apng', bmp: 'image/bmp', gif: 'image/gif', ico: 'image/x-icon',
  jpeg: 'image/jpeg', jpg: 'image/jpeg', png: 'image/png', svg: 'image/svg+xml',
  webp: 'image/webp',
  pdf: 'application/pdf',
  flac: 'audio/flac', m4a: 'audio/mp4', mp3: 'audio/mpeg', ogg: 'audio/ogg',
  wav: 'audio/wav',
  m4v: 'video/x-m4v', mov: 'video/quicktime', mp4: 'video/mp4', webm: 'video/webm',
};

/** 取扩展名（小写、不含点，无扩展名返回空串） */
function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i === -1 || i === name.length - 1 ? '' : name.slice(i + 1).toLowerCase();
}

function previewKind(name: string): PreviewKind {
  return PREVIEW_EXT_KIND[extOf(name)] || 'unknown';
}

/** 代码类文本包成带语言标注的 ``` 围栏，交给 MarkdownRenderer 高亮 */
function codeMarkdown(text: string, ext: string): string {
  return `\`\`\`${ext}\n${text}\n\`\`\``;
}

/** 对话条（前端渲染模型）：assistant 消息聚合思考流/工具轨迹/正文 */
interface ChatMsg {
  role: 'assistant' | 'user';
  content: string;
  /** 思考流（thinking.delta 累积，折叠展示） */
  thinking?: string;
  /** 工具调用轨迹（tool.started/completed 逐条追加） */
  toolCalls?: api.ToolCallTrace[];
  /** 本轮仍在流式（渲染光标，终态帧清掉） */
  running?: boolean;
  /** 失败/中断提示（终态帧或历史行带出） */
  error?: string;
  /** AskUser 结构化待答问题（turn.interrupted 帧带出，渲染成可填写表单） */
  pending?: api.PendingQuestion[];
  /** 表单作答草稿：question → { selected: 选项 label 数组, other: 自由文本 } */
  answers?: Record<string, { other: string; selected: string[] }>;
  /** 该挂起表单是否已提交（提交后收起、防重复提交） */
  answered?: boolean;
  /** 时间线片段（assistant 按到达顺序渲染；历史行由落库字段合成） */
  segments?: Segment[];
  /** 本轮 turnId：多会话并发时按会话定位在跑的轮（中断/关流用） */
  turnId?: string;
  /** 本轮用户消息携带的附件引用（user 气泡展示 + 历史回显） */
  attachments?: api.HarnessAttachment[];
  /** 本轮最终输出物（present_output 登记；文件卡片 + 自动预览，assistant 气泡展示） */
  artifacts?: api.HarnessArtifact[];
}

/** 已挂载条目 */
interface MountedItem {
  key: string;
  name: string;
  source: 'kb' | 'mcp' | 'skill' | 'tool';
}

/** 上传任务条目：沙箱工作区面板与对话框附件共用（逐个顺序上传、各自进度） */
interface UploadEntry {
  uid: string;
  name: string;
  size: number;
  /** 上传进度百分比 0-100 */
  percent: number;
  status: 'done' | 'error' | 'uploading';
  /** 上传成功后的沙箱工作区绝对路径（附件发送时用） */
  path?: string;
}

export default defineComponent({
  name: 'HernesExplorerPage',
  components: {
    ArrowLeftOutlined,
    BulbOutlined,
    CheckCircleOutlined,
    CloseOutlined,
    CloudDownloadOutlined,
    CloudUploadOutlined,
    DeleteOutlined,
    DoubleLeftOutlined,
    DoubleRightOutlined,
    FileTextFilled,
    FolderFilled,
    LoadingOutlined,
    MarkdownRenderer,
    MoreOutlined,
    PaperClipOutlined,
    PlusOutlined,
    ReloadOutlined,
    RobotOutlined,
    SendOutlined,
  },
  setup() {
    const accessStore = useAccessStore();

    // ==================== 会话（harness） ====================
    const sessions = ref<api.HarnessSession[]>([]);
    const sessionId = ref(0);
    const sessionLoading = ref(false);
    const modelOptions = ref<api.HarnessModelOption[]>([]);
    /** 当前选用的模型 id（未建会话时是草稿值，每次发消息随请求传） */
    const modelId = ref(0);
    // 后端只返回本人已授权的模型，可选即能用，直接以 model id 作选项值
    const modelSelectOptions = computed(() =>
      modelOptions.value.map((m) => ({
        label: m.name,
        value: m.id,
      })),
    );

    // ==================== 对话框 ====================
    /**
     * 每个会话一份独立的消息缓冲：切会话不清空，后台在跑的 SSE 继续写自己那一份，
     * 所以"离开再回来"实时片段不丢（Redis 频道不回放，只有持续订阅的那条流知道全貌）。
     */
    const sessionStore = reactive<Record<number, ChatMsg[]>>({});
    /** 已拉过历史的会话：再切回去用缓冲、不重载（重载会把实时片段清掉，见 loadMessages） */
    const loadedSessions = new Set<number>();
    /** 会话最近一轮的模型/挂载快照：切换时回显 composer，不重新拉库 */
    const composerMeta = new Map<
      number,
      { modelId?: number; mounts?: Partial<api.HarnessMounts> }
    >();
    /** turnId → 该轮订阅：一个在跑的轮各占一条流，与"当前在看哪个会话"无关 */
    const turnStreams = new Map<
      string,
      { es: EventSource; sessionId: number }
    >();
    /** 取（必要时建）某会话的消息缓冲：所有读写都经这里，保证命中响应式代理 */
    function bufFor(sid: number): ChatMsg[] {
      if (!sessionStore[sid]) sessionStore[sid] = [];
      return sessionStore[sid];
    }
    /** 该会话是否还有活动订阅（有则在往缓冲实时写）：有就绝不重载，否则会清空正被写的 target */
    function hasLiveStream(sid: number): boolean {
      for (const st of turnStreams.values()) {
        if (st.sessionId === sid) return true;
      }
      return false;
    }
    const inputValue = ref('');
    const sending = ref(false);
    const mountedItems = ref<MountedItem[]>([]);
    /** 展示的消息 = 当前会话那一份缓冲（切 sessionId 即换一份，不动别会话） */
    const messages = computed<ChatMsg[]>(
      () => sessionStore[sessionId.value] ?? [],
    );
    /** 当前会话是否有在跑的轮（决定 composer 是"发送"还是"中断"）：只看这一份缓冲 */
    const streaming = computed(() => messages.value.some((m) => m.running));

    // 选择弹窗（四口候选：kb / mcp / tool / skill）
    const pickerVisible = ref(false);
    const pickerType = ref<'kb' | 'mcp' | 'skill' | 'tool'>('skill');
    const pickerLoading = ref(false);
    const skillOptions = ref<api.HarnessOptionItem[]>([]);
    const mcpOptions = ref<api.HarnessOptionItem[]>([]);
    const toolOptions = ref<api.HarnessOptionItem[]>([]);
    const kbOptions = ref<api.HarnessOptionItem[]>([]);
    const pickerSelected = ref<MountedItem[]>([]);

    // ==================== 沙箱 ====================
    const sandboxLoading = ref(false);
    // 工作区绝对根：与后端 WORKSPACE_ROOT 对齐；前端所有路径都以此开头，避免“无 path”传参。
    const WORKSPACE_ROOT = '/workspace';
    const currentPath = ref(WORKSPACE_ROOT);
    const sandboxItems = ref<api.SandboxItem[]>([]);
    const workspace = ref('');
    const uploading = ref(false);
    // 内嵌预览状态（功能1）：点工作区文件/输出物卡片后在右侧面板内渲染，不再弹窗
    // PREVIEW_TEXT_MAX：文本类本地显示上限（后端下载口不截断，这里只防超大文本卡渲染）
    const PREVIEW_TEXT_MAX = 2_000_000;
    const preview = reactive({
      active: false,
      name: '',
      path: '',
      size: 0,
      ext: '',
      kind: '' as PreviewKind | '',
      blobUrl: '',
      text: '',
      truncated: false,
      loading: false,
      err: '',
    });
    // ==================== 文件上传（多文件、逐个顺序、各自进度）====================
    /** 单文件上限 200MB（与后端 UPLOAD_MAX 对齐） */
    const MAX_UPLOAD_MB = 200;
    /** 对话框附件落工作区的子目录（保持工作区根整洁；后端 save_file 会 mkdir -p） */
    const ATTACHMENT_DIR = `${WORKSPACE_ROOT}/attachments`;
    /** 沙箱工作区面板的上传任务列表（带进度，完成后短暂驻留） */
    const sandboxUploads = ref<UploadEntry[]>([]);
    /** 对话框待发送的附件列表（发送后清空） */
    const chatAttachments = ref<UploadEntry[]>([]);
    let uploadSeq = 0;
    function nextUploadUid(): string {
      uploadSeq += 1;
      return `up-${Date.now()}-${uploadSeq}`;
    }
    /** 沙箱工作区是否收起：true 时隐藏右栏、对话框横向铺满 */
    const sandboxCollapsed = ref(false);
    function toggleSandbox() {
      sandboxCollapsed.value = !sandboxCollapsed.value;
    }

    // ==================== 初始化 ====================
    onMounted(() => {
      loadSessions(true);
      loadModels();
      loadSandboxFiles();
    });
    // 离开页面即关所有 SSE（组件卸载后回调再写各会话缓冲已无意义）
    onUnmounted(() => closeAllStreams());

    // ==================== 会话管理 ====================

    async function loadSessions(autoSelect = false) {
      sessionLoading.value = true;
      try {
        const res = await api.HarnessSessionPage({ current: 1, size: 30 });
        sessions.value = res.records || [];
        const first = sessions.value[0];
        if (autoSelect && first && !sessionId.value) {
          await switchSession(first);
        }
      } catch {
        sessions.value = [];
      } finally {
        sessionLoading.value = false;
      }
    }

    async function switchSession(s: api.HarnessSession) {
      sessionId.value = s.id;
      // 已加载过、或还有活动流的会话直接复用缓冲（后台流一直在往里写），不重载：
      // 重载会 buf.length=0 清空正被实时写入的 target，而 RUNNING 轮 Redis 不回放
      // → 旧增量丢、新帧又进不了重建出的空气泡（永远“AI 正在思考…”）
      if (!loadedSessions.has(s.id) && !hasLiveStream(s.id)) await loadMessages(s.id);
      // 先拉齐候选再匹配名称（挂载回显要按 id 找 name）
      await ensurePickerOptions();
      // 模型与挂载不绑会话：按该会话最近一轮回显 composer；无历史则清空草稿
      const meta = composerMeta.get(s.id);
      if (meta) {
        if (meta.modelId) modelId.value = meta.modelId;
        mountedItems.value = mountsToItems(meta.mounts || {});
      } else {
        mountedItems.value = [];
      }
      scrollToBottom();
    }

    /** 四口挂载候选拉齐（挂载回显要按 id 匹配名称；use 口径=自建∪授权） */
    async function ensurePickerOptions() {
      const jobs: Promise<unknown>[] = [];
      if (skillOptions.value.length === 0) jobs.push(loadOptions('skill'));
      if (mcpOptions.value.length === 0) jobs.push(loadOptions('mcp'));
      if (toolOptions.value.length === 0) jobs.push(loadOptions('tool'));
      if (kbOptions.value.length === 0) jobs.push(loadOptions('kb'));
      await Promise.all(jobs);
    }

    /** 拉一类挂载候选（/api/harness/options/{kind}，失败降级为空） */
    async function loadOptions(kind: 'kb' | 'mcp' | 'skill' | 'tool') {
      try {
        const res = await api.HarnessOptions(kind);
        const items = res.items || [];
        if (kind === 'skill') skillOptions.value = items;
        else if (kind === 'mcp') mcpOptions.value = items;
        else if (kind === 'tool') toolOptions.value = items;
        else kbOptions.value = items;
      } catch {
        if (kind === 'skill') skillOptions.value = [];
        else if (kind === 'mcp') mcpOptions.value = [];
        else if (kind === 'tool') toolOptions.value = [];
        else kbOptions.value = [];
      }
    }

    /** 会话挂载快照 → 已挂载条目（名称按候选列表匹配，找不到用 #id 占位） */
    function mountsToItems(mounts: Partial<api.HarnessMounts>): MountedItem[] {
      const items: MountedItem[] = [];
      const match = (
        list: api.HarnessOptionItem[],
        id: number,
      ): api.HarnessOptionItem | undefined =>
        list.find((o) => String(o.id) === String(id));
      (mounts.skillIds || []).forEach((id) => {
        items.push({
          key: String(id),
          name: match(skillOptions.value, id)?.name || `技能#${id}`,
          source: 'skill',
        });
      });
      (mounts.mcpIds || []).forEach((id) => {
        items.push({
          key: String(id),
          name: match(mcpOptions.value, id)?.name || `MCP#${id}`,
          source: 'mcp',
        });
      });
      (mounts.toolIds || []).forEach((id) => {
        items.push({
          key: String(id),
          name: match(toolOptions.value, id)?.name || `工具#${id}`,
          source: 'tool',
        });
      });
      (mounts.kbIds || []).forEach((id) => {
        items.push({
          key: String(id),
          name: match(kbOptions.value, id)?.name || `知识库#${id}`,
          source: 'kb',
        });
      });
      return items;
    }

    /** 当前挂载 → 提交用快照（四类各归各位） */
    function currentMounts(): api.HarnessMounts {
      return {
        kbIds: mountedItems.value
          .filter((i) => i.source === 'kb')
          .map((i) => Number(i.key)),
        mcpIds: mountedItems.value
          .filter((i) => i.source === 'mcp')
          .map((i) => Number(i.key)),
        toolIds: mountedItems.value
          .filter((i) => i.source === 'tool')
          .map((i) => Number(i.key)),
        skillIds: mountedItems.value
          .filter((i) => i.source === 'skill')
          .map((i) => Number(i.key)),
      };
    }

    // ==================== AskUser 挂起快照（纯前端持久化）====================
    // askUser 的结构化待答问题只随 SSE turn.interrupted 帧到达、只活在内存缓冲里；
    // 组件重建（刷新 / 离开页面再回来）时缓冲清空、重载历史只剩 question 文本、选项没了。
    // 这里按会话在 sessionStorage 存一份最近挂起快照，重建消息时按 turnId 还原选项表单。
    const ASK_USER_CACHE_KEY = 'harness:askuser';
    type AskUserCache = Record<
      string,
      { pending: api.PendingQuestion[]; turnId: string }
    >;
    function readAskUserCache(): AskUserCache {
      try {
        return JSON.parse(sessionStorage.getItem(ASK_USER_CACHE_KEY) || '{}');
      } catch {
        return {};
      }
    }
    /** 记某会话最近的挂起问题（只留最新一份：表单本就只在末条消息渲染） */
    function saveAskUserCache(
      sid: number,
      turnId: string,
      pending: api.PendingQuestion[],
    ) {
      const cache = readAskUserCache();
      cache[sid] = { turnId, pending };
      try {
        sessionStorage.setItem(ASK_USER_CACHE_KEY, JSON.stringify(cache));
      } catch {
        // 容量满即放弃，不影响本会话实时显示
      }
    }
    /** 作答后清快照，避免刷新又还原出已提交的历史表单 */
    function clearAskUserCache(sid: number) {
      const cache = readAskUserCache();
      if (cache[sid]) {
        delete cache[sid];
        try {
          sessionStorage.setItem(ASK_USER_CACHE_KEY, JSON.stringify(cache));
        } catch {
          // 忽略写失败
        }
      }
    }

    async function loadMessages(sid: number): Promise<api.HarnessMessage[]> {
      try {
        const rows = (await api.HarnessSessionMessages(sid)) || [];
        const buf = bufFor(sid);
        buf.length = 0;
        // 本会话若有挂起快照，按 turnId 命中还原选项表单（切走/刷新重建后不丢）
        const askCache = readAskUserCache()[sid];
        for (const row of rows) {
          if (row.role === 'USER') {
            buf.push({
              role: 'user',
              content: row.content,
              attachments: row.attachments,
            });
            continue;
          }
          // 历史轮：落库正文/思考/轨迹合成时间线片段后渲染
          const msg: ChatMsg = {
            role: 'assistant',
            content: row.content,
            segments: legacyToSegments(row),
            turnId: row.turnId,
            // 历史回显只出文件卡片，不自动预览（不抓用户当前看到的面板）
            artifacts: row.artifacts,
          };
          if (row.status === 'RUNNING') {
            // 刷新撞上执行中：接回实时流续渲染（Redis 频道不回放，丢的只是刷新前帧）
            msg.running = true;
            buf.push(msg);
            subscribeTurn(sid, row.turnId, msg);
          } else {
            if (row.status === 'FAILED') msg.error = row.error || '执行失败';
            if (row.status === 'INTERRUPTED') {
              msg.error = row.error || '本轮已中断';
              // AskUser 挂起：按缓存 turnId 命中 → 还原可点选项表单（与实时流同口径）
              if (
                askCache &&
                askCache.turnId === row.turnId &&
                askCache.pending.length > 0
              ) {
                msg.pending = askCache.pending;
                const draft: Record<
                  string,
                  { other: string; selected: string[] }
                > = {};
                for (const q of askCache.pending) {
                  draft[q.question] = { selected: [], other: '' };
                }
                msg.answers = draft;
                msg.answered = false;
              }
            }
            buf.push(msg);
          }
        }
        loadedSessions.add(sid);
        // 记住该会话最近一轮的模型/挂载，切回来时回显 composer（不重拉库）
        const last =
          [...rows].reverse().find((r) => r.role === 'ASSISTANT') || rows.at(-1);
        if (last) {
          composerMeta.set(sid, { modelId: last.modelId, mounts: last.mounts });
        }
        return rows;
      } catch {
        message.error('历史消息加载失败');
        return [];
      }
    }

    function newSession() {
      // 只重置"新建"草稿会话(0)的视图，不动其它会话的缓冲与其后台流
      sessionId.value = 0;
      sessionStore[0] = [];
      loadedSessions.delete(0);
      composerMeta.delete(0);
      mountedItems.value = [];
      inputValue.value = '';
    }

    async function deleteSession(id: number) {
      if (!id) return;
      try {
        await api.HarnessSessionDelete(id);
        // 关掉该会话所有在跑的流并清缓冲（不关后台流会继续占连接）
        for (const [turnId, st] of [...turnStreams]) {
          if (st.sessionId === id) closeTurnStream(turnId);
        }
        delete sessionStore[id];
        loadedSessions.delete(id);
        composerMeta.delete(id);
        // 删的是当前会话才回到新建草稿；删侧栏里的历史项不打断当前对话
        if (sessionId.value === id) newSession();
        await loadSessions(true);
      } catch {
        message.error('会话删除失败');
      }
    }

    // ==================== 模型 ====================

    async function loadModels() {
      try {
        const res = await api.HarnessModels();
        modelOptions.value = res.items || [];
      } catch {
        modelOptions.value = [];
      }
    }

    /** 切模型：纯前端 composer 状态，随每次发消息一并传给后端（不绑会话，可随时切换） */
    function changeModel(value: number) {
      modelId.value = value;
    }

    // ==================== 对话（ARQ 投递 + SSE 订阅） ====================

    async function handleSend() {
      const text = inputValue.value.trim();
      if (streaming.value) {
        interruptTurn();
        return;
      }
      if (sending.value) return;
      if (chatAttachments.value.some((a) => a.status === 'uploading')) {
        message.warning('还有文件在上传中，请稍候再发送');
        return;
      }
      const atts = readyAttachments();
      if (!text && atts.length === 0) return;
      if (!modelId.value) {
        message.warning('请先在下方选择一个已授权的模型');
        return;
      }
      const messageText = text || '请分析我上传的附件';
      const displayText = text || `已上传 ${atts.length} 个附件`;
      inputValue.value = '';
      chatAttachments.value = []; // 发送即清草稿附件
      await postTurn(messageText, displayText, atts);
    }

    /** 发一轮：messageText 给后端，displayText 给用户气泡（AskUser 走 JSON 时用可读文本回显），attachments 随消息传 */
    async function postTurn(
      messageText: string,
      displayText: string,
      attachments?: api.HarnessAttachment[],
    ) {
      // 首条消息自动建会话：只落标题，模型与挂载随每次发消息传
      if (!sessionId.value) {
        try {
          const s = await api.HarnessSessionCreate({
            title: displayText.slice(0, 24) || '新探索',
          });
          sessionId.value = s.id;
        } catch (error: any) {
          message.error(error?.message || '会话创建失败');
          return;
        }
      }

      const sid = sessionId.value;
      // 本轮由本屏发起、实时写入本会话缓冲：标为已加载，切走再回来不触发 DB 重载
      loadedSessions.add(sid);
      bufFor(sid).push({
        role: 'user',
        content: displayText,
        attachments: attachments && attachments.length ? attachments : undefined,
      });
      sending.value = true;
      scrollToBottom();
      try {
        const res = await api.HarnessChat({
          sessionId: sid,
          message: messageText,
          modelId: modelId.value,
          mounts: currentMounts(),
          attachments: attachments && attachments.length ? attachments : undefined,
        });
        void loadSessions(); // 刷新侧栏排序（updateTime 刚变过），不重选当前会话
        subscribeTurn(sid, res.turnId);
      } catch (error: any) {
        bufFor(sid).push({
          role: 'assistant',
          content:
            error?.message || '请求失败，深度探索服务暂不可用，请稍后重试。',
        });
        scrollToBottom();
      } finally {
        sending.value = false;
      }
    }

    /** AskUser 单选/多选点选：多选切换该 label，单选整题替换为该 label */
    function toggleAskOption(
      msg: ChatMsg,
      q: api.PendingQuestion,
      label: string,
    ) {
      const draft = msg.answers || {};
      const cur = draft[q.question] || { selected: [], other: '' };
      cur.selected = q.multiSelect
        ? cur.selected.includes(label)
          ? cur.selected.filter((x) => x !== label)
          : [...cur.selected, label]
        : [label];
      draft[q.question] = cur;
      msg.answers = draft;
    }

    /** 提交 AskUser 表单：拼 answers JSON 走 message 字段，气泡显示可读文本 */
    async function submitAskUser(msg: ChatMsg) {
      if (msg.answered || sending.value) return;
      if (!modelId.value) {
        message.warning('请先在下方选择一个已授权的模型');
        return;
      }
      const pending = msg.pending || [];
      const draft = msg.answers || {};
      const answers = pending.map((q) => {
        const a = draft[q.question] || { selected: [], other: '' };
        return {
          question: q.question,
          selected: a.selected || [],
          other: (a.other || '').trim() || null,
        };
      });
      const display = answers
        .map((a) => {
          const picked = a.selected.join('、');
          const val =
            [picked, a.other || ''].filter(Boolean).join(' / ') || '（未作答）';
          return `${a.question}：${val}`;
        })
        .join('\n');
      msg.answered = true;
      clearAskUserCache(sessionId.value);
      await postTurn(JSON.stringify({ answers }), display);
    }

    /** AskUser “其他”自由文本写入草稿（避开 v-model 绑可能未定义路径） */
    function setAskOther(msg: ChatMsg, q: api.PendingQuestion, val: string) {
      const draft = msg.answers || {};
      const cur = draft[q.question] || { selected: [], other: '' };
      cur.other = val;
      draft[q.question] = cur;
      msg.answers = draft;
    }

    // ==================== 时间线片段（segments）====================
    let segSeq = 0;
    function nextSegId(): string {
      segSeq += 1;
      return `seg-${segSeq}`;
    }

    /** 收尾仍在「进行中」的思考段（遇新片段/终态时调用，停掉它的转圈） */
    function settleThinking(msg: ChatMsg) {
      for (const s of msg.segments || []) {
        if (s.kind === 'thinking') s.active = false;
      }
    }

    /** 思考增量：并入末尾思考段，否则收尾其它后新开一段 */
    function appendThinking(msg: ChatMsg, delta: string) {
      if (!msg.segments) msg.segments = [];
      const last = msg.segments[msg.segments.length - 1];
      if (last && last.kind === 'thinking') {
        last.text += delta;
        last.active = true;
      } else {
        settleThinking(msg);
        msg.segments.push({
          kind: 'thinking',
          id: nextSegId(),
          text: delta,
          active: true,
        });
      }
    }

    /** 正文增量：并入末尾文本段，否则收尾思考后新开一段 */
    function appendText(msg: ChatMsg, delta: string) {
      if (!msg.segments) msg.segments = [];
      const last = msg.segments[msg.segments.length - 1];
      if (last && last.kind === 'text') {
        last.text += delta;
      } else {
        settleThinking(msg);
        msg.segments.push({ kind: 'text', id: nextSegId(), text: delta });
      }
    }

    /** 工具开始：收尾思考，新开一个未完成（转圈）的工具段，id 用 toolCallId */
    function startTool(msg: ChatMsg, tool: string, toolCallId?: string) {
      if (!msg.segments) msg.segments = [];
      settleThinking(msg);
      msg.segments.push({
        kind: 'tool',
        id: toolCallId || nextSegId(),
        tool,
        arguments: '',
        done: false,
      });
    }

    /** 工具完成：优先按 toolCallId 命中，退回同名最近未完成段补入参置完成 */
    function finishTool(
      msg: ChatMsg,
      tool: string,
      argumentsText: string,
      toolCallId?: string,
      purpose?: string,
    ) {
      const segs = msg.segments || [];
      let target = toolCallId
        ? segs.find((s) => s.kind === 'tool' && s.id === toolCallId)
        : undefined;
      if (!target) {
        for (let i = segs.length - 1; i >= 0; i -= 1) {
          const s = segs[i];
          if (s && s.kind === 'tool' && s.tool === tool && !s.done) {
            target = s;
            break;
          }
        }
      }
      if (target && target.kind === 'tool') {
        target.arguments = argumentsText;
        // 本轮没有 purpose 不要把已有的抹掉（实时流下 purpose 只会随 completed 帧到）
        if (purpose) target.purpose = purpose;
        target.done = true;
      } else {
        segs.push({
          kind: 'tool',
          id: toolCallId || nextSegId(),
          tool,
          arguments: argumentsText,
          done: true,
          purpose,
        });
      }
    }

    /** 由落库字段合成时间线（历史行近似时序：思考 → 各工具 → 正文） */
    function legacyToSegments(row: api.HarnessMessage): Segment[] {
      const segs: Segment[] = [];
      if (row.thinking) {
        segs.push({
          kind: 'thinking',
          id: nextSegId(),
          text: row.thinking,
          active: false,
        });
      }
      for (const call of row.toolCalls || []) {
        segs.push({
          kind: 'tool',
          id: call.toolCallId || nextSegId(),
          tool: call.tool,
          arguments: call.arguments || '',
          done: true,
          purpose: call.purpose,
        });
      }
      if (row.content) {
        segs.push({ kind: 'text', id: nextSegId(), text: row.content });
      }
      return segs;
    }

    /** 订阅本轮事件流（传 msg = 刷新恢复的执行中历史行，复用同一条气泡） */
    function subscribeTurn(sid: number, turnId: string, msg?: ChatMsg) {
      // 该轮已有活动订阅：不重复连（EventSource 重连会回放已收帧、双写会乱时序）
      if (turnStreams.has(turnId)) return;
      const buf = bufFor(sid);
      // reactive 包装：SSE 回调里改字段才能驱动视图更新（与已入列的普通对象共享底层，
      // 数组里的同源代理由渲染侧惰性生成，依赖能被同一代理触发）
      const target: ChatMsg = reactive(
        msg || { role: 'assistant', content: '', segments: [], turnId },
      );
      target.turnId = turnId;
      if (!msg) {
        target.running = true;
        buf.push(target);
      }

      const es = new EventSource(api.HarnessSubscribeUrl(turnId));
      turnStreams.set(turnId, { es, sessionId: sid });
      es.onmessage = (ev: MessageEvent) => {
        let frame: api.HarnessFrame;
        try {
          frame = JSON.parse(ev.data);
        } catch {
          return;
        }
        applyFrame(target, frame, turnId, sid);
        // AskUser 挂起带结构化问题 → 落一份会话快照，切走/刷新重建历史时据此还原选项
        if (
          frame.type === 'turn.interrupted' &&
          frame.pendingQuestions &&
          frame.pendingQuestions.length > 0
        ) {
          saveAskUserCache(sid, turnId, frame.pendingQuestions);
        }
        // 只有正在看这个会话才跟滚动，后台会话流只更新缓冲不打扰当前视图
        if (sid === sessionId.value) scrollToBottom();
      };
      es.onerror = () => {
        // 终态帧先到时已 closeTurnStream，这里只会因异常断流触发：
        // 关连接防 EventSource 自动重连（重连会重复回放已收帧），结果以刷新后历史为准
        if (target.running) {
          target.running = false;
          target.error = '事件连接已断开，本轮结果稍后刷新查看';
        }
        closeTurnStream(turnId);
      };
      scrollToBottom();
    }

    /** 协议帧 → 消息条增量渲染（帧字段口径见 api.ts 的 HarnessFrame） */
    function applyFrame(
      msg: ChatMsg,
      frame: api.HarnessFrame,
      turnId: string,
      sid: number,
    ) {
      switch (frame.type) {
        case 'text.delta': {
          appendText(msg, frame.content || '');
          break;
        }
        case 'thinking.delta': {
          appendThinking(msg, frame.content || '');
          break;
        }
        case 'tool.started': {
          startTool(msg, frame.tool || '', frame.toolCallId);
          break;
        }
        case 'tool.completed': {
          finishTool(
            msg,
            frame.tool || '',
            frame.arguments || '',
            frame.toolCallId,
            frame.purpose,
          );
          break;
        }
        case 'turn.artifacts': {
          // 本会话该轮首个 artifacts 到达且正在看这个会话：展开工作区、自动预览首个
          //（后续累积帧只更卡片、不抢占用户手动切到的预览）
          const list = frame.artifacts || [];
          const had = (msg.artifacts || []).length > 0;
          msg.artifacts = list;
          if (!had && list.length > 0 && sessionId.value === sid) {
            sandboxCollapsed.value = false;
            const first = list[0];
            if (first) void openPreview(first.path, first.name, 0);
          }
          break;
        }
        case 'turn.completed': {
          // 终态：收尾仍在进行的思考段；正文已由 text.delta 落到时间线，
          // 不再用整段 content 覆盖（会打乱与工具/思考交错的时序）
          settleThinking(msg);
          msg.running = false;
          closeTurnStream(turnId);
          break;
        }
        case 'turn.interrupted': {
          settleThinking(msg);
          msg.running = false;
          // HITL 挂起时优先显示智能体的问题（reason 是「等待答复」占位，
          // 回放路径下 question 已拼进 reason）
          msg.error = frame.question || frame.reason || '本轮已中断';
          // AskUser 带结构化问题 → 初始化作答草稿，模板据此渲染选项表单
          if (frame.pendingQuestions && frame.pendingQuestions.length > 0) {
            msg.pending = frame.pendingQuestions;
            const draft: Record<string, { other: string; selected: string[] }> =
              {};
            for (const q of frame.pendingQuestions) {
              draft[q.question] = { selected: [], other: '' };
            }
            msg.answers = draft;
            msg.answered = false;
          }
          closeTurnStream(turnId);
          break;
        }
        case 'turn.failed': {
          settleThinking(msg);
          msg.running = false;
          msg.error = frame.error || '执行失败';
          closeTurnStream(turnId);
          break;
        }
        default:
          break; // turn.started 不渲染（气泡已建）
      }
    }

    /** 关闭某一轮的订阅（终态/异常/切走只关这一条，别会话的后台流不受影响） */
    function closeTurnStream(turnId?: string) {
      if (!turnId) return;
      const st = turnStreams.get(turnId);
      if (st) {
        st.es.close();
        turnStreams.delete(turnId);
      }
    }

    /** 关所有在跑的流（离开页面用：组件卸载后回调再写各会话缓冲已无意义） */
    function closeAllStreams() {
      for (const turnId of [...turnStreams.keys()]) closeTurnStream(turnId);
    }

    async function interruptTurn() {
      // 中断当前会话在跑的那一轮（别的会话后台流不受影响）
      const sid = sessionId.value;
      const hit = [...turnStreams].find(([, v]) => v.sessionId === sid);
      if (!hit) return;
      try {
        await api.HarnessInterrupt(hit[0]);
        message.info('已请求中断，稍候生效');
      } catch {
        message.error('中断请求失败');
      }
    }

    function scrollToBottom() {
      nextTick(() => {
        const el = document.querySelector('.hermes-hd-list');
        if (el) el.scrollTop = el.scrollHeight;
      });
    }

    function removeMounted(k: string) {
      mountedItems.value = mountedItems.value.filter((i) => i.key !== k);
    }

    // ==================== 四类能力挂载 picker ====================
    async function openPicker(type: 'kb' | 'mcp' | 'skill' | 'tool') {
      pickerType.value = type;
      pickerSelected.value = mountedItems.value.filter(
        (i) => i.source === type,
      );
      pickerVisible.value = true;
      pickerLoading.value = true;
      try {
        // 每次打开都刷新：use 授权可能刚被授予/收回，缓存的旧口径会误导选择
        await loadOptions(type);
      } finally {
        pickerLoading.value = false;
      }
    }

    function confirmPicker() {
      mountedItems.value = [
        ...mountedItems.value.filter((i) => i.source !== pickerType.value),
        ...pickerSelected.value,
      ];
      pickerVisible.value = false;
    }

    function togglePick(item: MountedItem) {
      const idx = pickerSelected.value.findIndex((i) => i.key === item.key);
      if (idx === -1) {
        pickerSelected.value.push(item);
      } else {
        pickerSelected.value.splice(idx, 1);
      }
    }

    // ==================== 沙箱文件逻辑 ====================
    async function loadSandboxFiles(dir = currentPath.value) {
      // 任何目录导航（返回/进入/面包屑/刷新）都先退出内嵌预览，回到文件列表
      closePreview();
      sandboxLoading.value = true;
      try {
        const res = await api.SandboxFiles(dir);
        currentPath.value = res.current_path || WORKSPACE_ROOT;
        workspace.value = res.workspace || '';
        sandboxItems.value = res.items || [];
      } catch {
        message.error('沙箱工作区加载失败');
      } finally {
        sandboxLoading.value = false;
      }
    }

    /** 面包屑非根段：剥掉首段 workspace（根由「工作区」面包屑单独代表），逐段带绝对路径 */
    const subSegments = computed(() => {
      const parts = currentPath.value.split('/').filter(Boolean);
      const body = parts[0] === 'workspace' ? parts.slice(1) : parts;
      return body.map((part, i) => ({
        part,
        path: `${WORKSPACE_ROOT}/${body.slice(0, i + 1).join('/')}`,
      }));
    });

    /** 已在工作区根时禁用回退 */
    const atWorkspaceRoot = computed(
      () => currentPath.value === WORKSPACE_ROOT,
    );

    function goBack() {
      if (atWorkspaceRoot.value) return;
      const parts = currentPath.value.split('/').filter(Boolean);
      parts.pop();
      loadSandboxFiles(`/${parts.join('/')}`);
    }

    function enterDir(name: string) {
      loadSandboxFiles(`${currentPath.value || WORKSPACE_ROOT}/${name}`);
    }

    function openFile(item: api.SandboxItem) {
      const path = `${currentPath.value || WORKSPACE_ROOT}/${item.name}`;
      void openPreview(path, item.name, item.size);
    }

    function formatSize(size: number): string {
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
      return `${(size / 1024 / 1024).toFixed(1)} MB`;
    }

    /** 下载（文件或文件夹），fetch blob 带 token */
    async function downloadItem(item: api.SandboxItem) {
      const path = `${currentPath.value || WORKSPACE_ROOT}/${item.name}`;
      try {
        const traceId = getTraceId();
        const resp = await fetch(api.SandboxDownloadUrl(path), {
          headers: {
            Authorization: `Bearer ${accessStore.accessToken}`,
            ...(traceId ? { 'X-Trace-Id': traceId } : {}),
          },
        });
        if (!resp.ok) {
          message.error('下载失败');
          return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = item.type === 'dir' ? `${item.name}.zip` : item.name;
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        message.error('下载失败，请检查网络');
      }
    }

    /** 内嵌预览：按扩展名归类渲染（功能1，零后端改动，复用下载口原始字节） */
    async function openPreview(path: string, name: string, size = 0) {
      closePreview();
      preview.active = true;
      preview.loading = true;
      preview.name = name;
      preview.path = path;
      preview.size = size;
      preview.ext = extOf(name);
      const kind = previewKind(name);
      preview.kind = kind;
      // office / 未知格式：不读内容（可能是大二进制），直接给占位 + 下载
      if (kind === 'office' || kind === 'unknown') {
        preview.loading = false;
        return;
      }
      try {
        const blob = await api.SandboxFileBlob(
          path,
          accessStore.accessToken,
          getTraceId(),
        );
        if (
          kind === 'image' ||
          kind === 'pdf' ||
          kind === 'audio' ||
          kind === 'video'
        ) {
          // 按扩展名重新包 typed Blob（下载口 Content-Type 可能是 octet-stream，不重包渲染不了）
          const mime =
            PREVIEW_MIME[preview.ext] ||
            blob.type ||
            'application/octet-stream';
          const buf = await blob.arrayBuffer();
          preview.blobUrl = URL.createObjectURL(new Blob([buf], { type: mime }));
        } else {
          // 文本类直接解码字节，绕开后端 read_text_file 的 200KB 截断；超上限再本地截断
          let text = await blob.text();
          if (text.length > PREVIEW_TEXT_MAX) {
            text = text.slice(0, PREVIEW_TEXT_MAX);
            preview.truncated = true;
          }
          preview.text = text;
        }
      } catch (error: any) {
        preview.err = error?.message || '预览失败，请下载后查看';
      } finally {
        preview.loading = false;
      }
    }

    /** 关闭预览并释放 object URL（返回列表 / 切目录 / 换文件时调用） */
    function closePreview() {
      if (preview.blobUrl) URL.revokeObjectURL(preview.blobUrl);
      preview.active = false;
      preview.name = '';
      preview.path = '';
      preview.size = 0;
      preview.ext = '';
      preview.kind = '';
      preview.blobUrl = '';
      preview.text = '';
      preview.truncated = false;
      preview.loading = false;
      preview.err = '';
    }

    /** 按绝对路径下载（预览头 / 输出物卡片用；同 downloadItem 的 blob 下载，带 token） */
    async function downloadByPath(path: string, name: string) {
      try {
        const traceId = getTraceId();
        const resp = await fetch(api.SandboxDownloadUrl(path), {
          headers: {
            Authorization: `Bearer ${accessStore.accessToken}`,
            ...(traceId ? { 'X-Trace-Id': traceId } : {}),
          },
        });
        if (!resp.ok) {
          message.error('下载失败');
          return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        message.error('下载失败，请检查网络');
      }
    }

    /** 上传单个文件：写 entry 的 percent/status/path（entry 必须是回取的响应式代理） */
    async function uploadOneFile(
      entry: UploadEntry,
      dir: string,
      file: File,
    ): Promise<boolean> {
      entry.status = 'uploading';
      entry.percent = 0;
      try {
        const res = await api.SandboxUpload(dir, file, (p) => {
          entry.percent = p;
        });
        entry.name = res.name || file.name;
        entry.path = res.path || `${dir}/${file.name}`;
        entry.percent = 100;
        entry.status = 'done';
        return true;
      } catch {
        entry.status = 'error';
        return false;
      }
    }

    /** 建条目并 push 后回取响应式代理（直接改原始对象不触发视图更新） */
    function pushUploadEntry(
      list: typeof sandboxUploads,
      file: File,
    ): UploadEntry {
      list.value.push({
        uid: nextUploadUid(),
        name: file.name,
        size: file.size,
        percent: 0,
        status: 'uploading',
      });
      return list.value[list.value.length - 1] as UploadEntry;
    }

    /** 过滤掉超过 200MB 的文件并提示；返回可上传的批次 */
    function filterBySize(files: File[]): File[] {
      const max = MAX_UPLOAD_MB * 1024 * 1024;
      const ok = files.filter((f) => f.size <= max);
      const over = files.filter((f) => f.size > max);
      if (over.length > 0) {
        message.warning(
          `已跳过超过 ${MAX_UPLOAD_MB}MB 的文件：${over
            .map((f) => f.name)
            .join('、')}`,
        );
      }
      return ok;
    }

    /** 沙箱工作区上传：多文件选择，a-upload 对每个文件都回调一次，只在首个启动整批顺序上传 */
    function beforeUpload(file: any, fileList: any[]) {
      if (file === fileList[0]) {
        void runSandboxBatch(filterBySize(fileList as File[]));
      }
      return false; // 阻止 a-upload 默认上传，改由我们手动顺序传
    }

    /** 顺序（不并发）上传一批到当前目录，每条带进度 */
    async function runSandboxBatch(files: File[]) {
      if (files.length === 0) return;
      uploading.value = true;
      let ok = 0;
      for (const f of files) {
        const entry = pushUploadEntry(sandboxUploads, f);
        if (
          await uploadOneFile(
            entry,
            currentPath.value || WORKSPACE_ROOT,
            f,
          )
        ) {
          ok += 1;
        }
      }
      uploading.value = false;
      if (ok > 0) message.success(`${ok} 个文件上传成功`);
      loadSandboxFiles();
      // 完成后短暂驻留：无论成功还是失败，进度条都在 2s 后自动清除（失败已有 toast 提示，不长期占用列表）
      setTimeout(() => {
        sandboxUploads.value = [];
      }, 2000);
    }

    /** 对话框“添加附件”：多文件不限格式，同样只在首个启动整批顺序上传 */
    function onChatAttachPick(file: any, fileList: any[]) {
      if (file === fileList[0]) {
        void runChatAttachBatch(filterBySize(fileList as File[]));
      }
      return false;
    }

    /** 附件顺序上传到工作区附件目录（不并发，各自进度）；失败项 2s 后自动清除，成功项保留到发送 */
    async function runChatAttachBatch(files: File[]) {
      for (const f of files) {
        const entry = pushUploadEntry(chatAttachments, f);
        if (!(await uploadOneFile(entry, ATTACHMENT_DIR, f))) {
          // 失败已有 toast 提示，条目只短暂驻留 2s 便于看到状态即自清（按引用移除，不误删成功项）
          setTimeout(() => removeChatAttachment(entry), 2000);
        }
      }
    }

    /** 发送前移除某个附件（或移除上传失败项） */
    function removeChatAttachment(entry: UploadEntry) {
      const i = chatAttachments.value.indexOf(entry);
      if (i !== -1) chatAttachments.value.splice(i, 1);
    }

    /** 就绪（上传成功）的附件引用：发送时随消息带上 */
    function readyAttachments(): api.HarnessAttachment[] {
      return chatAttachments.value
        .filter((a) => a.status === 'done' && a.path)
        .map((a) => ({ name: a.name, path: a.path as string }));
    }

    // pathBreadcrumbClick 已删：面包屑非根段改由 subSegments 直接递对应的绝对 path 给 loadSandboxFiles。

    return {
      accessStore,
      messages,
      inputValue,
      sending,
      streaming,
      mountedItems,
      toolLabel,
      toolArgsView,
      thinkingHint,
      handleSend,
      toggleAskOption,
      submitAskUser,
      setAskOther,
      removeMounted,
      // 会话 / 模型
      sessions,
      sessionId,
      sessionLoading,
      modelId,
      modelSelectOptions,
      switchSession,
      newSession,
      deleteSession,
      changeModel,
      // 挂载 picker
      openPicker,
      pickerVisible,
      pickerType,
      pickerLoading,
      skillOptions,
      mcpOptions,
      toolOptions,
      kbOptions,
      pickerSelected,
      togglePick,
      confirmPicker,
      sandboxLoading,
      currentPath,
      workspace,
      sandboxItems,
      loadSandboxFiles,
      goBack,
      enterDir,
      openFile,
      formatSize,
      downloadItem,
      beforeUpload,
      uploading,
      preview,
      openPreview,
      closePreview,
      downloadByPath,
      codeMarkdown,
      subSegments,
      atWorkspaceRoot,
      WORKSPACE_ROOT,
      sandboxCollapsed,
      toggleSandbox,
      // 多文件上传 + 对话框附件
      sandboxUploads,
      chatAttachments,
      onChatAttachPick,
      removeChatAttachment,
    };
  },
});
</script>

<template>
  <div class="hermes-page" :class="{ 'is-sandbox-collapsed': sandboxCollapsed }">
    <!-- 左侧：会话列表 -->
    <aside class="hermes-side">
      <a-button block ghost size="small" type="primary" @click="newSession">
        <template #icon><PlusOutlined /></template>
        新建探索
      </a-button>
      <div class="hermes-side-list">
        <div v-if="sessionLoading" class="hermes-side-empty">加载中…</div>
        <template v-else>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="hermes-side-item"
            :class="{ active: s.id === sessionId }"
            @click="switchSession(s)"
          >
            <span class="hermes-side-title" :title="s.title">{{
              s.title
            }}</span>
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <span class="hermes-side-more" @click.stop>
                <MoreOutlined />
              </span>
              <template #overlay>
                <a-menu>
                  <a-menu-item key="delete" danger @click="deleteSession(s.id)">
                    <DeleteOutlined /> 删除对话
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
          <div v-if="sessions.length === 0" class="hermes-side-empty">
            暂无历史会话
          </div>
        </template>
      </div>
    </aside>

    <!-- 中部：深度探索 Hernes 对话框 -->
    <section class="hermes-chat">
      <header class="hermes-chat-header">
        <div class="hermes-logo">
          <RobotOutlined />
        </div>
        <div class="hermes-title">
          <h2>深度探索 Hernes</h2>
          <p>连接云端沙箱运行时，描述你的问题，让 AI 帮你执行</p>
        </div>
        <a-tooltip v-if="sandboxCollapsed" title="展开沙箱工作区">
          <a-button size="small" @click="toggleSandbox">
            <template #icon><DoubleLeftOutlined /></template>
            展开工作区
          </a-button>
        </a-tooltip>
      </header>

      <!-- 已挂载能力（始终可见的清单条；模型/工具每轮可切换，这份 composer 状态随每次发消息传给后端） -->
      <div class="hermes-mounted">
        <span class="hermes-mounted-label">已挂载能力</span>
        <template v-if="mountedItems.length > 0">
          <a-tag
            v-for="item in mountedItems"
            :key="item.key"
            closable
            :color="
              item.source === 'skill'
                ? 'blue'
                : item.source === 'tool'
                  ? 'green'
                  : 'orange'
            "
            @close="removeMounted(item.key)"
          >
            {{ item.name }}
          </a-tag>
        </template>
        <span v-else class="hermes-mounted-empty">
          尚未挂载，点下方按钮添加 技能 / MCP / 工具 / 知识库
        </span>
      </div>

      <!-- 消息列表 -->
      <div class="hermes-hd-list">
        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          class="hermes-msg"
          :class="msg.role === 'user' ? 'is-user' : 'is-ai'"
        >
          <div v-if="msg.role === 'assistant'" class="hermes-avatar ai">
            <RobotOutlined />
          </div>
          <!-- assistant 消息：按到达时间序逐个渲染思考/工具/文本片段（对齐 workbuddy/deepseek 风格） -->
          <div v-if="msg.role === 'assistant'" class="hermes-body">
            <template v-for="seg in msg.segments" :key="seg.id">
              <!-- 思考块：进行中转圈，结束可折叠；默认折叠摘要 -->
              <details
                v-if="seg.kind === 'thinking'"
                class="hermes-seg hermes-thinking"
              >
                <summary class="hermes-seg-summary">
                  <LoadingOutlined v-if="seg.active" spin />
                  <BulbOutlined v-else />
                  <span>{{ seg.active ? '思考中…' : '已完成思考' }}</span>
                  <span v-if="thinkingHint(seg.text)" class="hermes-seg-hint">{{
                    thinkingHint(seg.text)
                  }}</span>
                </summary>
                <pre class="hermes-seg-body">{{ seg.text }}</pre>
              </details>

              <!-- 工具调用块：标题是模型写的用途（不是 Edit/Read 这种技术名），入参折叠 -->
              <details
                v-else-if="seg.kind === 'tool'"
                class="hermes-seg hermes-call"
              >
                <summary class="hermes-seg-summary">
                  <LoadingOutlined v-if="!seg.done && msg.running" spin />
                  <CheckCircleOutlined v-else class="hermes-seg-done" />
                  <span class="hermes-call-title" :title="seg.tool">{{
                    toolLabel(seg)
                  }}</span>
                  <span v-if="seg.tool" class="hermes-call-kind">{{
                    seg.tool
                  }}</span>
                </summary>
                <pre v-if="toolArgsView(seg)" class="hermes-seg-body">{{
                  toolArgsView(seg)
                }}</pre>
              </details>

              <!-- 正文块：完整 markdown 渲染（标题/列表/表格/代码高亮，复用平台 MarkdownRenderer，内部已 sanitize） -->
              <MarkdownRenderer
                v-else-if="seg.kind === 'text'"
                :content="seg.text"
                class="hermes-bubble"
              />
            </template>

            <!-- 本轮在跑但尚无任何可见片段时，兜底一个“正在思考” -->
            <div
              v-if="msg.running && (msg.segments || []).length === 0"
              class="hermes-running"
            >
              <LoadingOutlined spin />
              <span>AI 正在思考…</span>
            </div>
            <div v-if="msg.error" class="hermes-msg-error">
              <a-tag color="warning">{{ msg.error }}</a-tag>
            </div>
            <!-- AskUser 人工审批：把待答问题渲染成可点选的选项表单（仅最新一条、未提交、非流式中） -->
            <div
              v-if="
                msg.pending &&
                msg.pending.length > 0 &&
                !msg.answered &&
                !streaming &&
                idx === messages.length - 1
              "
              class="hermes-askuser"
            >
              <div
                v-for="q in msg.pending"
                :key="q.question"
                class="hermes-askuser-q"
              >
                <div class="hermes-askuser-title">{{ q.question }}</div>
                <div v-if="q.options.length > 0" class="hermes-askuser-opts">
                  <label
                    v-for="opt in q.options"
                    :key="opt.label"
                    class="hermes-askuser-opt"
                    :class="{
                      checked: msg.answers
                        ? msg.answers[q.question].selected.includes(opt.label)
                        : false,
                    }"
                  >
                    <input
                      :type="q.multiSelect ? 'checkbox' : 'radio'"
                      :name="`ask-${idx}-${q.question}`"
                      :checked="
                        msg.answers
                          ? msg.answers[q.question].selected.includes(opt.label)
                          : false
                      "
                      @change="toggleAskOption(msg, q, opt.label)"
                    />
                    <span class="hermes-askuser-opt-label">{{
                      opt.label
                    }}</span>
                    <small
                      v-if="opt.description"
                      class="hermes-askuser-opt-desc"
                    >
                      {{ opt.description }}
                    </small>
                  </label>
                </div>
                <a-textarea
                  :value="msg.answers ? msg.answers[q.question].other : ''"
                  :auto-size="{ minRows: 1, maxRows: 3 }"
                  placeholder="其他（可选，自由补充）"
                  class="hermes-askuser-other"
                  @change="(e: any) => setAskOther(msg, q, e.target.value)"
                />
              </div>
              <a-button
                type="primary"
                size="small"
                :loading="sending"
                @click="submitAskUser(msg)"
              >
                提交答复
              </a-button>
            </div>
            <!-- 最终输出物文件单：present_output 登记的交付文件，点击即在右侧工作区预览 -->
            <div
              v-if="msg.artifacts && msg.artifacts.length > 0"
              class="hermes-artifacts"
            >
              <div class="hermes-artifacts-title">最终输出物</div>
              <div
                v-for="a in msg.artifacts"
                :key="a.path"
                class="hermes-artifact-card"
                @click="openPreview(a.path, a.name, 0)"
              >
                <FileTextFilled class="hermes-artifact-icon" />
                <div class="hermes-artifact-main">
                  <span class="hermes-artifact-name" :title="a.path">{{
                    a.name
                  }}</span>
                  <span v-if="a.description" class="hermes-artifact-desc">{{
                    a.description
                  }}</span>
                </div>
                <a-button
                  type="text"
                  size="small"
                  class="hermes-artifact-dl"
                  @click.stop="downloadByPath(a.path, a.name)"
                >
                  <template #icon><CloudDownloadOutlined /></template>
                </a-button>
              </div>
            </div>
          </div>
          <!-- 用户气泡：原样文本（用户输入不做 md 渲染，保留换行即可）+ 附件小标签 -->
          <div v-else class="hermes-user-wrap">
            <div class="hermes-bubble" style="white-space: pre-wrap">
              {{ msg.content }}
            </div>
            <div
              v-if="msg.attachments && msg.attachments.length > 0"
              class="hermes-msg-attach"
            >
              <a-tag v-for="f in msg.attachments" :key="f.path" color="blue">
                <PaperClipOutlined /> {{ f.name }}
              </a-tag>
            </div>
          </div>
          <div v-if="msg.role === 'user'" class="hermes-avatar user">我</div>
        </div>
        <div v-if="messages.length === 0" class="hermes-empty">
          <RobotOutlined class="hermes-empty-icon" />
          <p>你好，我是 Hernes。可以让我分析数据、处理文件、组织任务…</p>
          <p class="hermes-empty-tip">
            试着说：帮我统计 sample_data.csv 里的访问量趋势
          </p>
        </div>
      </div>

      <!-- 输入区 -->
      <footer class="hermes-input">
        <div class="hermes-input-tools">
          <button
            class="hermes-add-btn is-skill"
            type="button"
            @click="openPicker('skill')"
          >
            <PlusOutlined /> 技能
          </button>
          <button
            class="hermes-add-btn is-mcp"
            type="button"
            @click="openPicker('mcp')"
          >
            <PlusOutlined /> MCP
          </button>
          <button
            class="hermes-add-btn is-tool"
            type="button"
            @click="openPicker('tool')"
          >
            <PlusOutlined /> 工具
          </button>
          <button
            class="hermes-add-btn is-kb"
            type="button"
            @click="openPicker('kb')"
          >
            <PlusOutlined /> 知识库
          </button>
          <a-upload
            :before-upload="onChatAttachPick"
            :multiple="true"
            :show-upload-list="false"
          >
            <button class="hermes-add-btn is-attach" type="button">
              <PaperClipOutlined /> 附件
            </button>
          </a-upload>
          <span v-if="mountedItems.length > 0" class="hermes-add-count">
            已挂载 {{ mountedItems.length }} 项
          </span>
          <a-select
            class="hermes-model-select"
            size="small"
            :options="modelSelectOptions"
            placeholder="选择模型"
            :value="modelId || undefined"
            @change="changeModel"
          />
        </div>
        <!-- 待发送附件：逐个上传进度条 + 百分比，可单个移除 -->
        <div v-if="chatAttachments.length > 0" class="hermes-attach-list">
          <div
            v-for="a in chatAttachments"
            :key="a.uid"
            class="hermes-attach-item"
            :class="a.status"
          >
            <PaperClipOutlined class="hermes-attach-icon" />
            <div class="hermes-attach-main">
              <span class="hermes-attach-name" :title="a.name">{{ a.name }}</span>
              <a-progress
                :percent="a.percent"
                size="small"
                :status="
                  a.status === 'error'
                    ? 'exception'
                    : a.status === 'done'
                      ? 'success'
                      : 'active'
                "
              />
            </div>
            <a-button
              type="text"
              size="small"
              class="hermes-attach-del"
              @click="removeChatAttachment(a)"
            >
              <template #icon><CloseOutlined /></template>
            </a-button>
          </div>
        </div>
        <div class="hermes-input-row">
          <a-textarea
            v-model:value="inputValue"
            :rows="3"
            placeholder="描述你的问题，AI 将在云端沙箱中执行…"
            @press-enter.prevent="handleSend"
          />
          <a-button
            type="primary"
            class="hermes-send"
            :loading="sending"
            @click="handleSend"
          >
            <template #icon><SendOutlined /></template>
            {{ streaming ? '停止' : '发送' }}
          </a-button>
        </div>
      </footer>
    </section>

    <!-- 右侧：沙箱桌面（可收起，收起后对话框横向铺满） -->
    <section v-show="!sandboxCollapsed" class="hermes-sandbox">
      <header class="hermes-sandbox-header">
        <span class="hermes-sandbox-title">
          <span class="sandbox-dot"></span>
          沙箱工作区
        </span>
        <a-space>
          <a-tooltip title="回退到上一级">
            <a-button size="small" :disabled="atWorkspaceRoot" @click="goBack">
              <template #icon><ArrowLeftOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-tooltip title="刷新">
            <a-button size="small" @click="loadSandboxFiles()">
              <template #icon><ReloadOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-upload
            :before-upload="beforeUpload"
            :multiple="true"
            :show-upload-list="false"
          >
            <a-button size="small" type="primary" :loading="uploading">
              <template #icon><CloudUploadOutlined /></template>
              上传
            </a-button>
          </a-upload>
          <a-tooltip title="收起工作区">
            <a-button size="small" @click="toggleSandbox">
              <template #icon><DoubleRightOutlined /></template>
            </a-button>
          </a-tooltip>
        </a-space>
      </header>

      <!-- 多文件上传进度：每个文件一行进度条 + 百分比 -->
      <div v-if="sandboxUploads.length > 0" class="hermes-upload-progress">
        <div v-for="u in sandboxUploads" :key="u.uid" class="hermes-upload-row">
          <span class="hermes-upload-name" :title="u.name">{{ u.name }}</span>
          <a-progress
            :percent="u.percent"
            size="small"
            :status="
              u.status === 'error'
                ? 'exception'
                : u.status === 'done'
                  ? 'success'
                  : 'active'
            "
          />
        </div>
      </div>

      <!-- 路径面包屑 -->
      <a-breadcrumb class="hermes-path">
        <a-breadcrumb-item>
          <a class="hermes-path-item" @click="loadSandboxFiles(WORKSPACE_ROOT)"
            >工作区</a
          >
        </a-breadcrumb-item>
        <a-breadcrumb-item v-for="seg in subSegments" :key="seg.path">
          <a class="hermes-path-item" @click="loadSandboxFiles(seg.path)">{{
            seg.part
          }}</a>
        </a-breadcrumb-item>
      </a-breadcrumb>

      <!-- 内嵌预览：点文件后从列表切到内容预览（功能1） -->
      <div v-if="preview.active" class="hermes-preview">
        <div class="hermes-preview-head">
          <FileTextFilled class="hermes-preview-head-icon" />
          <span class="hermes-preview-name" :title="preview.path">{{
            preview.name
          }}</span>
          <span v-if="preview.size" class="hermes-preview-size">{{
            formatSize(preview.size)
          }}</span>
          <a-space class="hermes-preview-actions">
            <a-button size="small" @click="closePreview">
              <template #icon><ArrowLeftOutlined /></template>返回
            </a-button>
            <a-button
              size="small"
              type="link"
              @click="downloadByPath(preview.path, preview.name)"
            >
              <template #icon><CloudDownloadOutlined /></template>下载
            </a-button>
          </a-space>
        </div>
        <div class="hermes-preview-body">
          <div v-if="preview.loading" class="hermes-preview-center">
            <a-spin />
          </div>
          <div
            v-else-if="preview.err"
            class="hermes-preview-center hermes-preview-err"
          >
            {{ preview.err }}
          </div>
          <img
            v-else-if="preview.kind === 'image'"
            :src="preview.blobUrl"
            :alt="preview.name"
            class="hermes-preview-img"
          />
          <iframe
            v-else-if="preview.kind === 'pdf'"
            :src="preview.blobUrl"
            class="hermes-preview-frame"
            title="pdf 预览"
          ></iframe>
          <audio
            v-else-if="preview.kind === 'audio'"
            :src="preview.blobUrl"
            controls
            class="hermes-preview-media"
          ></audio>
          <video
            v-else-if="preview.kind === 'video'"
            :src="preview.blobUrl"
            controls
            class="hermes-preview-video"
          ></video>
          <MarkdownRenderer
            v-else-if="preview.kind === 'md'"
            :content="preview.text"
            class="hermes-preview-md"
          />
          <MarkdownRenderer
            v-else-if="preview.kind === 'code'"
            :content="codeMarkdown(preview.text, preview.ext)"
            class="hermes-preview-md"
          />
          <iframe
            v-else-if="preview.kind === 'html'"
            :srcdoc="preview.text"
            sandbox=""
            class="hermes-preview-frame"
            title="html 预览"
          ></iframe>
          <pre
            v-else-if="preview.kind === 'text' || preview.kind === 'csv'"
            class="hermes-preview-pre"
            >{{ preview.text }}</pre
          >
          <div v-else-if="preview.kind === 'office'" class="hermes-preview-center">
            <FileTextFilled class="hermes-preview-office-icon" />
            <p>
              {{ (preview.ext || '该').toUpperCase() }}
              文档暂不支持在线预览，请下载查看
            </p>
          </div>
          <div v-else class="hermes-preview-center">
            <p>该文件类型暂不支持在线预览，请下载查看</p>
          </div>
          <div
            v-if="!preview.loading && preview.truncated"
            class="hermes-preview-truncated"
          >
            内容过长已截断，仅显示前 {{ preview.text.length }} 字符，下载查看完整内容
          </div>
        </div>
      </div>

      <!-- 文件列表 -->
      <div v-else class="hermes-fs" :class="{ loading: sandboxLoading }">
        <a-spin :spinning="sandboxLoading">
          <div v-if="sandboxItems.length > 0" class="hermes-fs-grid">
            <div
              v-for="item in sandboxItems"
              :key="item.name"
              class="hermes-fs-item"
              :class="item.type"
              @dblclick="
                item.type === 'dir' ? enterDir(item.name) : openFile(item)
              "
            >
              <div class="hermes-fs-icon">
                <FolderFilled v-if="item.type === 'dir'" />
                <FileTextFilled v-else />
              </div>
              <div class="hermes-fs-name" :title="item.name">
                {{ item.name }}
              </div>
              <div class="hermes-fs-meta">
                {{ item.type === 'dir' ? '文件夹' : formatSize(item.size) }}
                · {{ item.mtime }}
              </div>
              <div class="hermes-fs-actions">
                <a-button
                  size="small"
                  type="link"
                  @click.stop="downloadItem(item)"
                >
                  <template #icon><CloudDownloadOutlined /></template>
                  下载
                </a-button>
                <a-button
                  v-if="item.type === 'file'"
                  size="small"
                  type="link"
                  @click.stop="openFile(item)"
                >
                  查看
                </a-button>
              </div>
            </div>
          </div>
          <div v-else class="hermes-fs-empty">
            <FolderFilled />
            <p>该目录是空的，点击「上传」添加文件，或让 AI 在沙箱中生成文件</p>
          </div>
        </a-spin>
      </div>
    </section>

    <!-- 技能/MCP/工具/知识库选择弹窗 -->
    <a-modal
      v-model:open="pickerVisible"
      :title="
        pickerType === 'skill'
          ? '挂载技能'
          : pickerType === 'mcp'
            ? '挂载 MCP 连接'
            : pickerType === 'tool'
              ? '挂载平台工具'
              : '挂载知识库'
      "
      width="520px"
      @ok="confirmPicker"
    >
      <div v-if="pickerLoading" class="hermes-picker-loading">
        <a-spin />
      </div>
      <template v-else>
        <div v-if="pickerType === 'skill'">
          <div
            v-for="s in skillOptions"
            :key="s.id"
            class="hermes-picker-item"
            :class="{
              active: pickerSelected.some((i) => i.key === String(s.id)),
            }"
            @click="
              togglePick({
                key: String(s.id),
                name: s.name,
                source: 'skill',
              })
            "
          >
            <b>{{ s.name }}</b>
            <span>{{ s.description || '技能' }}</span>
          </div>
          <a-empty
            v-if="skillOptions.length === 0"
            description="暂无可用技能（需本人创建或被授权使用）"
          />
        </div>
        <div v-else-if="pickerType === 'mcp'">
          <div
            v-for="t in mcpOptions"
            :key="t.id"
            class="hermes-picker-item"
            :class="{
              active: pickerSelected.some((i) => i.key === String(t.id)),
            }"
            @click="
              togglePick({ key: String(t.id), name: t.name, source: 'mcp' })
            "
          >
            <b>{{ t.name }}</b>
            <span>{{ t.type }} · {{ t.description || 'MCP 连接' }}</span>
          </div>
          <a-empty
            v-if="mcpOptions.length === 0"
            description="暂无可挂载的 MCP 连接（需本人创建或被授权使用）"
          />
        </div>
        <div v-else-if="pickerType === 'tool'">
          <div
            v-for="t in toolOptions"
            :key="t.id"
            class="hermes-picker-item"
            :class="{
              active: pickerSelected.some((i) => i.key === String(t.id)),
            }"
            @click="
              togglePick({ key: String(t.id), name: t.name, source: 'tool' })
            "
          >
            <b>{{ t.name }}</b>
            <span>{{ t.description || '平台工具' }}</span>
          </div>
          <a-empty
            v-if="toolOptions.length === 0"
            description="暂无可用平台工具（需本人创建或被授权使用）"
          />
        </div>
        <div v-else>
          <div
            v-for="k in kbOptions"
            :key="k.id"
            class="hermes-picker-item"
            :class="{
              active: pickerSelected.some((i) => i.key === String(k.id)),
            }"
            @click="
              togglePick({
                key: String(k.id),
                name: k.name,
                source: 'kb',
              })
            "
          >
            <b>{{ k.name }}</b>
            <span>{{ k.description || '知识库' }}</span>
          </div>
          <a-empty v-if="kbOptions.length === 0" description="暂无可用知识库" />
        </div>
      </template>
    </a-modal>
  </div>
</template>

<style lang="less" scoped>
.hermes-page {
  display: flex;
  gap: 12px;
  height: calc(100vh - 140px);
  padding: 12px;
}

/* ==================== 输出物文件卡片（功能2） ==================== */
.hermes-artifacts {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;

  .hermes-artifacts-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-color-2, #4e5969);
  }

  .hermes-artifact-card {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    border: 1px solid var(--border-color, #e5e7eb);
    border-radius: 8px;
    background: var(--hover-bg-color, #f8fafc);
    cursor: pointer;
    transition: all 0.15s;

    &:hover {
      border-color: var(--primary-color, #1677ff);
      box-shadow: 0 1px 6px rgba(22, 119, 255, 0.15);
    }

    .hermes-artifact-icon {
      color: var(--primary-color, #1677ff);
      font-size: 16px;
      flex-shrink: 0;
    }

    .hermes-artifact-main {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1;
    }

    .hermes-artifact-name {
      font-size: 13px;
      font-weight: 500;
      color: var(--text-color-1, #1f2329);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-artifact-desc {
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-artifact-dl {
      flex-shrink: 0;
    }
  }
}

/* ==================== 沙箱内嵌预览（功能1） ==================== */
.hermes-preview {
  display: flex;
  flex-direction: column;

  .hermes-preview-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 4px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);

    .hermes-preview-head-icon {
      color: var(--primary-color, #1677ff);
      flex-shrink: 0;
    }

    .hermes-preview-name {
      font-size: 13px;
      font-weight: 500;
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-preview-size {
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
      flex-shrink: 0;
    }

    .hermes-preview-actions {
      flex-shrink: 0;
    }
  }

  .hermes-preview-body {
    overflow: auto;
    padding: 10px 4px;

    .hermes-preview-center {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      min-height: 200px;
      color: var(--text-color-3, #8a9099);
      text-align: center;
      padding: 20px;
    }

    .hermes-preview-err {
      color: #d4380d;
    }

    .hermes-preview-office-icon {
      font-size: 40px;
      color: var(--text-color-3, #c9ced6);
    }

    .hermes-preview-img {
      max-width: 100%;
      height: auto;
      display: block;
      margin: 0 auto;
    }

    .hermes-preview-frame {
      width: 100%;
      height: 60vh;
      min-height: 400px;
      border: none;
      background: #fff;
    }

    .hermes-preview-media {
      width: 100%;
    }

    .hermes-preview-video {
      width: 100%;
      max-height: 70vh;
      background: #000;
    }

    .hermes-preview-pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 12px;
      line-height: 1.6;
    }

    .hermes-preview-truncated {
      margin-top: 10px;
      padding: 6px 10px;
      font-size: 12px;
      color: #ad6800;
      background: rgba(250, 173, 20, 0.1);
      border-radius: 6px;
    }
  }
}

// 沙箱收起：右栏已 v-show 隐藏，对话框从固定 55% 改为撑满剩余宽度
.hermes-page.is-sandbox-collapsed {
  .hermes-chat {
    flex: 1 1 auto;
  }
}

// ==================== 左侧对话框 ====================
.hermes-chat {
  flex: 0 0 55%;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-radius: 12px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  overflow: hidden;

  .hermes-chat-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);
    background: linear-gradient(120deg, #f0f5ff 0%, #fff 60%);

    .hermes-logo {
      width: 40px;
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 10px;
      color: #fff;
      font-size: 20px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
    }

    .hermes-title {
      flex: 1;
      min-width: 0;
      h2 {
        margin: 0;
        font-size: 16px;
        color: var(--text-color-1, #1f2329);
      }
      p {
        margin: 2px 0 0;
        font-size: 12px;
        color: var(--text-color-3, #8a9099);
      }
    }
  }

  .hermes-mounted {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 10px 16px 0;
    padding: 8px 12px;
    min-height: 34px;
    border: 1px dashed var(--border-color-2, #e5e7eb);
    border-radius: 10px;
    background: var(--hover-bg-color, #f8fafc);

    .hermes-mounted-label {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-color-2, #4e5969);
      margin-right: 2px;
    }

    .hermes-mounted-empty {
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
    }
  }

  .hermes-hd-list {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;

    .hermes-msg {
      display: flex;
      gap: 8px;
      margin-bottom: 14px;

      &.is-user {
        flex-direction: row-reverse;

        .hermes-bubble {
          background: var(--primary-color, #1677ff);
          color: #fff;
          border-radius: 10px 2px 10px 10px;
        }

        // 用户一侧靠右：文本 + 附件标签竖排，整块限宽
        .hermes-user-wrap {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 6px;
          max-width: 78%;

          .hermes-bubble {
            max-width: 100%;
          }
        }
      }

      .hermes-msg-attach {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        justify-content: flex-end;
      }

      &.is-ai {
        .hermes-bubble {
          background: var(--hover-color, #f5f7fa);
          border-radius: 2px 10px 10px 10px;
        }
      }

      .hermes-avatar {
        width: 30px;
        height: 30px;
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        font-size: 14px;

        &.ai {
          color: #fff;
          background: linear-gradient(135deg, #6366f1, #8b5cf6);
        }
        &.user {
          color: #fff;
          background: var(--primary-color, #1677ff);
          font-size: 12px;
        }
      }

      .hermes-bubble {
        max-width: 78%;
        padding: 8px 12px;
        font-size: 13px;
        line-height: 1.7;
        word-break: break-word;

        :deep(p) {
          margin: 4px 0;
        }
        :deep(blockquote) {
          margin: 6px 0;
          padding: 4px 10px;
          border-left: 3px solid var(--primary-color, #1677ff);
          background: rgba(22, 119, 255, 0.06);
          border-radius: 4px;
        }
        :deep(strong) {
          font-weight: 600;
        }
        :deep(ul) {
          margin: 4px 0;
          padding-left: 18px;
        }
      }
    }

    .hermes-empty {
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: var(--text-color-3, #8a9099);

      .hermes-empty-icon {
        font-size: 44px;
        color: #a5b4fc;
      }
      p {
        margin: 8px 0 0;
        font-size: 13px;
      }
      .hermes-empty-tip {
        font-size: 12px;
        opacity: 0.75;
      }
    }
  }

  .hermes-input {
    padding: 10px 16px 14px;
    border-top: 1px solid var(--border-color, #f0f0f0);

    .hermes-input-tools {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;

      .hermes-add-btn {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        height: 28px;
        padding: 0 12px;
        font-size: 12px;
        line-height: 1;
        border-radius: 8px;
        border: 1px solid transparent;
        cursor: pointer;
        transition:
          transform 0.15s ease,
          box-shadow 0.15s ease,
          filter 0.15s ease;

        .anticon {
          font-size: 12px;
        }

        &:hover {
          transform: translateY(-1px);
          box-shadow: 0 4px 10px rgba(0, 0, 0, 0.12);
          filter: brightness(1.03);
        }

        &:active {
          transform: translateY(0);
        }

        &.is-skill {
          color: #1677ff;
          background: rgba(22, 119, 255, 0.1);
          border-color: rgba(22, 119, 255, 0.32);
        }

        &.is-mcp {
          color: #fa8c16;
          background: rgba(250, 140, 22, 0.1);
          border-color: rgba(250, 140, 22, 0.32);
        }

        &.is-tool {
          color: #52c41a;
          background: rgba(82, 196, 26, 0.1);
          border-color: rgba(82, 196, 26, 0.32);
        }

        &.is-kb {
          color: #722ed1;
          background: rgba(114, 46, 209, 0.1);
          border-color: rgba(114, 46, 209, 0.32);
        }

        &.is-attach {
          color: #13c2c2;
          background: rgba(19, 194, 194, 0.1);
          border-color: rgba(19, 194, 194, 0.32);
        }
      }

      .hermes-add-count {
        font-size: 12px;
        color: var(--text-color-3, #8a9099);
      }

      .hermes-model-select {
        margin-left: auto;
        width: 160px;
        flex-shrink: 0;
      }
    }

    // 待发送附件列表：每项一行（回形针 + 文件名/进度条 + 删除）
    .hermes-attach-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 10px;

      .hermes-attach-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        border: 1px solid var(--border-color, #f0f0f0);
        border-radius: 8px;
        background: var(--hover-color, #f8fafc);

        &.error {
          border-color: #ffccc7;
          background: rgba(255, 77, 79, 0.04);
        }

        .hermes-attach-icon {
          flex-shrink: 0;
          color: #13c2c2;
        }

        .hermes-attach-main {
          flex: 1;
          min-width: 0;

          .hermes-attach-name {
            display: block;
            overflow: hidden;
            font-size: 12px;
            text-overflow: ellipsis;
            white-space: nowrap;
          }

          :deep(.ant-progress) {
            margin: 0;
            line-height: 1.2;
          }
        }

        .hermes-attach-del {
          flex-shrink: 0;
        }
      }
    }

    .hermes-input-row {
      display: flex;
      gap: 10px;
      align-items: flex-end;

      textarea {
        resize: none;
        font-size: 13px;
      }

      .hermes-send {
        height: auto;
        padding: 8px 16px;
      }
    }
  }
}

// ==================== 右侧沙箱桌面 ====================
.hermes-sandbox {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-radius: 12px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  overflow: hidden;

  .hermes-sandbox-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);
    background: linear-gradient(120deg, #f6f8fc 0%, #fff 60%);

    .hermes-sandbox-title {
      font-size: 14px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;

      .sandbox-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #22c55e;
        box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
      }
      small {
        font-weight: 400;
        font-size: 12px;
        color: var(--text-color-3, #8a9099);
      }
    }
  }

  .hermes-path {
    padding: 8px 14px 0;
    font-size: 12px;

    .hermes-path-item {
      cursor: pointer;
    }
  }

  // 多文件上传进度列表（每文件一行进度条 + 百分比）
  .hermes-upload-progress {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);
    background: var(--hover-color, #f8fafc);

    .hermes-upload-row {
      display: flex;
      align-items: center;
      gap: 10px;

      .hermes-upload-name {
        flex: 0 0 40%;
        min-width: 0;
        overflow: hidden;
        font-size: 12px;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      :deep(.ant-progress) {
        flex: 1;
        margin: 0;
      }
    }
  }

  .hermes-fs {
    flex: 1;
    overflow-y: auto;
    padding: 10px 12px 14px;

    .hermes-fs-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(128px, 1fr));
      gap: 10px;
    }

    .hermes-fs-item {
      position: relative;
      padding: 12px 8px 8px;
      border-radius: 10px;
      border: 1px solid transparent;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;

      &:hover {
        border-color: var(--border-color-2, #d9d9d9);
        background: var(--hover-color, #f5f7fa);

        .hermes-fs-actions {
          opacity: 1;
        }
      }

      .hermes-fs-icon {
        font-size: 34px;
        line-height: 1;
      }

      &.file .hermes-fs-icon {
        color: #60a5fa;
      }
      &.dir .hermes-fs-icon {
        color: #fbbf24;
      }

      .hermes-fs-name {
        margin-top: 6px;
        font-size: 12px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .hermes-fs-meta {
        margin-top: 2px;
        font-size: 11px;
        color: var(--text-color-3, #8a9099);
      }

      .hermes-fs-actions {
        margin-top: 6px;
        opacity: 0;
        transition: opacity 0.2s;
        display: flex;
        justify-content: center;
      }
    }

    .hermes-fs-empty {
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: var(--text-color-3, #8a9099);
      font-size: 40px;

      p {
        font-size: 12px;
        max-width: 240px;
        text-align: center;
      }
    }
  }
}

// ==================== 弹窗 ====================
.hermes-picker-loading {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hermes-picker-item {
  padding: 8px 12px;
  margin-bottom: 8px;
  border: 1px solid var(--border-color, #f0f0f0);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 2px;

  b {
    font-size: 13px;
  }
  span {
    font-size: 12px;
    color: var(--text-color-3, #8a9099);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &.active {
    border-color: var(--primary-color, #1677ff);
    background: rgba(22, 119, 255, 0.06);
  }
}

.hermes-file-meta {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--text-color-3, #8a9099);
}

.hermes-file-content {
  max-height: 480px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.6;
}

// ==================== 会话侧栏 ====================
.hermes-side {
  flex: 0 0 160px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 8px;
  border-radius: 12px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  overflow: hidden;

  .hermes-side-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  .hermes-side-item {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 7px 8px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;

    &:hover {
      background: var(--hover-color, #f5f7fa);

      .hermes-side-more {
        opacity: 1;
      }
    }
    &.active {
      background: rgba(22, 119, 255, 0.08);

      .hermes-side-title {
        color: var(--primary-color, #1677ff);
      }
    }

    .hermes-side-title {
      flex: 1;
      min-width: 0;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-side-more {
      display: inline-flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      border-radius: 4px;
      color: var(--text-color-3, #8a9099);
      opacity: 0;
      transition: opacity 0.15s;

      .anticon {
        transform: rotate(90deg);
        font-size: 14px;
      }

      &:hover {
        background: rgba(0, 0, 0, 0.06);
        color: var(--text-color-2, #4e5969);
      }
    }
  }

  .hermes-side-empty {
    padding: 18px 0;
    text-align: center;
    font-size: 12px;
    color: var(--text-color-3, #8a9099);
  }
}

// ==================== assistant 消息复合体（思考/工具/运行态） ====================
.hermes-chat .hermes-msg .hermes-body {
  max-width: 78%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;

  .hermes-bubble {
    max-width: 100%;
  }

  // 时间片段通用：细边框、可折叠、次要色，对齐 workbuddy/deepseek 的“思考/工具”轨道
  .hermes-seg {
    width: 100%;
    border: 1px solid var(--border-color, #eef0f3);
    border-radius: 8px;
    background: var(--ac-bg-1, #fafbfc);
    overflow: hidden;

    .hermes-seg-summary {
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
      user-select: none;

      &::-webkit-details-marker {
        display: none;
      }
    }

    .hermes-seg-body {
      margin: 0 10px 8px;
      padding: 8px 10px;
      border-top: 1px dashed var(--border-color, #eef0f3);
      font-size: 12px;
      line-height: 1.6;
      color: var(--text-color-2, #5e6470);
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 260px;
      overflow-y: auto;
    }

    // 工具行标题：模型写的用途作主体，技术名退成右侧弱化 chip
    .hermes-call-title {
      flex: 0 1 auto;
      min-width: 0;
      color: var(--text-color-1, #1f2937);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-call-kind {
      flex: none;
      padding: 0 6px;
      border-radius: 999px;
      background: rgb(124 58 237 / 8%);
      font-size: 11px;
      white-space: nowrap;
    }

    // 折叠摘要后面的一行预览（思考块）：不点开也能跟上
    .hermes-seg-hint {
      flex: 0 1 auto;
      min-width: 0;
      font-size: 11px;
      opacity: 0.75;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .hermes-thinking .hermes-seg-summary {
    color: #6366f1;
  }

  .hermes-call .hermes-seg-summary {
    color: #7c3aed;
  }

  .hermes-seg-done {
    color: #16a34a;
  }

  .hermes-msg-error {
    :deep(.ant-tag) {
      max-width: 100%;
      white-space: normal;
      height: auto;
    }
  }

  .hermes-askuser {
    margin-top: 8px;
    padding: 12px 14px;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    background: #fafafa;
    display: flex;
    flex-direction: column;
    gap: 12px;

    .hermes-askuser-q {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .hermes-askuser-title {
      font-weight: 600;
      color: #111827;
    }

    .hermes-askuser-opts {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .hermes-askuser-opt {
      display: flex;
      align-items: baseline;
      gap: 6px;
      padding: 6px 8px;
      border-radius: 8px;
      cursor: pointer;
      transition: background 0.15s;

      &:hover {
        background: #f0f0ff;
      }

      &.checked {
        background: #eef2ff;
      }

      input {
        margin: 0;
        flex: none;
      }

      .hermes-askuser-opt-label {
        font-weight: 500;
        color: #1f2937;
      }

      .hermes-askuser-opt-desc {
        color: #6b7280;
      }
    }

    .hermes-askuser-other {
      width: 100%;
    }
  }

  .hermes-running {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 2px 2px;
    font-size: 12px;
    color: var(--text-color-3, #8a9099);
  }
}
</style>
