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
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  FileTextFilled,
  FolderFilled,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { getTraceId } from '#/api/request';
import * as api from './api';

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
    CloudDownloadOutlined,
    CloudUploadOutlined,
    DeleteOutlined,
    FileTextFilled,
    FolderFilled,
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
    const currentPath = ref('');
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
          // 历史轮：正文/思考/轨迹都已落库，直接渲染
          const msg: ChatMsg = {
            role: 'assistant',
            content: row.content,
            thinking: row.thinking || undefined,
            toolCalls: row.toolCalls || [],
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

    async function deleteCurrentSession() {
      if (!sessionId.value) return;
      try {
        await api.HarnessSessionDelete(sessionId.value);
        newSession();
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
        message.warning('请先在右上角选择一个已授权的模型');
        return;
      }

      // 首条消息自动建会话：只落标题，模型与挂载随每次发消息传
      if (!sessionId.value) {
        try {
          const s = await api.HarnessSessionCreate({
            title: text.slice(0, 24),
          });
          sessionId.value = s.id;
        } catch (error: any) {
          message.error(error?.message || '会话创建失败');
          return;
        }
      }

      messages.value.push({ role: 'user', content: text });
      inputValue.value = '';
      sending.value = true;
      scrollToBottom();
      try {
        const res = await api.HarnessChat({
          sessionId: sessionId.value,
          message: text,
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
          thinking: '',
          toolCalls: [],
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
          msg.content += frame.content || '';
          break;
        }
        case 'thinking.delta': {
          msg.thinking = (msg.thinking || '') + (frame.content || '');
          break;
        }
        case 'tool.started': {
          msg.toolCalls = msg.toolCalls || [];
          msg.toolCalls.push({
            tool: frame.tool || '',
            toolCallId: frame.toolCallId,
            arguments: '',
          });
          break;
        }
        case 'tool.completed': {
          const calls = msg.toolCalls || [];
          // 从后往前找同名未闭合的调用补入参（工具名+空入参定位）
          for (let i = calls.length - 1; i >= 0; i--) {
            const call = calls[i];
            if (call && call.tool === frame.tool && !call.arguments) {
              call.arguments = frame.arguments || '';
              break;
            }
          }
          break;
        }
        case 'turn.completed': {
          // 终态帧带落库全文：以库为准覆盖（流式拼接与落库的出入以权威结果修正）
          if (frame.content) msg.content = frame.content;
          msg.running = false;
          closeStream();
          break;
        }
        case 'turn.interrupted': {
          msg.running = false;
          // HITL 挂起时优先显示智能体的问题（reason 是「等待答复」占位，
          // 回放路径下 question 已拼进 reason）
          msg.error = frame.question || frame.reason || '本轮已中断';
          closeStream();
          break;
        }
        case 'turn.failed': {
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
        currentPath.value = res.current_path || '';
        workspace.value = res.workspace || '';
        sandboxItems.value = res.items || [];
      } catch {
        message.error('沙箱工作区加载失败');
      } finally {
        sandboxLoading.value = false;
      }
    }

    function goBack() {
      const parts = currentPath.value.split('/').filter(Boolean);
      parts.pop();
      loadSandboxFiles(parts.join('/'));
    }

    function enterDir(name: string) {
      const next = currentPath.value ? `${currentPath.value}/${name}` : name;
      loadSandboxFiles(next);
    }

    async function openFile(item: api.SandboxItem) {
      const path = currentPath.value
        ? `${currentPath.value}/${item.name}`
        : item.name;
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
      const path = currentPath.value
        ? `${currentPath.value}/${item.name}`
        : item.name;
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
        const res = await api.SandboxUpload(currentPath.value, file);
        message.success(`文件 ${res.name} 上传成功`);
        loadSandboxFiles();
      } catch {
        message.error('上传失败');
      } finally {
        uploading.value = false;
      }
    }

    function pathBreadcrumbClick(part: string, index: number) {
      const parts = currentPath.value.split('/').filter(Boolean);
      loadSandboxFiles(parts.slice(0, index + 1).join('/'));
      void part;
    }

    return {
      accessStore,
      messages,
      inputValue,
      sending,
      streaming,
      mountedItems,
      renderMarkdown,
      handleSend,
      removeMounted,
      // 会话 / 模型
      sessions,
      sessionId,
      sessionLoading,
      modelId,
      modelSelectOptions,
      switchSession,
      newSession,
      deleteCurrentSession,
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
      pathBreadcrumbClick,
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
        <a-select
          class="hermes-model-select"
          :options="modelSelectOptions"
          placeholder="选择模型"
          size="small"
          :value="modelId || undefined"
          @change="changeModel"
        />
        <a-tooltip v-if="sessionId" title="删除当前会话">
          <a-button danger ghost size="small" @click="deleteCurrentSession">
            <template #icon><DeleteOutlined /></template>
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
          <!-- assistant 消息复合体：思考流（折叠）→ 工具轨迹 → 正文 → 运行光标 → 异常提示 -->
          <div v-if="msg.role === 'assistant'" class="hermes-body">
            <details v-if="msg.thinking" class="hermes-thinking">
              <summary>思考过程</summary>
              <pre>{{ msg.thinking }}</pre>
            </details>
            <div
              v-for="(call, ci) in msg.toolCalls || []"
              :key="ci"
              class="hermes-call"
            >
              <b>{{ call.tool }}</b>
              <pre v-if="call.arguments">{{ call.arguments }}</pre>
            </div>
            <!-- renderMarkdown 已先行转义 HTML 特殊字符，仅输出受控标签 -->
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div
              v-if="msg.content"
              class="hermes-bubble"
              v-html="renderMarkdown(msg.content)"
            ></div>
            <div v-if="msg.running" class="hermes-running">
              <span class="dot"></span>
              <span class="dot"></span>
              <span class="dot"></span>
            </div>
            <div v-if="msg.error" class="hermes-msg-error">
              <a-tag color="warning">{{ msg.error }}</a-tag>
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
            <a-button size="small" :disabled="!currentPath" @click="goBack">
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
          <a class="hermes-path-item" @click="loadSandboxFiles('')">工作区</a>
        </a-breadcrumb-item>
        <a-breadcrumb-item
          v-for="(part, i) in currentPath.split('/').filter(Boolean)"
          :key="i"
        >
          <a class="hermes-path-item" @click="pathBreadcrumbClick(part, i)">{{
            part
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

    .hermes-model-select {
      width: 190px;
      flex-shrink: 0;
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
        margin-left: auto;
        font-size: 12px;
        color: var(--text-color-3, #8a9099);
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
    padding: 7px 8px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;

    &:hover {
      background: var(--hover-color, #f5f7fa);
    }
    &.active {
      background: rgba(22, 119, 255, 0.08);

      .hermes-side-title {
        color: var(--primary-color, #1677ff);
      }
    }

    .hermes-side-title {
      display: block;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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

  .hermes-thinking {
    width: 100%;
    border: 1px dashed var(--border-color, #e4e7ed);
    border-radius: 8px;
    padding: 4px 8px;

    summary {
      cursor: pointer;
      font-size: 12px;
      color: var(--text-color-3, #8a9099);
      user-select: none;
    }
    pre {
      margin: 6px 0 2px;
      font-size: 12px;
      line-height: 1.6;
      color: var(--text-color-2, #5e6470);
      white-space: pre-wrap;
      word-break: break-word;
    }
  }

  .hermes-call {
    max-width: 100%;
    border-left: 3px solid #8b5cf6;
    background: rgba(139, 92, 246, 0.06);
    border-radius: 4px 8px 8px 4px;
    padding: 4px 8px;

    b {
      font-size: 12px;
    }
    pre {
      margin: 4px 0 2px;
      font-size: 11px;
      color: var(--text-color-3, #8a9099);
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 120px;
      overflow-y: auto;
    }
  }

  .hermes-msg-error {
    :deep(.ant-tag) {
      max-width: 100%;
      white-space: normal;
      height: auto;
    }
  }

  .hermes-running {
    display: inline-flex;
    gap: 4px;
    padding: 6px 2px 2px;

    .dot {
      width: 5px;
      height: 5px;
      border-radius: 50%;
      background: #8b5cf6;
      animation: hermes-blink 1.2s infinite;

      &:nth-child(2) {
        animation-delay: 0.2s;
      }
      &:nth-child(3) {
        animation-delay: 0.4s;
      }
    }
  }
}

@keyframes hermes-blink {
  0%,
  60%,
  100% {
    opacity: 0.25;
  }

  30% {
    opacity: 1;
  }
}
</style>
