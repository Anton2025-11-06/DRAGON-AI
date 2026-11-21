<script lang="ts">
import type { ComposerChip } from './composer';

import {
  computed,
  defineAsyncComponent,
  defineComponent,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from 'vue';

import { useAccessStore } from '@vben/stores';

import {
  ApiOutlined,
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  CloseOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DoubleLeftOutlined,
  DoubleRightOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  FileTextFilled,
  FolderFilled,
  HolderOutlined,
  LoadingOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { getTraceId } from '#/api/request';

import MarkdownRenderer from '../ai/workflow/components/debug/MarkdownRenderer.vue';
import ComposerEditor from './ComposerEditor.vue';
import * as api from './api';

/** 时间线片段：assistant 消息按到达顺序拆成思考/工具/文本/提醒四类块，各自可折叠 */
type Segment =
  | {
      id: string;
      kind: 'nudge';
      /** 预算上限 / 本轮第几次提醒 / 清单剩余未完成项（帧缺字段则摘要不报计数） */
      maxNudges?: number;
      nudge?: number;
      /** 系统塞给模型的纠正提醒正文（已剔掉外壳标签），折叠里给想看的人看 */
      text: string;
      unfinished?: number;
    }
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

/** 提醒正文剔掉 <system-reminder> 外壳：那是给模型的分隔标记，不是给人看的正文 */
function unwrapReminder(text: string): string {
  return text.replaceAll(/<\/?system-reminder>/gu, '').trim();
}

/**
 * 提醒行摘要：计数齐全直接报「还剩几项、第几次」，比把模型收到的那段长提醒
 * 铺在时间线上好读；计数缺失（协议以后改了字段）才回落到正文首行。
 */
function nudgeHint(seg: Segment): string {
  if (seg.kind !== 'nudge') return '';
  if (typeof seg.unfinished === 'number') {
    const budget =
      typeof seg.nudge === 'number' && typeof seg.maxNudges === 'number'
        ? `（第 ${seg.nudge}/${seg.maxNudges} 次提醒）`
        : '';
    return `任务清单还有 ${seg.unfinished} 项未完成，已把它推回去继续${budget}`;
  }
  return clip(unwrapReminder(seg.text).split('\n')[0] ?? '', TOOL_HINT_MAX);
}

/** 预览弹窗指向的文件：内容取回与渲染都在 SandboxPreviewModal 里，这里只登记「开哪个」 */
interface PreviewTarget {
  name: string;
  open: boolean;
  path: string;
  size: number;
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
  /** 停止已下达、worker 正在收尾：气泡已判停，但发送口仍锁着等终态帧 */
  stopping?: boolean;
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

/** 芯片身份：资源类型 + id（不同类资源 id 会重号，不能只按 id 认） */
const chipKey = (chip: ComposerChip) => `${chip.source}:${chip.key}`;

/**
 * 富输入框对外能力。框内 DOM 由组件自己管（contenteditable 里要插元素芯片），
 * 父组件只通过这些方法读写正文与芯片，不直接碰 contenteditable。
 */
interface ComposerHandle {
  clear: () => void;
  focus: () => void;
  getChips: () => ComposerChip[];
  getHtml: () => string;
  getText: () => string;
  insertChips: (chips: ComposerChip[]) => void;
  saveCaret: () => void;
  setHtml: (html: string) => ComposerChip[];
  syncChips: (chips: ComposerChip[]) => void;
}

/** 每轮发送配置快照：排队期间再改 composer，也不影响已经入队的问题。 */
interface TurnSnapshot {
  attachments: api.HarnessAttachment[];
  modelId?: number;
  mounts: api.HarnessMounts;
  orchAgent: api.HarnessOrchAgent | null;
  sessionId?: number;
}

/** 前端待发送问题：仅存在当前页面内存中，刷新后不持久化。 */
interface QueuedQuestion extends TurnSnapshot {
  displayText: string;
  id: string;
  messageText: string;
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
  name: 'Harnes智能体ExplorerPage',
  components: {
    ApiOutlined,
    ArrowLeftOutlined,
    BulbOutlined,
    CheckCircleOutlined,
    CheckOutlined,
    CloseOutlined,
    CloudDownloadOutlined,
    CloudUploadOutlined,
    ComposerEditor,
    DatabaseOutlined,
    DeleteOutlined,
    DoubleLeftOutlined,
    DoubleRightOutlined,
    EditOutlined,
    ExclamationCircleOutlined,
    FileTextFilled,
    FolderFilled,
    HolderOutlined,
    LoadingOutlined,
    MarkdownRenderer,
    MoreOutlined,
    PaperClipOutlined,
    PlusOutlined,
    ReloadOutlined,
    RightOutlined,
    RobotOutlined,
    SearchOutlined,
    // 预览库（docx/excel/pptx 解析器 + codemirror）有几 MB，只在真点开文件时才拉
    SandboxPreviewModal: defineAsyncComponent(
      () => import('./SandboxPreviewModal.vue'),
    ),
    SendOutlined,
    StopOutlined,
    ToolOutlined,
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
    /** 会话级 composer 草稿：切换时恢复模型、挂载与智能体，避免把上一个会话的配置误发到下一个。 */
    const composerMeta = new Map<
      number,
      {
        modelId?: number;
        mounts?: Partial<api.HarnessMounts>;
        orchAgent?: api.HarnessOrchAgent | null;
      }
    >();
    /**
     * 会话级输入框草稿（文字 + 芯片穿插的原样结构）：正文与已选资源现在同处一个
     * 框内，拆不成两份状态分开存，所以整份结构按会话缓存，切回来原样还原。
     * 只在本页内存里往返、不落任何存储，还原时子组件还要过一道白名单清洗。
     */
    const sessionDrafts = new Map<number, string>();
    /** turnId → 该轮订阅：一个在跑的轮各占一条流，与"当前在看哪个会话"无关 */
    const turnStreams = new Map<
      string,
      { es: EventSource; sessionId: number }
    >();
    /**
     * turnId → 「停止收尾宽限期」定时器：点停止后这条流要留着等 worker 的终态帧
     * （那一帧才解锁发送口），worker 进程暴毙就永远等不到，靠这个定时器兜底。
     */
    const stopGuards = new Map<string, ReturnType<typeof setTimeout>>();
    /** 点停止后等 worker 收尾的最长时间（毫秒），到点按已停处理、放开发送口 */
    const STOP_SETTLE_GUARD_MS = 30_000;
    /** 撤掉某轮的停止收尾宽限期定时器（终态帧到了或流关了都不用再等） */
    function clearStopGuard(turnId?: string) {
      if (!turnId) return;
      const timer = stopGuards.get(turnId);
      if (timer) {
        clearTimeout(timer);
        stopGuards.delete(turnId);
      }
    }
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
    /** 每个会话各自排队，切会话不会把待发问题带到另一段对话。 */
    const questionQueues = reactive<Record<number, QueuedQuestion[]>>({});
    const queuedQuestions = computed<QueuedQuestion[]>(
      () => questionQueues[sessionId.value] ?? [],
    );
    const editingQueueId = ref('');
    const editingQueueText = ref('');
    const draggedQueueId = ref('');
    const queuePumpPending = new Set<number>();
    let queueSeq = 0;
    let disposed = false;
    /** 展示的消息 = 当前会话那一份缓冲（切 sessionId 即换一份，不动别会话） */
    const messages = computed<ChatMsg[]>(
      () => sessionStore[sessionId.value] ?? [],
    );
    /** 当前会话是否有在跑的轮（决定 composer 是"发送"还是"中断"）：只看这一份缓冲 */
    const streaming = computed(() => messages.value.some((m) => m.running));
    /**
     * 停止已下达、worker 还在收尾：气泡已经不转了，但这一轮的 AgentState 还没
     * 落定，此时发下一句就是并发写同一份会话记忆（后端同会话闸门也会拒），
     * 所以发送口继续锁着，直到终态帧 / 断流 / 宽限期到点三者之一把它放开。
     */
    const stopping = computed(() => messages.value.some((m) => m.stopping));
    /** 这一轮到底完了没：模板里一律按 busy 判，别再分着看 streaming / stopping */
    const busy = computed(() => streaming.value || stopping.value);

    // 选择弹窗（四口候选：kb / mcp / tool / skill）
    const pickerVisible = ref(false);
    const pickerType = ref<'kb' | 'mcp' | 'skill' | 'tool'>('skill');
    const pickerLoading = ref(false);
    const skillOptions = ref<api.HarnessOptionItem[]>([]);
    const mcpOptions = ref<api.HarnessOptionItem[]>([]);
    const toolOptions = ref<api.HarnessOptionItem[]>([]);
    const kbOptions = ref<api.HarnessOptionItem[]>([]);
    const pickerSelected = ref<MountedItem[]>([]);

    // 智能体选择（四类编排配置：单/团队/目标/SOP，use 口径，只能选一个）
    const orchAgent = ref<api.HarnessOrchAgent | null>(null);
    const agentPickerVisible = ref(false);
    const agentPickerLoading = ref(false);
    const agentOptions = ref<api.HarnessOrchAgent[]>([]);
    /** 弹框里当前勾选的键（点「确定」才落到 orchAgent）：`${mode}:${id}` */
    const agentPickKey = ref('');
    /** 弹框分组顺序与标题（与四个智能体页面同序） */
    const AGENT_MODE_LABELS: Array<{
      label: string;
      mode: api.HarnessOrchMode;
    }> = [
      { label: '单智能体', mode: 'single' },
      { label: '团队智能体', mode: 'team' },
      { label: '目标智能体', mode: 'goal' },
      { label: 'SOP', mode: 'sop' },
    ];
    const agentGroups = computed(() =>
      AGENT_MODE_LABELS.map((g) => ({
        ...g,
        items: agentOptions.value.filter((a) => a.mode === g.mode),
      })),
    );
    /** 同一张表的不同模式 id 可能重号，分组键带上 mode */
    const agentKeyOf = (a: api.HarnessOrchAgent) => `${a.mode}:${a.id}`;
    /** 已选智能体的模式文案（标签上显示「团队智能体 · xxx」，便于分辨重名） */
    const agentModeLabel = (mode: api.HarnessOrchMode) =>
      AGENT_MODE_LABELS.find((g) => g.mode === mode)?.label || mode;

    // ==================== / 与 @ 资源命令面板 ====================
    type CommandSource = 'agent' | MountedItem['source'];
    interface CommandResource {
      agent?: api.HarnessOrchAgent;
      description?: null | string;
      key: string;
      mount?: MountedItem;
      name: string;
      source: CommandSource;
    }
    interface CommandCategory {
      icon: string;
      items: CommandResource[];
      label: string;
      source: CommandSource;
    }

    const composerRef = ref<ComposerHandle | null>(null);
    /** 面板里的名称搜索框：面板一开就抢焦点，方向键/空格都由它接 */
    const commandSearchRef = ref<HTMLInputElement | null>(null);
    const commandOpen = ref(false);
    const commandLoading = ref(false);
    /** 横向 Tab 上选中的分类（←→ 换它） */
    const commandCategoryIndex = ref(0);
    /** 当前分类列表里的高亮行（↑↓ 换它），下标针对过滤后的条目 */
    const commandItemIndex = ref(0);
    /** 名称搜索词：切分类不清词，方便同一个词横着几类一起找 */
    const commandQuery = ref('');
    const commandAgentKey = ref('');
    const commandSelectedMounts = ref<MountedItem[]>([]);

    function resourceIcon(source: CommandSource): string {
      return {
        agent: 'RobotOutlined',
        kb: 'DatabaseOutlined',
        mcp: 'ApiOutlined',
        skill: 'BulbOutlined',
        tool: 'ToolOutlined',
      }[source];
    }

    const commandCategories = computed<CommandCategory[]>(() => {
      const mountCategory = (
        source: MountedItem['source'],
        label: string,
        items: api.HarnessOptionItem[],
      ): CommandCategory => ({
        source,
        label,
        icon: resourceIcon(source),
        items: items.map((item) => ({
          key: String(item.id),
          name: item.name,
          description: item.description,
          source,
          mount: { key: String(item.id), name: item.name, source },
        })),
      });
      return [
        {
          source: 'agent',
          label: '智能体',
          icon: resourceIcon('agent'),
          items: agentOptions.value.map((agent) => ({
            key: agentKeyOf(agent),
            name: `${agentModeLabel(agent.mode)} · ${agent.name}`,
            description: agent.purpose,
            source: 'agent',
            agent,
          })),
        },
        mountCategory('skill', '技能', skillOptions.value),
        mountCategory('mcp', 'MCP', mcpOptions.value),
        mountCategory('tool', '工具', toolOptions.value),
        mountCategory('kb', '知识库', kbOptions.value),
      ];
    });

    /** 当前 Tab 选中的分类 */
    const commandActiveCategory = computed<CommandCategory | undefined>(() => {
      const category = commandCategories.value[commandCategoryIndex.value];
      return category ?? commandCategories.value[0];
    });

    /** 当前分类按名称/描述过滤后的条目：列表渲染与键盘导航都以它为准 */
    const commandActiveItems = computed<CommandResource[]>(() => {
      const keyword = commandQuery.value.trim().toLowerCase();
      const items = commandActiveCategory.value?.items || [];
      if (!keyword) return items;
      return items.filter((item) =>
        `${item.name} ${item.description || ''}`
          .toLowerCase()
          .includes(keyword),
      );
    });

    // 搜索词一变、分类一换，高亮行就得回到首条，否则下标会落在过滤后的列表外
    watch([commandQuery, commandCategoryIndex], () => {
      commandItemIndex.value = 0;
    });

    function focusComposer() {
      nextTick(() => composerRef.value?.focus());
    }

    function focusCommandSearch() {
      nextTick(() => commandSearchRef.value?.focus());
    }

    /** 已选智能体 + 已挂载资源 → 输入框里应有的芯片（智能体也是一枚芯片，不再单列一行） */
    function resourceChips(
      agent: api.HarnessOrchAgent | null = orchAgent.value,
      mounts: MountedItem[] = mountedItems.value,
    ): ComposerChip[] {
      const chips: ComposerChip[] = [];
      if (agent) {
        chips.push({
          key: agentKeyOf(agent),
          name: agent.name,
          source: 'agent',
        });
      }
      mounts.forEach((item) =>
        chips.push({ key: item.key, name: item.name, source: item.source }),
      );
      return chips;
    }

    /** 把框内芯片收到与 composer 状态一致：多退少补，保证两边不打架 */
    function syncComposerChips() {
      composerRef.value?.syncChips(resourceChips());
    }

    async function openCommandPalette() {
      commandSelectedMounts.value = mountedItems.value.map((item) => ({
        ...item,
      }));
      commandAgentKey.value = orchAgent.value ? agentKeyOf(orchAgent.value) : '';
      commandCategoryIndex.value = 0;
      commandItemIndex.value = 0;
      commandQuery.value = '';
      commandOpen.value = true;
      commandLoading.value = true;
      // 搜索框会抢走光标，先把插入位置记下来：确认时芯片要插回句子中间
      composerRef.value?.saveCaret();
      try {
        const [, agentRes] = await Promise.all([
          ensurePickerOptions(),
          api.HarnessOrchAgents(),
        ]);
        agentOptions.value = agentRes.items || [];
      } catch {
        agentOptions.value = [];
      } finally {
        commandLoading.value = false;
        // 搜索框要等 loading 结束才渲染出来，抢焦点必须放到这一步之后：
        // 否则刚点「+资源」时输入框还不存在，focus 落空，方向键就没人接，
        // 非得用鼠标点一下条目才恢复。
        focusCommandSearch();
      }
    }

    function closeCommandPalette() {
      commandOpen.value = false;
      focusComposer();
    }

    function confirmCommandPalette() {
      const previousKey = orchAgent.value ? agentKeyOf(orchAgent.value) : '';
      const nextAgent =
        agentOptions.value.find(
          (agent) => agentKeyOf(agent) === commandAgentKey.value,
        ) || null;
      const nextMounts = commandSelectedMounts.value.map((item) => ({
        ...item,
      }));
      const before = new Set(resourceChips().map((chip) => chipKey(chip)));
      // 新勾选的插回光标处（就是刚才打 / 的位置），不堆到末尾去
      const added = resourceChips(nextAgent, nextMounts).filter(
        (chip) => !before.has(chipKey(chip)),
      );
      composerRef.value?.insertChips(added);
      orchAgent.value = nextAgent;
      mountedItems.value = nextMounts;
      // 面板里取消勾选的，芯片也从框里揭掉
      syncComposerChips();
      if (nextAgent && agentKeyOf(nextAgent) !== previousKey) modelId.value = 0;
      commandOpen.value = false;
      focusComposer();
    }

    function focusCommandCategory(index: number) {
      commandCategoryIndex.value = index;
      commandItemIndex.value = 0;
      focusCommandSearch();
    }

    function isCommandItemSelected(item: CommandResource): boolean {
      if (item.source === 'agent') return commandAgentKey.value === item.key;
      return commandSelectedMounts.value.some(
        (mounted) =>
          mounted.source === item.mount?.source && mounted.key === item.key,
      );
    }

    function toggleCommandItem(item: CommandResource) {
      if (item.agent) {
        commandAgentKey.value =
          commandAgentKey.value === item.key ? '' : item.key;
        return;
      }
      if (!item.mount) return;
      const index = commandSelectedMounts.value.findIndex(
        (mounted) =>
          mounted.source === item.mount?.source && mounted.key === item.key,
      );
      if (index === -1) commandSelectedMounts.value.push({ ...item.mount });
      else commandSelectedMounts.value.splice(index, 1);
    }

    /** 鼠标点条目：高亮行跟着移过去再勾选，与键盘是同一套焦点语义 */
    function pickCommandItem(itemIndex: number, item: CommandResource) {
      commandItemIndex.value = itemIndex;
      toggleCommandItem(item);
      focusCommandSearch();
    }

    /** 高亮行跟着滚，不然搜出几十条时键盘选到视区外看不见 */
    function scrollCommandItemIntoView() {
      nextTick(() => {
        document
          .querySelector('.hermes-command-list .hermes-command-item.active')
          ?.scrollIntoView({ block: 'nearest' });
      });
    }

    function moveCommandItem(delta: number) {
      const count = commandActiveItems.value.length;
      if (count === 0) return;
      commandItemIndex.value = Math.max(
        0,
        Math.min(count - 1, commandItemIndex.value + delta),
      );
      scrollCommandItemIntoView();
    }

    function moveCommandCategory(delta: number) {
      const count = commandCategories.value.length;
      if (count === 0) return;
      commandCategoryIndex.value = Math.max(
        0,
        Math.min(count - 1, commandCategoryIndex.value + delta),
      );
      commandItemIndex.value = 0;
      nextTick(() => {
        document
          .querySelector('.hermes-command-tabs .hermes-command-tab.active')
          ?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      });
    }

    function selectedCommandCount(source: CommandSource): number {
      if (source === 'agent') return commandAgentKey.value ? 1 : 0;
      return commandSelectedMounts.value.filter(
        (item) => item.source === source,
      ).length;
    }

    /** 面板键盘口径：←→ 换分类，↑↓ 换条目，空格勾选，回车完成，Esc 退出 */
    function onCommandSearchKeydown(e: KeyboardEvent) {
      if (e.isComposing) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        moveCommandItem(e.key === 'ArrowDown' ? 1 : -1);
        return;
      }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        moveCommandCategory(e.key === 'ArrowRight' ? 1 : -1);
        return;
      }
      if (e.key === ' ') {
        // 资源名几乎不带空格，空格让给「勾选」；搜不进去空格不是损失
        e.preventDefault();
        const item = commandActiveItems.value[commandItemIndex.value];
        if (item) toggleCommandItem(item);
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        confirmCommandPalette();
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        closeCommandPalette();
      }
    }

    /** 芯片在框里被退格或点叉删掉：状态跟着减，芯片本身已经不在 DOM 里了 */
    function onChipRemoved(chip: ComposerChip) {
      if (chip.source === 'agent') {
        if (orchAgent.value && agentKeyOf(orchAgent.value) === chip.key) {
          orchAgent.value = null;
        }
        return;
      }
      removeMounted({
        key: chip.key,
        name: chip.name,
        source: chip.source as MountedItem['source'],
      });
    }

    function onComposerKeydown(e: KeyboardEvent) {
      if (e.isComposing) return;
      if (
        (e.key === '/' || e.key === '@') &&
        !e.altKey &&
        !e.ctrlKey &&
        !e.metaKey
      ) {
        // 触发字符不落正文：确认时芯片就插在这个位置，取消也不用回删
        e.preventDefault();
        openCommandPalette();
        return;
      }
      onComposerEnter(e);
    }

    /** 富输入框每次改动都把序列化后的正文同步过来（芯片按 @名称 算进正文） */
    function onComposerText(text: string) {
      inputValue.value = text;
    }

    // ==================== 沙箱 ====================
    const sandboxLoading = ref(false);
    // 工作区绝对根：与后端 WORKSPACE_ROOT 对齐；前端所有路径都以此开头，避免“无 path”传参。
    const WORKSPACE_ROOT = '/workspace';
    const currentPath = ref(WORKSPACE_ROOT);
    const sandboxItems = ref<api.SandboxItem[]>([]);
    const workspace = ref('');
    const uploading = ref(false);
    // 预览弹窗（功能1 改版）：渲染整体交给 SandboxPreviewModal，工作区栏只留文件列表；
    // 这里四个字段决定「开哪个文件」，取字节/按扩展名选渲染方式都在弹窗里做。
    const previewModal = reactive<PreviewTarget>({
      open: false,
      name: '',
      path: '',
      size: 0,
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
    // 离开页面即关所有 SSE，并阻止已安排的队列任务在组件销毁后继续发送。
    onUnmounted(() => {
      disposed = true;
      closeAllStreams();
    });

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
      const previousId = sessionId.value;
      if (previousId !== s.id) {
        composerMeta.set(previousId, {
          modelId: modelId.value || undefined,
          mounts: currentMounts(),
          orchAgent: orchAgent.value,
        });
        sessionDrafts.set(previousId, composerRef.value?.getHtml() ?? '');
      }
      sessionId.value = s.id;
      // 已加载过、或还有活动流的会话直接复用缓冲（后台流一直在往里写），不重载：
      // 重载会 buf.length=0 清空正被实时写入的 target，而 RUNNING 轮 Redis 不回放
      // → 旧增量丢、新帧又进不了重建出的空气泡（永远“AI 正在思考…”）
      if (!loadedSessions.has(s.id) && !hasLiveStream(s.id))
        await loadMessages(s.id);
      // 先拉齐候选再匹配名称（挂载回显要按 id 找 name）
      await ensurePickerOptions();
      // 恢复目标会话草稿；历史消息只能恢复模型和挂载，智能体草稿仅在当前页面会话内保留。
      const meta = composerMeta.get(s.id);
      modelId.value = meta?.modelId || 0;
      mountedItems.value = mountsToItems(meta?.mounts || {});
      orchAgent.value = meta?.orchAgent || null;
      applyComposerDraft(s.id);
      scrollToBottom();
      // 后台会话只保留队列；切回来时若已空闲，从队首继续。
      scheduleQuestionQueue(s.id);
    }

    /**
     * 还原某会话的输入框：有草稿就连文字带芯片原样还原（芯片位置以草稿为准），
     * 没草稿则清空后把该会话上轮的挂载铺成芯片，保证「框内芯片 == composer 状态」。
     */
    function applyComposerDraft(sid: number) {
      const draft = sessionDrafts.get(sid) ?? '';
      if (draft) {
        const chips = composerRef.value?.setHtml(draft) || [];
        mountedItems.value = chips
          .filter((chip) => chip.source !== 'agent')
          .map((chip) => ({
            key: chip.key,
            name: chip.name,
            source: chip.source as MountedItem['source'],
          }));
        const agentChip = chips.find((chip) => chip.source === 'agent');
        if (!agentChip) {
          orchAgent.value = null;
        } else if (
          !orchAgent.value ||
          agentKeyOf(orchAgent.value) !== agentChip.key
        ) {
          // 芯片在、但智能体对象要靠候选列表才能还原（授权被收回就当没选）
          orchAgent.value =
            agentOptions.value.find(
              (agent) => agentKeyOf(agent) === agentChip.key,
            ) || null;
        }
      } else {
        composerRef.value?.clear();
      }
      syncComposerChips();
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
            // INTERRUPTING（停止已下达、worker 还没收尾）按「已停」显示且不重新
            // 订阅：这一行此刻还没有终态内容，重连只会让页面又转起来
            if (row.status === 'INTERRUPTING') msg.error = '本轮已停止';
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
          [...rows].reverse().find((r) => r.role === 'ASSISTANT') ||
          rows.at(-1);
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
      // 保留当前会话草稿，再重置“新建”草稿会话，避免返回旧会话时配置丢失。
      if (sessionId.value !== 0) {
        composerMeta.set(sessionId.value, {
          modelId: modelId.value || undefined,
          mounts: currentMounts(),
          orchAgent: orchAgent.value,
        });
        sessionDrafts.set(sessionId.value, composerRef.value?.getHtml() ?? '');
      }
      sessionId.value = 0;
      sessionStore[0] = [];
      loadedSessions.delete(0);
      composerMeta.delete(0);
      sessionDrafts.delete(0);
      modelId.value = 0;
      mountedItems.value = [];
      orchAgent.value = null;
      // 框内同时有正文与芯片，光清 inputValue 不够，得把 contenteditable 一并抹干净
      composerRef.value?.clear();
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
        delete questionQueues[id];
        queuePumpPending.delete(id);
        loadedSessions.delete(id);
        composerMeta.delete(id);
        sessionDrafts.delete(id);
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

    /**
     * 切模型：纯前端 composer 状态，随每次发消息一并传给后端（不绑会话，可随时切换）。
     * 开了 allowClear 后 X 以 undefined 进来 → 等同清空本轮选择。
     */
    function changeModel(value?: number) {
      modelId.value = Number(value) || 0;
      // 已选智能体又手动指定模型：明确告知这会盖掉智能体里配好的模型
      if (orchAgent.value && modelId.value) {
        message.warning('选择该模型，将会覆盖智能体已配置的模型');
      }
    }

    // ==================== 对话（ARQ 投递 + SSE 订阅 + 前端提问队列） ====================

    function queueFor(sid: number): QueuedQuestion[] {
      if (!questionQueues[sid]) questionQueues[sid] = [];
      return questionQueues[sid];
    }

    async function handleSend() {
      const text = inputValue.value.trim();
      if (chatAttachments.value.some((a) => a.status === 'uploading')) {
        message.warning('还有文件在上传中，请稍候再发送');
        return;
      }
      const atts = readyAttachments();
      if (!text && atts.length === 0) return;
      // 选了智能体就可以不选模型（用智能体自带的）；纯深度探索没模型跑不了
      if (!modelId.value && !orchAgent.value) {
        message.warning('请先在下方选择一个已授权的模型');
        return;
      }
      const messageText = text || '请分析我上传的附件';
      const displayText = text || `已上传 ${atts.length} 个附件`;
      // 生成、停止收尾和请求投递期间都允许继续输入；发送键只入队，不再兼任停止键。
      if (busy.value || sending.value) {
        const queue = queueFor(sessionId.value);
        queueSeq += 1;
        queue.push({
          id: `question-${Date.now()}-${queueSeq}`,
          messageText,
          displayText,
          attachments: atts.map((item) => ({ ...item })),
          modelId: modelId.value || undefined,
          mounts: currentMounts(),
          orchAgent: orchAgent.value ? { ...orchAgent.value } : null,
        });
        chatAttachments.value = [];
        resetComposerText();
        return;
      }
      chatAttachments.value = []; // 发送即清草稿附件
      resetComposerText();
      await postTurn(messageText, displayText, atts);
    }

    /**
     * 发完收口：正文与芯片清空，但已选资源留给下一轮（芯片重新铺在空框开头）。
     * 不能让挂载默默生效而框里看不到——那又是一份“看不见但已勾上”的状态。
     */
    function resetComposerText() {
      inputValue.value = '';
      composerRef.value?.clear();
      syncComposerChips();
    }

    function removeQueuedQuestion(id: string) {
      const queue = queueFor(sessionId.value);
      const index = queue.findIndex((item) => item.id === id);
      if (index !== -1) queue.splice(index, 1);
      if (editingQueueId.value === id) cancelQueueEdit();
    }

    function clearQueuedQuestions() {
      questionQueues[sessionId.value] = [];
      cancelQueueEdit();
      draggedQueueId.value = '';
    }

    function startQueueEdit(item: QueuedQuestion) {
      editingQueueId.value = item.id;
      editingQueueText.value = item.messageText;
      nextTick(() => {
        const editor = document.querySelector<HTMLTextAreaElement>(
          'textarea.hermes-queue-edit',
        );
        editor?.focus();
        editor?.setSelectionRange(editor.value.length, editor.value.length);
      });
    }

    function cancelQueueEdit() {
      editingQueueId.value = '';
      editingQueueText.value = '';
    }

    function saveQueueEdit(item: QueuedQuestion) {
      const text = editingQueueText.value.trim();
      if (!text) {
        message.warning('排队问题不能为空');
        return;
      }
      item.messageText = text;
      item.displayText = text;
      cancelQueueEdit();
    }

    function onQueueDragStart(event: DragEvent, id: string) {
      draggedQueueId.value = id;
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', id);
      }
    }

    function onQueueDrop(targetId: string) {
      const sourceId = draggedQueueId.value;
      draggedQueueId.value = '';
      if (!sourceId || sourceId === targetId) return;
      const queue = queueFor(sessionId.value);
      const sourceIndex = queue.findIndex((item) => item.id === sourceId);
      const targetIndex = queue.findIndex((item) => item.id === targetId);
      if (sourceIndex === -1 || targetIndex === -1) return;
      const [moved] = queue.splice(sourceIndex, 1);
      if (moved) queue.splice(targetIndex, 0, moved);
    }

    /** 终态、断流或停止收尾后触发一次；同一会话只安排一个消费任务，避免并发发送。 */
    function scheduleQuestionQueue(sid: number) {
      if (
        disposed ||
        sid !== sessionId.value ||
        !questionQueues[sid]?.length ||
        queuePumpPending.has(sid)
      ) {
        return;
      }
      queuePumpPending.add(sid);
      setTimeout(() => {
        queuePumpPending.delete(sid);
        void pumpQuestionQueue(sid);
      }, 80);
    }

    async function pumpQuestionQueue(sid: number) {
      if (
        disposed ||
        sid !== sessionId.value ||
        sending.value ||
        bufFor(sid).some((item) => item.running || item.stopping)
      ) {
        return;
      }
      const queue = questionQueues[sid];
      const next = queue?.shift();
      if (!next) return;
      await postTurn(next.messageText, next.displayText, next.attachments, {
        ...next,
        sessionId: sid,
      });
    }

    /**
     * 输入框回车分流。
     *
     * 不能写成模板上的 @press-enter.prevent：ant-design-vue 的 TextArea 对
     * keyCode===13 无条件 emit pressEnter（不看 shift 键），.prevent 于是把
     * Shift+Enter 的原生换行一起吞了——换行键位整个失效。这里自己分流：
     * Shift+Enter 与输入法选词回车都放行给 textarea 原生长行，只有普通 Enter
     * 才拦下默认行为去发送。
     */
    function onComposerEnter(e: KeyboardEvent) {
      if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      handleSend();
    }

    /** 发一轮：messageText 给后端，displayText 给用户气泡（AskUser 走 JSON 时用可读文本回显），attachments 随消息传 */
    async function postTurn(
      messageText: string,
      displayText: string,
      attachments?: api.HarnessAttachment[],
      snapshot?: TurnSnapshot,
    ): Promise<boolean> {
      let sid = snapshot?.sessionId || sessionId.value;
      // 首条消息自动建会话：只落标题，模型与挂载随每次发消息传
      if (!sid) {
        try {
          const s = await api.HarnessSessionCreate({
            title: displayText.slice(0, 24) || '新探索',
          });
          sessionId.value = s.id;
          sid = s.id;
        } catch (error: any) {
          message.error(error?.message || '会话创建失败');
          return false;
        }
      }

      // 本轮由本屏发起、实时写入本会话缓冲：标为已加载，切走再回来不触发 DB 重载
      loadedSessions.add(sid);
      bufFor(sid).push({
        role: 'user',
        content: displayText,
        attachments:
          attachments && attachments.length ? attachments : undefined,
      });
      sending.value = true;
      if (sid === sessionId.value) scrollToBottom();
      try {
        const res = await api.HarnessChat({
          sessionId: sid,
          message: messageText,
          modelId: snapshot ? snapshot.modelId : modelId.value || undefined,
          mounts: snapshot?.mounts || currentMounts(),
          // 排队消息使用入队时的编排快照，不被之后的 composer 修改覆盖。
          orchMode: snapshot
            ? snapshot.orchAgent?.mode
            : orchAgent.value?.mode,
          orchConfigId: snapshot
            ? snapshot.orchAgent?.id
            : orchAgent.value?.id,
          attachments:
            attachments && attachments.length ? attachments : undefined,
        });
        void loadSessions(); // 刷新侧栏排序（updateTime 刚变过），不重选当前会话
        subscribeTurn(sid, res.turnId);
        return true;
      } catch (error: any) {
        bufFor(sid).push({
          role: 'assistant',
          content:
            error?.message || '请求失败，深度探索服务暂不可用，请稍后重试。',
        });
        if (sid === sessionId.value) scrollToBottom();
        return false;
      } finally {
        sending.value = false;
        // 请求失败没有终态帧，也要尝试下一条；仍在生成时 pump 会自行退出。
        scheduleQuestionQueue(sid);
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
      // 与 handleSend 同口径：选了智能体即可免选模型
      if (!modelId.value && !orchAgent.value) {
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

    /** 闸门纠正提醒（turn.nudge）：时间线上加一行系统提醒（仅实时流，刷新不回放） */
    function pushNudge(msg: ChatMsg, frame: api.HarnessFrame) {
      if (!msg.segments) msg.segments = [];
      msg.segments.push({
        kind: 'nudge',
        id: nextSegId(),
        text: unwrapReminder(frame.text || ''),
        nudge: frame.nudge,
        maxNudges: frame.maxNudges,
        unfinished: frame.unfinished,
      });
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
        // 停止收尾期间断流：发送口的锁只能在这里放掉，否则用户得干等到宽限期到点
        target.stopping = false;
        closeTurnStream(turnId);
        scheduleQuestionQueue(sid);
      };
      scrollToBottom();
    }

    /** 协议帧 → 消息条增量渲染（帧字段口径见 api.ts 的 HarnessFrame） */
    function applyFrame(
      target: ChatMsg,
      frame: api.HarnessFrame,
      turnId: string,
      sid: number,
    ) {
      const terminal =
        frame.type === 'turn.completed' ||
        frame.type === 'turn.failed' ||
        frame.type === 'turn.interrupted';
      // 已点停止：worker 收尾前可能还在发最后几帧增量，那些帧不再写进气泡——
      // 否则页面判停了还在往下长，看着就像没停住
      if (target.stopping && !terminal) return;
      // 终态帧到了就是收尾完成，解锁发送口（宽限期定时器由 closeTurnStream 收掉）
      if (terminal) target.stopping = false;
      switch (frame.type) {
        case 'text.delta': {
          appendText(target, frame.content || '');
          break;
        }
        case 'thinking.delta': {
          appendThinking(target, frame.content || '');
          break;
        }
        case 'tool.started': {
          startTool(target, frame.tool || '', frame.toolCallId);
          break;
        }
        case 'tool.completed': {
          finishTool(
            target,
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
          const had = (target.artifacts || []).length > 0;
          target.artifacts = list;
          if (!had && list.length > 0 && sessionId.value === sid) {
            sandboxCollapsed.value = false;
            const first = list[0];
            if (first) openPreview(first.path, first.name, 0);
          }
          break;
        }
        case 'turn.completed': {
          // 终态：收尾仍在进行的思考段；正文已由 text.delta 落到时间线，
          // 不再用整段 content 覆盖（会打乱与工具/思考交错的时序）
          settleThinking(target);
          target.running = false;
          closeTurnStream(turnId);
          break;
        }
        case 'turn.interrupted': {
          settleThinking(target);
          target.running = false;
          // HITL 挂起时优先显示智能体的问题（reason 是「等待答复」占位，
          // 回放路径下 question 已拼进 reason）
          target.error = frame.question || frame.reason || '本轮已中断';
          // AskUser 带结构化问题 → 初始化作答草稿，模板据此渲染选项表单
          if (frame.pendingQuestions && frame.pendingQuestions.length > 0) {
            target.pending = frame.pendingQuestions;
            const draft: Record<string, { other: string; selected: string[] }> =
              {};
            for (const q of frame.pendingQuestions) {
              draft[q.question] = { selected: [], other: '' };
            }
            target.answers = draft;
            target.answered = false;
          }
          closeTurnStream(turnId);
          break;
        }
        case 'turn.failed': {
          settleThinking(target);
          target.running = false;
          target.error = frame.error || '执行失败';
          closeTurnStream(turnId);
          break;
        }
        case 'turn.nudge': {
          // 自主性闸门压掉收尾、把它推回去继续干活：实时帧不落库也不回放，
          // 刷新后看到的是被推之后多干出来的那些工具行
          pushNudge(target, frame);
          break;
        }
        default:
          break; // turn.started 不渲染（气泡已建）
      }
      // AskUser 正在等用户作答时暂停队列，其余终态按当前顺序自动发下一条。
      if (
        terminal &&
        !(frame.type === 'turn.interrupted' && frame.pendingQuestions?.length)
      ) {
        scheduleQuestionQueue(sid);
      }
    }

    /** 关闭某一轮的订阅（终态/异常/切走只关这一条，别会话的后台流不受影响） */
    function closeTurnStream(turnId?: string) {
      if (!turnId) return;
      // 流一关就不会再收到那一轮的终态帧，宽限期定时器跟着收掉
      clearStopGuard(turnId);
      const st = turnStreams.get(turnId);
      if (st) {
        st.es.close();
        turnStreams.delete(turnId);
      }
    }

    /** 关所有在跑的流（离开页面用：组件卸载后回调再写各会话缓冲已无意义） */
    function closeAllStreams() {
      for (const turnId of [...turnStreams.keys()]) closeTurnStream(turnId);
      for (const turnId of [...stopGuards.keys()]) clearStopGuard(turnId);
    }

    /**
     * 点停止：命令下达即把气泡判停，但这条流不能抢着关。
     *
     * worker 收到停止命令后还要取消正在跑的那次调用、把已生成内容落库，然后
     * 才发终态帧——那帧到达（或断流、宽限期到点）才放开发送口。提前关流会让
     * 页面再也等不到终态，用户要么干转要么刷新看一轮空回答。
     */
    async function interruptTurn() {
      // 停止当前会话在跑的那一轮（别的会话后台流不受影响）
      const sid = sessionId.value;
      const hit = [...turnStreams].find(([, v]) => v.sessionId === sid);
      if (!hit) return;
      const turnId = hit[0];
      try {
        await api.HarnessInterrupt(turnId);
      } catch {
        // 命令没下达就去锁发送口，是拿一个没生效的停止把用户闲住
        message.error('停止请求失败，请重试');
        return;
      }
      const target = messages.value.find(
        (m) => m.turnId === turnId && m.running,
      );
      if (!target) {
        // 找不到对应气泡（别的标签页发起的轮、或刷新前那一轮已不在缓冲）：
        // 本地没有要等终态的状态，这条流直接关掉
        closeTurnStream(turnId);
        scheduleQuestionQueue(sid);
        return;
      }
      target.running = false;
      // 标成「正在收尾」：不转了，但发送口继续锁着，等终态帧放开
      target.stopping = true;
      settleThinking(target);
      // 兜底：worker 进程暴毙时终态帧永远不来，宽限期到点按已停处理放开发送口
      clearStopGuard(turnId);
      stopGuards.set(
        turnId,
        setTimeout(() => {
          stopGuards.delete(turnId);
          target.stopping = false;
          target.error = target.error || '停止收尾超时，请刷新查看本轮结果';
          closeTurnStream(turnId);
          scheduleQuestionQueue(sid);
        }, STOP_SETTLE_GUARD_MS),
      );
      message.info('已停止，正在收尾本轮已生成的内容');
    }

    function scrollToBottom() {
      nextTick(() => {
        const el = document.querySelector('.hermes-hd-list');
        if (el) el.scrollTop = el.scrollHeight;
      });
    }

    function removeMounted(target: MountedItem) {
      // 不同资源类型的 id 可能相同，必须按“类型 + id”删除，避免误删另一类挂载。
      mountedItems.value = mountedItems.value.filter(
        (i) => !(i.source === target.source && i.key === target.key),
      );
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

    // ==================== 智能体 picker（四类，单选）====================
    async function openAgentPicker() {
      agentPickerVisible.value = true;
      agentPickerLoading.value = true;
      agentPickKey.value = orchAgent.value ? agentKeyOf(orchAgent.value) : '';
      try {
        // 每次打开都刷新：use 授权可能刚被授予/收回，缓存的旧口径会误导选择
        agentOptions.value = (await api.HarnessOrchAgents()).items || [];
      } catch {
        agentOptions.value = [];
      } finally {
        agentPickerLoading.value = false;
      }
    }

    /** 单选：点中即选中，再点同一个取消；换智能体直接点另一个即可 */
    function toggleAgentPick(item: api.HarnessOrchAgent) {
      const key = agentKeyOf(item);
      agentPickKey.value = agentPickKey.value === key ? '' : key;
    }

    function confirmAgentPicker() {
      const next =
        agentOptions.value.find((a) => agentKeyOf(a) === agentPickKey.value) ||
        null;
      orchAgent.value = next;
      // 添加了智能体→模型选择自动置空（框里改显「智能体自带配置模型」）；
      // 用户再手动选则视为覆盖，见 changeModel 的提示
      if (next) modelId.value = 0;
      agentPickerVisible.value = false;
    }

    /** X 掉已选智能体：回到纯深度探索（本轮不再走任何编排运行时） */
    function clearAgent() {
      orchAgent.value = null;
    }

    // ==================== 沙箱文件逻辑 ====================
    async function loadSandboxFiles(dir = currentPath.value) {
      // 任何目录导航（返回/进入/面包屑/刷新）都先收起预览弹窗，回到文件列表
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
      openPreview(path, item.name, item.size);
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

    /** 删除工作区里的文件或目录（不可逆，模板里带 popconfirm 二次确认），删完重拉当前目录 */
    async function deleteItem(item: api.SandboxItem) {
      const path = `${currentPath.value || WORKSPACE_ROOT}/${item.name}`;
      try {
        await api.SandboxDelete(path);
        message.success(`已删除 ${item.name}`);
        // 正预览的就是被删的这个：弹窗里的内容已失效，直接关掉
        if (previewModal.open && previewModal.path === path) closePreview();
        await loadSandboxFiles(currentPath.value);
      } catch (error: any) {
        // 后端把「不存在 / 删除未生效 / 根目录不让删」写在 message 里，直接透出不吞成「删除失败」
        message.error(error?.message || '删除失败');
      }
    }

    /**
     * 打开预览弹窗：只登记目标文件（工作区卡片 / 输出物卡片 / 新一轮输出物自动预览都走这里），
     * 取字节与按扩展名选渲染方式（含 docx/xlsx/pptx 的纯前端解析）都在弹窗组件里做。
     */
    function openPreview(path: string, name: string, size = 0) {
      previewModal.path = path;
      previewModal.name = name;
      previewModal.size = size;
      previewModal.open = true;
    }

    /** 收起预览弹窗（返回列表 / 切目录 / 删掉正在看的文件时调用） */
    function closePreview() {
      previewModal.open = false;
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
          await uploadOneFile(entry, currentPath.value || WORKSPACE_ROOT, f)
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
      stopping,
      busy,
      mountedItems,
      toolLabel,
      toolArgsView,
      thinkingHint,
      nudgeHint,
      handleSend,
      interruptTurn,
      queuedQuestions,
      editingQueueId,
      editingQueueText,
      draggedQueueId,
      removeQueuedQuestion,
      clearQueuedQuestions,
      startQueueEdit,
      cancelQueueEdit,
      saveQueueEdit,
      onQueueDragStart,
      onQueueDrop,
      composerRef,
      onComposerKeydown,
      onComposerText,
      onChipRemoved,
      commandOpen,
      commandLoading,
      commandSearchRef,
      commandQuery,
      commandCategoryIndex,
      commandItemIndex,
      commandCategories,
      commandActiveCategory,
      commandActiveItems,
      resourceIcon,
      openCommandPalette,
      closeCommandPalette,
      confirmCommandPalette,
      focusCommandCategory,
      onCommandSearchKeydown,
      isCommandItemSelected,
      pickCommandItem,
      toggleCommandItem,
      selectedCommandCount,
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
      // 智能体 picker
      orchAgent,
      agentPickerVisible,
      agentPickerLoading,
      agentGroups,
      agentPickKey,
      agentKeyOf,
      agentModeLabel,
      openAgentPicker,
      toggleAgentPick,
      confirmAgentPicker,
      clearAgent,
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
      deleteItem,
      beforeUpload,
      uploading,
      previewModal,
      openPreview,
      closePreview,
      downloadByPath,
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
  <div
    class="hermes-page"
    :class="{ 'is-sandbox-collapsed': sandboxCollapsed }"
  >
    <!-- 左侧：会话列表 -->
    <aside class="hermes-side">
      <a-button block type="primary" class="hermes-new" @click="newSession">
        <template #icon><PlusOutlined /></template>
        新建探索
      </a-button>
      <div class="hermes-side-label">最近会话</div>
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

    <!-- 中部：深度探索 Harness 智能体对话框 -->
    <section class="hermes-chat">
      <header class="hermes-chat-header">
        <div class="hermes-logo">
          <RobotOutlined />
        </div>
        <div class="hermes-title">
          <h2>深度探索</h2>
          <p>Harness 智能体 · 描述问题，让 AI 在云端沙箱里执行</p>
        </div>
        <a-tooltip v-if="sandboxCollapsed" title="展开沙箱工作区">
          <a-button size="small" @click="toggleSandbox">
            <template #icon><DoubleLeftOutlined /></template>
            展开工作区
          </a-button>
        </a-tooltip>
      </header>

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
                  <span class="hermes-seg-label">{{
                    seg.active ? '思考中…' : '已完成思考'
                  }}</span>
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

              <!-- 闸门提醒：系统不让它提前收尾，已把它推回去继续清单里的下一项 -->
              <details
                v-else-if="seg.kind === 'nudge'"
                class="hermes-seg hermes-nudge"
              >
                <summary class="hermes-seg-summary">
                  <ExclamationCircleOutlined />
                  <span class="hermes-seg-label">系统提醒</span>
                  <span v-if="nudgeHint(seg)" class="hermes-seg-hint">{{
                    nudgeHint(seg)
                  }}</span>
                </summary>
                <pre v-if="seg.text" class="hermes-seg-body">
                  {{ seg.text }}
                </pre>
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
                !busy &&
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
                      checked:
                        msg.answers?.[q.question]?.selected.includes(opt.label) ||
                        false,
                    }"
                  >
                    <input
                      :type="q.multiSelect ? 'checkbox' : 'radio'"
                      :name="`ask-${idx}-${q.question}`"
                      :checked="
                        msg.answers?.[q.question]?.selected.includes(opt.label) ||
                        false
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
                  :value="msg.answers?.[q.question]?.other || ''"
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
          <div class="hermes-empty-mark">
            <RobotOutlined />
          </div>
          <h3>今天想探索什么</h3>
          <p>我是 Harness 智能体。可以分析数据、处理文件、组织任务。</p>
          <div class="hermes-empty-tip">
            试着说：帮我统计 sample_data.csv 里的访问量趋势
          </div>
        </div>
      </div>

      <!-- 前端提问队列：回复终态后按此处顺序自动消费，仅保留在当前页面内存中。 -->
      <section
        v-if="queuedQuestions.length > 0"
        class="hermes-queue"
        aria-label="排队问题列表"
      >
        <header class="hermes-queue-header">
          <div>
            <HolderOutlined />
            <strong>排队</strong>
            <span>{{ queuedQuestions.length }} 条消息排队中</span>
          </div>
          <button type="button" class="hermes-queue-clear" @click="clearQueuedQuestions">
            清空
          </button>
        </header>
        <div class="hermes-queue-list">
          <article
            v-for="(item, index) in queuedQuestions"
            :key="item.id"
            class="hermes-queue-item"
            :class="{ 'is-dragging': draggedQueueId === item.id }"
            @dragover.prevent
            @drop.prevent="onQueueDrop(item.id)"
          >
            <button
              type="button"
              class="hermes-queue-drag"
              draggable="true"
              :aria-label="`拖拽排序第 ${index + 1} 条问题`"
              title="拖拽调整顺序"
              @dragstart="onQueueDragStart($event, item.id)"
              @dragend="draggedQueueId = ''"
            >
              <HolderOutlined />
            </button>
            <span class="hermes-queue-index">{{ index + 1 }}</span>
            <div class="hermes-queue-content">
              <div v-if="editingQueueId === item.id" class="hermes-queue-editing">
                <a-textarea
                  v-model:value="editingQueueText"
                  class="hermes-queue-edit"
                  :auto-size="{ minRows: 1, maxRows: 4 }"
                  aria-label="编辑排队问题"
                  @keydown.enter.exact.prevent="saveQueueEdit(item)"
                  @keydown.esc.prevent="cancelQueueEdit"
                />
                <button type="button" aria-label="保存问题修改" @click="saveQueueEdit(item)">
                  <CheckOutlined />
                </button>
                <button type="button" aria-label="取消问题修改" @click="cancelQueueEdit">
                  <CloseOutlined />
                </button>
              </div>
              <template v-else>
                <div
                  class="hermes-queue-text"
                  :title="`${item.displayText}\n双击编辑`"
                  @dblclick="startQueueEdit(item)"
                >
                  {{ item.displayText }}
                </div>
                <small v-if="item.attachments.length > 0">
                  <PaperClipOutlined /> {{ item.attachments.length }} 个附件
                </small>
              </template>
            </div>
            <div v-if="editingQueueId !== item.id" class="hermes-queue-actions">
              <button type="button" :aria-label="`编辑第 ${index + 1} 条问题`" @click="startQueueEdit(item)">
                <EditOutlined />
              </button>
              <button
                type="button"
                class="is-danger"
                :aria-label="`删除第 ${index + 1} 条问题`"
                @click="removeQueuedQuestion(item.id)"
              >
                <DeleteOutlined />
              </button>
            </div>
          </article>
        </div>
        <footer class="hermes-queue-hint">
          <span>拖拽调整顺序，双击或点击编辑修改</span>
          <span>当前回复结束后自动发送</span>
        </footer>
      </section>

      <!-- 输入区：资源、正文与发送操作集中在一张对话卡片内 -->
      <footer class="hermes-input" :class="{ 'is-command-open': commandOpen }">
        <!-- 分类横向并排：选中一类只展开那一类的列表，不再把五类堆叠在一起顶掉消息区 -->
        <div
          v-if="commandOpen"
          class="hermes-command-panel"
          role="dialog"
          aria-label="添加智能体与资源"
        >
          <header class="hermes-command-header">
            <div>
              <strong>添加智能体与资源</strong>
              <span>勾选后以图标插进正文，跟文字写在同一段里</span>
            </div>
            <div class="hermes-command-header-right">
              <kbd>/</kbd><kbd>@</kbd>
              <button
                type="button"
                tabindex="-1"
                aria-label="关闭资源选择"
                @click="closeCommandPalette"
              >
                <CloseOutlined />
              </button>
            </div>
          </header>
          <div v-if="commandLoading" class="hermes-command-loading">
            <a-spin size="small" /> 正在加载可用资源…
          </div>
          <template v-else>
            <nav
              class="hermes-command-tabs"
              aria-label="资源分类"
              @mousedown.prevent
            >
              <button
                v-for="(category, categoryIndex) in commandCategories"
                :key="category.source"
                type="button"
                tabindex="-1"
                class="hermes-command-tab"
                :class="{ active: commandCategoryIndex === categoryIndex }"
                @click="focusCommandCategory(categoryIndex)"
              >
                <component :is="category.icon" />
                <span>{{ category.label }}</span>
                <small v-if="selectedCommandCount(category.source)">
                  {{ selectedCommandCount(category.source) }}
                </small>
              </button>
            </nav>
            <div class="hermes-command-search">
              <SearchOutlined class="hermes-command-search-icon" />
              <input
                ref="commandSearchRef"
                v-model="commandQuery"
                class="hermes-command-search-input"
                type="text"
                :placeholder="`搜索${commandActiveCategory?.label || '资源'}名称`"
                aria-label="按名称搜索资源"
                @keydown="onCommandSearchKeydown"
              />
              <button
                v-if="commandQuery"
                type="button"
                tabindex="-1"
                aria-label="清空搜索"
                @click="commandQuery = ''"
              >
                <CloseOutlined />
              </button>
            </div>
            <div class="hermes-command-list" @mousedown.prevent>
              <button
                v-for="(item, itemIndex) in commandActiveItems"
                :key="`${item.source}:${item.key}`"
                type="button"
                tabindex="-1"
                class="hermes-command-item"
                :class="{
                  active: commandItemIndex === itemIndex,
                  selected: isCommandItemSelected(item),
                }"
                :aria-pressed="isCommandItemSelected(item)"
                @click="pickCommandItem(itemIndex, item)"
              >
                <component
                  :is="resourceIcon(item.source)"
                  class="hermes-command-item-icon"
                />
                <span class="hermes-command-item-name">{{ item.name }}</span>
                <small v-if="item.description" class="hermes-command-item-desc">
                  {{ item.description }}
                </small>
                <CheckOutlined
                  v-if="isCommandItemSelected(item)"
                  class="hermes-command-item-checked"
                />
              </button>
              <div
                v-if="commandActiveItems.length === 0"
                class="hermes-command-empty"
              >
                {{
                  commandQuery.trim()
                    ? '没有匹配的资源，换个词试试'
                    : '暂无可用资源（需本人创建或被授权使用）'
                }}
              </div>
            </div>
          </template>
          <footer class="hermes-command-footer">
            <span>
              <kbd>←</kbd><kbd>→</kbd> 切分类 <kbd>↑</kbd><kbd>↓</kbd> 选条目
              <kbd>Space</kbd> 勾选
            </span>
            <a-button
              type="primary"
              size="small"
              tabindex="-1"
              @click="confirmCommandPalette"
            >
              完成 <kbd>Enter</kbd>
            </a-button>
          </footer>
        </div>

        <div class="hermes-editor">
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
                <span class="hermes-attach-name" :title="a.name">{{
                  a.name
                }}</span>
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

          <!-- 资源芯片与正文穿插在同一行：图标不再单独占顶部一行去顶消息区 -->
          <ComposerEditor
            ref="composerRef"
            :placeholder="
              busy || sending
                ? '随时继续提问，Enter 加入排队（Shift+Enter 换行）…'
                : '描述你的问题，输入 / 或 @ 把智能体与资源插进正文（Shift+Enter 换行）…'
            "
            aria-label="探索问题输入框"
            @update:text="onComposerText"
            @remove="onChipRemoved"
            @keydown="onComposerKeydown"
          />
        </div>

        <div class="hermes-input-tools">
          <div class="hermes-input-actions">
            <button
              class="hermes-resource-trigger"
              type="button"
              @click="openCommandPalette()"
            >
              <PlusOutlined /> 资源
            </button>
            <a-upload
              :before-upload="onChatAttachPick"
              :multiple="true"
              :show-upload-list="false"
            >
              <button class="hermes-attach-trigger" type="button">
                <PaperClipOutlined /> 附件
              </button>
            </a-upload>
            <span class="hermes-shortcut-hint"><kbd>/</kbd> 或 <kbd>@</kbd> 快速选择</span>
          </div>
          <div class="hermes-input-meta">
            <a-select
              class="hermes-model-select"
              size="small"
              :options="modelSelectOptions"
              :placeholder="
                orchAgent && !modelId ? '智能体自带配置模型' : '选择模型'
              "
              allow-clear
              placement="topLeft"
              :value="modelId || undefined"
              @change="changeModel"
            />
            <span v-if="stopping" class="hermes-stopping-label">正在收尾</span>
            <a-tooltip v-if="streaming" title="停止当前回复，排队问题将继续">
              <a-button
                class="hermes-stop"
                aria-label="停止当前回复"
                @click="interruptTurn"
              >
                <template #icon><StopOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-button
              type="primary"
              class="hermes-send"
              :aria-label="busy || sending ? '加入排队' : '发送'"
              :title="busy || sending ? '加入排队' : '发送'"
              @click="handleSend"
            >
              <template #icon><SendOutlined /></template>
            </a-button>
          </div>
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

      <!-- 文件列表（预览已搬到独立弹窗 SandboxPreviewModal，栏内不再画内容） -->
      <div class="hermes-fs" :class="{ loading: sandboxLoading }">
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
                <a-tooltip
                  :title="
                    item.type === 'dir'
                      ? '删除文件夹（连同里面的内容）'
                      : '删除文件'
                  "
                >
                  <!-- 卡片整块绑了 dblclick 进入/预览，这两个修饰符不挡就会误发导航 -->
                  <span class="hermes-fs-del" @click.stop @dblclick.stop>
                    <a-popconfirm
                      cancel-text="取消"
                      ok-text="删除"
                      :ok-button-props="{ danger: true }"
                      :title="
                        item.type === 'dir'
                          ? `删除「${item.name}」及其中全部文件？删了不可恢复。`
                          : `删除「${item.name}」？删了不可恢复。`
                      "
                      @confirm="deleteItem(item)"
                    >
                      <a-button danger size="small" type="link">
                        <template #icon><DeleteOutlined /></template>
                      </a-button>
                    </a-popconfirm>
                  </span>
                </a-tooltip>
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

    <!-- 智能体选择弹窗：按 单/团队/目标/SOP 分组，组内跨组都只能选一个 -->
    <a-modal
      v-model:open="agentPickerVisible"
      title="选择智能体（只能选一个）"
      width="560px"
      @ok="confirmAgentPicker"
    >
      <div v-if="agentPickerLoading" class="hermes-picker-loading">
        <a-spin />
      </div>
      <div v-else class="hermes-agent-groups">
        <div v-for="g in agentGroups" :key="g.mode" class="hermes-agent-group">
          <div class="hermes-agent-group-title">{{ g.label }}</div>
          <div
            v-for="a in g.items"
            :key="agentKeyOf(a)"
            class="hermes-picker-item"
            :class="{ active: agentPickKey === agentKeyOf(a) }"
            @click="toggleAgentPick(a)"
          >
            <b>{{ a.name }}</b>
            <span>{{ a.purpose || '未填写用途' }}</span>
          </div>
          <div v-if="g.items.length === 0" class="hermes-agent-group-empty">
            暂无可用（需本人创建或被授权使用）
          </div>
        </div>
      </div>
    </a-modal>

    <!-- 沙箱文件预览弹窗（输出物卡片与工作区文件共用一份，内容按扩展名选渲染方式） -->
    <SandboxPreviewModal
      v-model:open="previewModal.open"
      :name="previewModal.name"
      :path="previewModal.path"
      :size="previewModal.size"
    />
  </div>
</template>

<style lang="less" scoped>
.hermes-page {
  display: flex;
  gap: 14px;
  height: calc(100vh - 132px);
  padding: 16px;
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

/* ==================== 沙箱预览 ====================
   渲染样式已随预览内容搬进 SandboxPreviewModal.vue（弹窗自己带 scoped 样式），
   这一栏只负责文件列表。 */

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
  border: 1px solid var(--border-color, rgb(15 23 42 / 6%));
  border-radius: 18px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 10px 30px rgb(15 23 42 / 4%);
  overflow: hidden;

  .hermes-chat-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 18px 14px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);
    background:
      radial-gradient(420px 120px at 0% 0%, rgb(99 102 241 / 14%), transparent 70%),
      var(--layout-header-bg-color, #fff);

    .hermes-logo {
      width: 42px;
      height: 42px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 14px;
      color: #fff;
      font-size: 20px;
      background: linear-gradient(145deg, #4f46e5, #7c3aed);
      box-shadow: 0 8px 18px rgb(79 70 229 / 28%);
    }

    .hermes-title {
      flex: 1;
      min-width: 0;
      h2 {
        margin: 0;
        font-size: 17px;
        font-weight: 650;
        letter-spacing: -0.01em;
        color: var(--text-color-1, #1f2329);
      }
      p {
        margin: 3px 0 0;
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
    margin: 12px 18px 0;
    padding: 0;
    min-height: 0;
    border: none;
    border-radius: 0;
    background: transparent;

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
          background: linear-gradient(180deg, #4f46e5, #4338ca);
          color: #fff;
          border-radius: 16px 16px 6px 16px;
          box-shadow: 0 8px 18px rgb(67 56 202 / 18%);
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
          background: var(--hover-bg-color, #f7f8fb);
          border: 1px solid var(--border-color, #eef0f4);
          border-radius: 6px 16px 16px 16px;
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
          background: linear-gradient(145deg, #4f46e5, #7c3aed);
        }
        &.user {
          color: #fff;
          background: var(--primary-color, #1677ff);
          font-size: 12px;
        }
      }

      .hermes-bubble {
        max-width: 78%;
        padding: 10px 14px;
        font-size: 14px;
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
      padding: 24px;
      text-align: center;
      color: var(--text-color-3, #8a9099);

      .hermes-empty-mark {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 64px;
        height: 64px;
        border-radius: 20px;
        color: #fff;
        font-size: 28px;
        background: linear-gradient(145deg, #4f46e5, #7c3aed);
        box-shadow: 0 16px 32px rgb(79 70 229 / 24%);
      }

      h3 {
        margin: 16px 0 0;
        font-size: 20px;
        font-weight: 650;
        letter-spacing: -0.02em;
        color: var(--text-color-1, #1f2329);
      }

      p {
        max-width: 360px;
        margin: 8px 0 0;
        font-size: 13px;
        line-height: 1.6;
      }

      .hermes-empty-tip {
        margin-top: 16px;
        padding: 8px 12px;
        border-radius: 999px;
        background: var(--hover-bg-color, #f4f6fb);
        font-size: 12px;
        color: var(--text-color-2, #4e5969);
      }
    }
  }

  .hermes-queue {
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    max-height: 228px;
    margin: 6px 16px 0;
    padding: 10px 12px 8px;
    background: var(--component-background, #fff);
    border: 1px solid var(--border-color, #e3e7ee);
    border-radius: 16px;
    box-shadow: 0 6px 22px rgb(15 23 42 / 5%);

    .hermes-queue-header {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: space-between;
      min-height: 26px;
      padding: 0 2px 7px;

      > div {
        display: flex;
        gap: 8px;
        align-items: center;
        min-width: 0;
        color: var(--text-color-2, #4e5969);
      }

      strong {
        font-size: 13px;
        font-weight: 600;
        color: var(--text-color-1, #1f2329);
      }

      span {
        font-size: 12px;
        color: var(--text-color-3, #8a9099);
      }
    }

    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      color: var(--text-color-3, #7b8492);
      cursor: pointer;
      background: transparent;
      border: 0;
      border-radius: 6px;

      &:hover {
        color: hsl(var(--primary));
        background: hsl(var(--primary) / 7%);
      }
    }

    .hermes-queue-clear {
      min-width: 38px;
      height: 26px;
      font-size: 12px;
    }

    .hermes-queue-list {
      min-height: 0;
      overflow-y: auto;
      border-top: 1px solid var(--border-color, #f0f2f5);
    }

    .hermes-queue-item {
      display: flex;
      gap: 8px;
      align-items: center;
      min-height: 43px;
      padding: 5px 0;
      background: var(--component-background, #fff);
      border-bottom: 1px solid var(--border-color, #f3f4f6);
      transition: background-color 0.15s ease;

      &:last-child {
        border-bottom: 0;
      }

      &:hover {
        background: var(--hover-bg-color, #fafbfc);
      }

      &.is-dragging {
        opacity: 0.45;
      }
    }

    .hermes-queue-drag {
      flex-shrink: 0;
      width: 22px;
      height: 30px;
      cursor: grab;

      &:active {
        cursor: grabbing;
      }
    }

    .hermes-queue-index {
      display: inline-flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      font-size: 11px;
      color: var(--text-color-3, #8a9099);
      background: var(--hover-bg-color, #f2f4f7);
      border-radius: 6px;
    }

    .hermes-queue-content {
      flex: 1;
      min-width: 0;

      small {
        display: flex;
        gap: 4px;
        align-items: center;
        margin-top: 2px;
        font-size: 11px;
        color: var(--text-color-3, #8a9099);
      }
    }

    .hermes-queue-text {
      overflow: hidden;
      font-size: 13px;
      line-height: 1.6;
      color: var(--text-color-1, #303642);
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .hermes-queue-actions {
      display: flex;
      flex-shrink: 0;
      gap: 3px;

      button {
        width: 28px;
        height: 28px;

        &.is-danger:hover {
          color: var(--error-color, #ef4444);
          background: var(--error-color-bg, #fef2f2);
        }
      }
    }

    .hermes-queue-editing {
      display: flex;
      gap: 5px;
      align-items: flex-start;

      .hermes-queue-edit {
        flex: 1;
        min-width: 0;
      }

      > button {
        flex-shrink: 0;
        width: 28px;
        height: 28px;
        margin-top: 1px;
      }

      :deep(textarea.ant-input) {
        padding: 4px 8px;
        font-size: 13px;
        line-height: 1.5;
      }
    }

    .hermes-queue-hint {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: space-between;
      padding: 7px 2px 0;
      font-size: 11px;
      color: var(--text-color-3, #9aa0aa);
      border-top: 1px solid var(--border-color, #f0f2f5);
    }

    @media (width < 640px) {
      margin-right: 10px;
      margin-left: 10px;

      .hermes-queue-hint > span:first-child {
        display: none;
      }

      .hermes-queue-hint {
        justify-content: flex-end;
      }
    }
  }

  .hermes-input {
    position: relative;
    flex-shrink: 0;
    margin: 6px 16px 16px;
    padding: 0 14px 10px;
    background: var(--component-background, #fff);
    border: 1px solid var(--border-color, #e3e7ee);
    border-radius: 20px;
    box-shadow: 0 8px 28px rgb(15 23 42 / 7%);
    transition:
      border-color 0.18s ease,
      box-shadow 0.18s ease;

    &:focus-within,
    &.is-command-open {
      border-color: hsl(var(--primary) / 45%);
      box-shadow: 0 10px 32px rgb(15 23 42 / 9%);
    }

    kbd {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 20px;
      height: 20px;
      padding: 0 5px;
      font-family: inherit;
      font-size: 11px;
      line-height: 1;
      color: var(--text-color-2, #4e5969);
      background: var(--hover-bg-color, #f5f7fa);
      border: 1px solid var(--border-color, #e5e8ef);
      border-radius: 5px;
      box-shadow: 0 1px 0 rgb(15 23 42 / 5%);
    }

    .hermes-command-panel {
      position: absolute;
      right: 0;
      bottom: calc(100% + 10px);
      left: 0;
      z-index: 20;
      overflow: hidden;
      background: var(--component-background, #fff);
      border: 1px solid var(--border-color, #e2e7ef);
      border-radius: 16px;
      box-shadow: 0 18px 52px rgb(15 23 42 / 16%);
    }

    .hermes-command-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 58px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border-color, #edf0f4);

      > div:first-child {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }

      strong {
        font-size: 14px;
        font-weight: 600;
        color: var(--text-color-1, #1f2329);
      }

      span {
        font-size: 11px;
        color: var(--text-color-3, #8a9099);
      }

      .hermes-command-header-right {
        display: flex;
        gap: 5px;
        align-items: center;

        > button {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 28px;
          margin-left: 4px;
          color: var(--text-color-3, #8a9099);
          cursor: pointer;
          background: transparent;
          border: 0;
          border-radius: 7px;

          &:hover {
            color: var(--text-color-1, #1f2329);
            background: var(--hover-bg-color, #f5f7fa);
          }
        }
      }
    }

    .hermes-command-loading {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: center;
      min-height: 250px;
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
    }

    // 分类横向并排；←→ 就是在这一排上跳
    .hermes-command-tabs {
      display: flex;
      gap: 6px;
      padding: 10px 12px 0;
      overflow-x: auto;

      .hermes-command-tab {
        display: inline-flex;
        flex: 0 0 auto;
        gap: 6px;
        align-items: center;
        min-height: 32px;
        padding: 5px 11px;
        font-size: 13px;
        color: var(--text-color-2, #4e5969);
        white-space: nowrap;
        cursor: pointer;
        background: var(--hover-bg-color, #f6f8fb);
        border: 1px solid transparent;
        border-radius: 9px;

        > .anticon {
          color: var(--text-color-3, #8a9099);
        }

        small {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 17px;
          height: 17px;
          font-size: 11px;
          color: hsl(var(--primary));
          background: hsl(var(--primary) / 10%);
          border-radius: 9px;
        }

        &:hover {
          color: hsl(var(--primary));
        }

        &.active {
          font-weight: 500;
          color: hsl(var(--primary));
          background: hsl(var(--primary) / 8%);
          border-color: hsl(var(--primary) / 22%);

          > .anticon {
            color: hsl(var(--primary));
          }
        }
      }
    }

    .hermes-command-search {
      display: flex;
      gap: 7px;
      align-items: center;
      margin: 10px 12px 0;
      padding: 0 10px;
      background: var(--hover-bg-color, #f6f8fb);
      border: 1px solid var(--border-color, #e5e8ee);
      border-radius: 9px;

      &:focus-within {
        background: var(--component-background, #fff);
        border-color: hsl(var(--primary) / 45%);
      }

      > .hermes-command-search-icon {
        font-size: 13px;
        color: var(--text-color-3, #9aa0aa);
      }

      .hermes-command-search-input {
        flex: 1;
        min-width: 0;
        height: 32px;
        font-size: 13px;
        color: var(--text-color-1, #1f2329);
        background: transparent;
        border: 0;
        outline: none;

        &::placeholder {
          color: var(--text-color-3, #a0a6b0);
        }
      }

      > button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        height: 20px;
        padding: 0;
        color: var(--text-color-3, #9aa0aa);
        cursor: pointer;
        background: transparent;
        border: 0;
        border-radius: 5px;

        .anticon {
          font-size: 10px;
        }

        &:hover {
          color: var(--text-color-1, #1f2329);
        }
      }
    }

    // 只出当前分类的条目：一条一行，名称与用途同排读完
    .hermes-command-list {
      display: flex;
      flex-direction: column;
      gap: 3px;
      // 固定高度而非 max-height：某类条目少时也不塌下去，左右切分类面板
      // 长宽恒定，不会因为数据多少来回缩放晃眼。
      height: 232px;
      padding: 8px 8px 10px;
      overflow-y: auto;
    }

    .hermes-command-item {
      display: flex;
      gap: 8px;
      align-items: center;
      width: 100%;
      min-height: 34px;
      padding: 6px 9px;
      font-size: 13px;
      color: var(--text-color-2, #4e5969);
      text-align: left;
      cursor: pointer;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 9px;

      .hermes-command-item-icon {
        flex-shrink: 0;
        color: hsl(var(--primary));
      }

      .hermes-command-item-name {
        flex: 0 1 auto;
        overflow: hidden;
        font-weight: 500;
        color: var(--text-color-1, #1f2329);
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .hermes-command-item-desc {
        flex: 1;
        min-width: 0;
        overflow: hidden;
        font-size: 11px;
        color: var(--text-color-3, #9aa0aa);
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .hermes-command-item-checked {
        flex-shrink: 0;
        font-size: 12px;
        color: hsl(var(--primary));
      }

      &.selected {
        background: hsl(var(--primary) / 7%);
        border-color: hsl(var(--primary) / 22%);
      }

      &.active {
        border-color: hsl(var(--primary) / 55%);
        outline: 2px solid hsl(var(--primary) / 13%);
        outline-offset: 1px;
      }
    }

    .hermes-command-empty {
      padding: 18px 4px;
      font-size: 12px;
      color: var(--text-color-3, #9aa0aa);
      text-align: center;
    }

    .hermes-command-footer {
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      min-height: 50px;
      padding: 8px 12px;
      background: var(--hover-bg-color, #fafbfc);
      border-top: 1px solid var(--border-color, #edf0f4);

      > span {
        display: flex;
        gap: 4px;
        align-items: center;
        font-size: 11px;
        color: var(--text-color-3, #8a9099);
      }

      .ant-btn kbd {
        height: 17px;
        margin-left: 3px;
        color: inherit;
        background: rgb(255 255 255 / 14%);
        border-color: rgb(255 255 255 / 28%);
        box-shadow: none;
      }
    }

    .hermes-editor {
      padding: 12px 2px 4px;
    }

    // 待发送附件列表：每项一行（回形针 + 文件名/进度条 + 删除）
    .hermes-attach-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-bottom: 8px;

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

    .hermes-input-tools {
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      min-height: 40px;
      padding-top: 2px;

      .hermes-input-actions,
      .hermes-input-meta {
        display: flex;
        gap: 6px;
        align-items: center;
        min-width: 0;
      }

      .hermes-input-actions {
        flex: 1 1 auto;
      }

      .hermes-input-meta {
        flex: 0 0 auto;
        gap: 8px;
        justify-content: flex-end;
        margin-left: auto;
      }

      .hermes-resource-trigger,
      .hermes-attach-trigger {
        display: inline-flex;
        gap: 5px;
        align-items: center;
        justify-content: center;
        min-height: 32px;
        padding: 0 8px;
        font-size: 12px;
        color: var(--text-color-2, #4e5969);
        cursor: pointer;
        background: transparent;
        border: 0;
        border-radius: 8px;
        transition:
          color 0.16s ease,
          background-color 0.16s ease;

        &:hover {
          color: hsl(var(--primary));
          background: hsl(var(--primary) / 7%);
        }
      }

      .hermes-resource-trigger {
        color: hsl(var(--primary));
      }

      .hermes-shortcut-hint {
        display: inline-flex;
        gap: 4px;
        align-items: center;
        margin-left: 4px;
        font-size: 11px;
        color: var(--text-color-3, #9aa0aa);
        white-space: nowrap;

        kbd {
          min-width: 18px;
          height: 18px;
          padding: 0 4px;
        }
      }

      .hermes-model-select {
        width: 190px;
        flex-shrink: 0;
      }

      .hermes-send,
      .hermes-stop {
        display: inline-flex;
        flex-shrink: 0;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        padding: 0;
        border-radius: 50%;
      }

      .hermes-send {
        border: none;
        box-shadow: 0 5px 14px hsl(var(--primary) / 22%);
      }

      .hermes-stop {
        color: var(--text-color-2, #4e5969);
        background: var(--hover-bg-color, #f2f4f7);
        border: 1px solid var(--border-color, #e3e7ee);
      }

      .hermes-stopping-label {
        font-size: 11px;
        color: var(--text-color-3, #8a9099);
        white-space: nowrap;
      }
    }

    @media (width < 1280px) {
      .hermes-shortcut-hint {
        display: none;
      }
    }

    @media (width < 640px) {
      margin-right: 10px;
      margin-left: 10px;
      padding-right: 10px;
      padding-left: 10px;

      .hermes-command-footer > span {
        display: none;
      }

      .hermes-command-footer {
        justify-content: flex-end;
      }

      .hermes-input-tools {
        .hermes-model-select {
          width: 150px;
        }

        .hermes-attach-trigger {
          font-size: 0;

          .anticon {
            font-size: 13px;
          }
        }
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
  border: 1px solid var(--border-color, rgb(15 23 42 / 6%));
  border-radius: 18px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 10px 30px rgb(15 23 42 / 4%);
  overflow: hidden;

  .hermes-sandbox-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-color, #f0f0f0);
    background: var(--layout-header-bg-color, #fff);

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
        // 卡片最窄只 128px，多一个删除按钮后一行放不下，换行比挤成一团好
        flex-wrap: wrap;

        // 删除外层包了 tooltip + popconfirm 两层，样式在这一层上把它接回同一行
        .hermes-fs-del {
          display: inline-flex;
          align-items: center;
        }
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

// 智能体弹框：四类各一节，候选多时组内整体滚动，标题不参与滚动之外的布局挤压
.hermes-agent-groups {
  max-height: 420px;
  overflow-y: auto;

  .hermes-agent-group {
    margin-bottom: 12px;
  }

  .hermes-agent-group-title {
    margin-bottom: 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-color-2, #4e5969);
  }

  .hermes-agent-group-empty {
    padding: 2px 12px 6px;
    font-size: 12px;
    color: var(--text-color-3, #b0b6bf);
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
  flex: 0 0 220px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 12px;
  border: 1px solid var(--border-color, rgb(15 23 42 / 6%));
  border-radius: 18px;
  background: var(--layout-header-bg-color, #fff);
  box-shadow: 0 10px 30px rgb(15 23 42 / 4%);
  overflow: hidden;

  .hermes-new {
    height: 36px;
    border: none;
    border-radius: 12px;
    background: linear-gradient(180deg, #4f46e5, #4338ca);
    box-shadow: 0 8px 16px rgb(67 56 202 / 22%);
  }

  .hermes-side-label {
    padding: 2px 8px 0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--text-color-3, #8a9099);
  }

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
      background: rgb(79 70 229 / 8%);

      .hermes-side-title {
        color: #4338ca;
        font-weight: 600;
      }
    }

    .hermes-side-title {
      flex: 1;
      min-width: 0;
      font-size: 13px;
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

    // 思考态标签（【思考中】【已完成思考】）必须单行展示：summary 是 flex 布局，
    // 而中文文本的 min-content 宽度只有 1 个字，子项默认 flex-shrink:1 时被 hint
    // 挤窄就会拆成“思考”一行、“中”一行，所以这里锁死不收缩 + 禁止换行
    .hermes-seg-label {
      flex: none;
      white-space: nowrap;
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

  // 闸门提醒：琥珀色区别于思考（蓝）/工具（紫），它是系统行为不是模型输出
  .hermes-nudge .hermes-seg-summary {
    color: #d97706;
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
