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
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextFilled,
  FolderFilled,
  LoadingOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { getTraceId } from '#/api/request';
import * as api from './api';

/** 时间线片段：assistant 消息按到达顺序拆成思考/工具/文本三类块，各自可折叠 */
type Segment =
  | { kind: 'thinking'; id: string; text: string; active?: boolean }
  | { kind: 'text'; id: string; text: string }
  | { kind: 'tool'; id: string; tool: string; arguments: string; done: boolean };

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
}

/** 已挂载条目 */
interface MountedItem {
  key: string;
  name: string;
  source: 'kb' | 'mcp' | 'skill' | 'tool';
}

export default defineComponent({
  name: 'HernesExplorerPage',
  components: {
    ArrowLeftOutlined,
    BulbOutlined,
    CheckCircleOutlined,
    CloudDownloadOutlined,
    CloudUploadOutlined,
    DeleteOutlined,
    FileTextFilled,
    FolderFilled,
    LoadingOutlined,
    MoreOutlined,
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
    const messages = ref<ChatMsg[]>([]);
    const inputValue = ref('');
    const sending = ref(false);
    const streaming = ref(false);
    const mountedItems = ref<MountedItem[]>([]);
    /** SSE 连接（普通变量即可：同一时刻只允许一条流写本屏） */
    let eventSource: null | EventSource = null;
    let activeTurnId = '';

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
    const fileVisible = ref(false);
    const fileData = ref<api.SandboxFileRep | null>(null);

    // ==================== 初始化 ====================
    onMounted(() => {
      loadSessions(true);
      loadModels();
      loadSandboxFiles();
    });
    // 离开页面即关 SSE（组件卸载后回调里再写 messages 已无意义）
    onUnmounted(() => closeStream());

    // ==================== 对话框逻辑 ====================
    /** 轻量 markdown 渲染：加粗 / 引用 / 无序列表 / 换行 */
    function renderMarkdown(text: string): string {
      const esc = text
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
      const lines = esc.split('\n');
      const html = lines
        .map((line) => {
          const l = line.trim();
          if (l.startsWith('> ')) {
            return `<blockquote>${renderInline(l.slice(2))}</blockquote>`;
          }
          if (l.startsWith('- ')) {
            return `<li>${renderInline(l.slice(2))}</li>`;
          }
          if (!l) {
            return '<br/>';
          }
          return `<p>${renderInline(l)}</p>`;
        })
        .join('');
      return html.replaceAll(/<li>(.*?)<\/li>/g, '<ul>$&</ul>');
    }

    function renderInline(text: string): string {
      return text.replaceAll(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    }

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
      closeStream();
      sessionId.value = s.id;
      const rows = await loadMessages(s.id);
      // 先拉齐候选再匹配名称（挂载回显要按 id 找 name）
      await ensurePickerOptions();
      // 模型与挂载不绑会话：切会话时按该会话最近一轮回显 composer 选择，无历史则保留当前草稿
      const last =
        [...rows].reverse().find((r) => r.role === 'ASSISTANT') ||
        rows.at(-1);
      if (last) {
        if (last.modelId) modelId.value = last.modelId;
        mountedItems.value = mountsToItems(last.mounts || {});
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

    async function loadMessages(sid: number): Promise<api.HarnessMessage[]> {
      try {
        const rows = (await api.HarnessSessionMessages(sid)) || [];
        const list: ChatMsg[] = [];
        for (const row of rows) {
          if (row.role === 'USER') {
            list.push({ role: 'user', content: row.content });
            continue;
          }
          // 历史轮：落库正文/思考/轨迹合成时间线片段后渲染
          const msg: ChatMsg = {
            role: 'assistant',
            content: row.content,
            segments: legacyToSegments(row),
          };
          if (row.status === 'RUNNING') {
            // 刷新撞上执行中：接回实时流续渲染（Redis 频道不回放，丢的只是刷新前帧）
            msg.running = true;
            list.push(msg);
            subscribeTurn(row.turnId, msg);
          } else {
            if (row.status === 'FAILED') msg.error = row.error || '执行失败';
            if (row.status === 'INTERRUPTED') {
              msg.error = row.error || '本轮已中断';
            }
            list.push(msg);
          }
        }
        messages.value = list;
        return rows;
      } catch {
        message.error('历史消息加载失败');
        return [];
      }
    }

    function newSession() {
      closeStream();
      sessionId.value = 0;
      messages.value = [];
      mountedItems.value = [];
      inputValue.value = '';
    }

    async function deleteSession(id: number) {
      if (!id) return;
      try {
        await api.HarnessSessionDelete(id);
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
      if (!text || sending.value) return;
      if (!modelId.value) {
        message.warning('请先在下方选择一个已授权的模型');
        return;
      }
      inputValue.value = '';
      await postTurn(text, text);
    }

    /** 发一轮：messageText 给后端，displayText 给用户气泡（AskUser 走 JSON 时用可读文本回显） */
    async function postTurn(messageText: string, displayText: string) {
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

      messages.value.push({ role: 'user', content: displayText });
      sending.value = true;
      scrollToBottom();
      try {
        const res = await api.HarnessChat({
          sessionId: sessionId.value,
          message: messageText,
          modelId: modelId.value,
          mounts: currentMounts(),
        });
        void loadSessions(); // 刷新侧栏排序（updateTime 刚变过），不重选当前会话
        subscribeTurn(res.turnId);
      } catch (error: any) {
        messages.value.push({
          role: 'assistant',
          content: error?.message || '请求失败，深度探索服务暂不可用，请稍后重试。',
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
        target.done = true;
      } else {
        segs.push({
          kind: 'tool',
          id: toolCallId || nextSegId(),
          tool,
          arguments: argumentsText,
          done: true,
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
        });
      }
      if (row.content) {
        segs.push({ kind: 'text', id: nextSegId(), text: row.content });
      }
      return segs;
    }

    /** 订阅本轮事件流（传 msg = 刷新恢复的执行中历史行，复用同一条气泡） */
    function subscribeTurn(turnId: string, msg?: ChatMsg) {
      closeStream();
      activeTurnId = turnId;
      // reactive 包装：SSE 回调里改字段才能驱动视图更新（与已入列的普通对象共享底层，
      // 数组里的同源代理由渲染侧惰性生成，依赖能被同一代理触发）
      const target: ChatMsg = reactive(
        msg || {
          role: 'assistant',
          content: '',
          segments: [],
        },
      );
      if (!msg) {
        target.running = true;
        messages.value.push(target);
      }
      streaming.value = true;

      const es = new EventSource(api.HarnessSubscribeUrl(turnId));
      eventSource = es;
      es.onmessage = (ev: MessageEvent) => {
        let frame: api.HarnessFrame;
        try {
          frame = JSON.parse(ev.data);
        } catch {
          return;
        }
        applyFrame(target, frame);
        scrollToBottom();
      };
      es.onerror = () => {
        // 终态帧先到时 closeStream 已把连接关掉，这里只会因异常断流触发：
        // 关连接防 EventSource 自动重连（重连会重复回放已收帧），结果以刷新后历史为准
        if (target.running) {
          target.running = false;
          target.error = '事件连接已断开，本轮结果稍后刷新查看';
        }
        closeStream();
      };
      scrollToBottom();
    }

    /** 协议帧 → 消息条增量渲染（帧字段口径见 api.ts 的 HarnessFrame） */
    function applyFrame(msg: ChatMsg, frame: api.HarnessFrame) {
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
          );
          break;
        }
        case 'turn.completed': {
          // 终态：收尾仍在进行的思考段；正文已由 text.delta 落到时间线，
          // 不再用整段 content 覆盖（会打乱与工具/思考交错的时序）
          settleThinking(msg);
          msg.running = false;
          closeStream();
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
          closeStream();
          break;
        }
        case 'turn.failed': {
          settleThinking(msg);
          msg.running = false;
          msg.error = frame.error || '执行失败';
          closeStream();
          break;
        }
        default:
          break; // turn.started 不渲染（气泡已建）
      }
    }

    function closeStream() {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      activeTurnId = '';
      streaming.value = false;
    }

    async function interruptTurn() {
      if (!activeTurnId) return;
      try {
        await api.HarnessInterrupt(activeTurnId);
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
    const atWorkspaceRoot = computed(() => currentPath.value === WORKSPACE_ROOT);

    function goBack() {
      if (atWorkspaceRoot.value) return;
      const parts = currentPath.value.split('/').filter(Boolean);
      parts.pop();
      loadSandboxFiles(`/${parts.join('/')}`);
    }

    function enterDir(name: string) {
      loadSandboxFiles(`${currentPath.value || WORKSPACE_ROOT}/${name}`);
    }

    async function openFile(item: api.SandboxItem) {
      const path = `${currentPath.value || WORKSPACE_ROOT}/${item.name}`;
      try {
        const res = await api.SandboxFile(path);
        fileData.value = res;
        fileVisible.value = true;
      } catch (error: any) {
        message.error(error?.message || '文件预览失败，请下载后查看');
      }
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

    /** 上传文件到当前目录 */
    function beforeUpload(file: File) {
      uploadFile(file);
      return false;
    }

    async function uploadFile(file: File) {
      uploading.value = true;
      try {
        const res = await api.SandboxUpload(
          currentPath.value || WORKSPACE_ROOT,
          file,
        );
        message.success(`文件 ${res.name} 上传成功`);
        loadSandboxFiles();
      } catch {
        message.error('上传失败');
      } finally {
        uploading.value = false;
      }
    }

    // pathBreadcrumbClick 已删：面包屑非根段改由 subSegments 直接递对应的绝对 path 给 loadSandboxFiles。

    return {
      accessStore,
      messages,
      inputValue,
      sending,
      streaming,
      mountedItems,
      renderMarkdown,
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
      fileVisible,
      fileData,
      subSegments,
      atWorkspaceRoot,
      WORKSPACE_ROOT,
    };
  },
});
</script>

<template>
  <div class="hermes-page">
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
                </summary>
                <pre class="hermes-seg-body">{{ seg.text }}</pre>
              </details>

              <!-- 工具调用块：未完成转圈，完成打勾，入参折叠 -->
              <details
                v-else-if="seg.kind === 'tool'"
                class="hermes-seg hermes-call"
              >
                <summary class="hermes-seg-summary">
                  <LoadingOutlined v-if="!seg.done && msg.running" spin />
                  <CheckCircleOutlined v-else class="hermes-seg-done" />
                  <span>调用工具 · {{ seg.tool }}</span>
                </summary>
                <pre v-if="seg.arguments" class="hermes-seg-body">{{
                  seg.arguments
                }}</pre>
              </details>

              <!-- 正文块：按 markdown 直出（renderMarkdown 已转义 HTML，仅输出受控标签） -->
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div
                v-else-if="seg.kind === 'text'"
                class="hermes-bubble"
                v-html="renderMarkdown(seg.text)"
              ></div>
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
                    <span class="hermes-askuser-opt-label">{{ opt.label }}</span>
                    <small
                      v-if="opt.description"
                      class="hermes-askuser-opt-desc"
                    >
                      {{ opt.description }}
                    </small>
                  </label>
                </div>
                <a-textarea
                  :value="
                    msg.answers ? msg.answers[q.question].other : ''
                  "
                  :auto-size="{ minRows: 1, maxRows: 3 }"
                  placeholder="其他（可选，自由补充）"
                  class="hermes-askuser-other"
                  @change="
                    (e: any) => setAskOther(msg, q, e.target.value)
                  "
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
          </div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div
            v-else
            class="hermes-bubble"
            v-html="renderMarkdown(msg.content)"
          ></div>
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

    <!-- 右侧：沙箱桌面 -->
    <section class="hermes-sandbox">
      <header class="hermes-sandbox-header">
        <span class="hermes-sandbox-title">
          <span class="sandbox-dot"></span>
          沙箱工作区 <small>（{{ workspace }}）</small>
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
          <a-upload :before-upload="beforeUpload" :show-upload-list="false">
            <a-button size="small" type="primary" :loading="uploading">
              <template #icon><CloudUploadOutlined /></template>
              上传
            </a-button>
          </a-upload>
        </a-space>
      </header>

      <!-- 路径面包屑 -->
      <a-breadcrumb class="hermes-path">
        <a-breadcrumb-item>
          <a class="hermes-path-item" @click="loadSandboxFiles(WORKSPACE_ROOT)">工作区</a>
        </a-breadcrumb-item>
        <a-breadcrumb-item v-for="seg in subSegments" :key="seg.path">
          <a class="hermes-path-item" @click="loadSandboxFiles(seg.path)">{{
            seg.part
          }}</a>
        </a-breadcrumb-item>
      </a-breadcrumb>

      <!-- 文件列表 -->
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

    <!-- 文件查看弹窗 -->
    <a-modal
      v-model:open="fileVisible"
      :title="fileData?.name || '文件预览'"
      width="720px"
      :footer="null"
    >
      <div class="hermes-file-meta">
        {{ fileData?.path }} · {{ formatSize(fileData?.size || 0) }}
        <a-tag v-if="fileData?.truncated" color="warning">内容过长已截断</a-tag>
      </div>
      <pre class="hermes-file-content">{{ fileData?.content }}</pre>
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
