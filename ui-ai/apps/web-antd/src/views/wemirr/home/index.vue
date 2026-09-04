<script lang="ts">
import { defineComponent, nextTick, onMounted, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import {
  ArrowLeftOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
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

interface ChatMsg {
  role: 'assistant' | 'user';
  content: string;
}

/** 已挂载条目 */
interface MountedItem {
  key: string;
  name: string;
  source: 'kb' | 'skill' | 'tool';
}

export default defineComponent({
  name: 'HernesExplorerPage',
  components: {
    ArrowLeftOutlined,
    CloudDownloadOutlined,
    CloudUploadOutlined,
    FileTextFilled,
    FolderFilled,
    PlusOutlined,
    ReloadOutlined,
    RobotOutlined,
    SendOutlined,
  },
  setup() {
    const accessStore = useAccessStore();

    // ==================== 对话框 ====================
    const messages = ref<ChatMsg[]>([]);
    const inputValue = ref('');
    const sending = ref(false);
    const streaming = ref(false);
    const mountedItems = ref<MountedItem[]>([]);
    const typeWriterTimer = ref<any>(null);

    // 选择弹窗
    const pickerVisible = ref(false);
    const pickerType = ref<'kb' | 'skill' | 'tool'>('skill');
    const pickerLoading = ref(false);
    const skillOptions = ref<api.SandboxSkill[]>([]);
    const toolOptions = ref<api.McpToolOption[]>([]);
    const kbOptions = ref<api.KbOption[]>([]);
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
      loadSandboxFiles();
      loadSkills();
    });

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

    /** 打字机效果展示 AI 回复 */
    function typewrite(full: string) {
      streaming.value = true;
      const msg: ChatMsg = { role: 'assistant', content: '' };
      messages.value.push(msg);
      let idx = 0;
      typeWriterTimer.value = setInterval(() => {
        idx += 2;
        msg.content = full.slice(0, idx);
        if (idx >= full.length) {
          clearInterval(typeWriterTimer.value);
          typeWriterTimer.value = null;
          streaming.value = false;
        }
        scrollToBottom();
      }, 20);
    }

    function stopStreaming() {
      if (typeWriterTimer.value) {
        clearInterval(typeWriterTimer.value);
        typeWriterTimer.value = null;
        streaming.value = false;
        const last = messages.value[messages.value.length - 1];
        if (last && last.role === 'assistant') {
          // 保持当前显示内容
        }
      }
    }

    async function handleSend() {
      const text = inputValue.value.trim();
      if (!text || sending.value) return;
      if (streaming.value) {
        stopStreaming();
        return;
      }
      messages.value.push({ role: 'user', content: text });
      inputValue.value = '';
      sending.value = true;
      scrollToBottom();
      try {
        const res = await api.SandboxChat(
          text,
          mountedItems.value
            .filter((i) => i.source === 'skill')
            .map((i) => i.key),
          mountedItems.value
            .filter((i) => i.source === 'tool')
            .map((i) => i.key),
          mountedItems.value.filter((i) => i.source === 'kb').map((i) => i.key),
        );
        typewrite(res.reply || '（沙箱未返回内容）');
      } catch {
        messages.value.push({
          role: 'assistant',
          content: '请求失败，沙箱运行服务暂时不可用，请稍后重试。',
        });
        scrollToBottom();
      } finally {
        sending.value = false;
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

    // ==================== 技能 / 工具 / 知识库选择 ====================
    async function loadSkills() {
      try {
        const res = await api.SandboxSkills();
        skillOptions.value = res.items || [];
      } catch {
        skillOptions.value = [];
      }
    }

    async function openPicker(type: 'kb' | 'skill' | 'tool') {
      pickerType.value = type;
      pickerSelected.value = mountedItems.value.filter(
        (i) => i.source === type,
      );
      pickerVisible.value = true;
      pickerLoading.value = true;
      try {
        if (type === 'skill') {
          if (skillOptions.value.length === 0) await loadSkills();
        } else if (type === 'tool') {
          const res = await api.McpServersPage({ current: 1, size: 100 });
          toolOptions.value = res.records || [];
        } else {
          const res = await api.KbList({ page: 1, page_size: 100 });
          kbOptions.value = res.items || [];
        }
      } catch {
        if (type === 'kb') {
          message.error('知识库服务暂不可用');
        } else {
          message.error('列表加载失败');
        }
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
      openPicker,
      pickerVisible,
      pickerType,
      pickerLoading,
      skillOptions,
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
    <!-- 左侧：深度探索 Hernes 对话框 -->
    <section class="hermes-chat">
      <header class="hermes-chat-header">
        <div class="hermes-logo">
          <RobotOutlined />
        </div>
        <div class="hermes-title">
          <h2>深度探索 Hernes</h2>
          <p>连接云端沙箱运行时，描述你的问题，让 AI 帮你执行</p>
        </div>
        <a-tag color="purple">沙箱演示模式</a-tag>
      </header>

      <!-- 已挂载能力 -->
      <div class="hermes-mounted">
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
          点击下方 + 按钮挂载技能 / 工具 / 知识库
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
          <!-- renderMarkdown 已先行转义 HTML 特殊字符，仅输出受控标签 -->
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="hermes-bubble" v-html="renderMarkdown(msg.content)"></div>
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
          <a-button
            size="small"
            ghost
            type="primary"
            @click="openPicker('skill')"
          >
            <template #icon><PlusOutlined /></template>
            技能
          </a-button>
          <a-button
            size="small"
            ghost
            type="success"
            @click="openPicker('tool')"
          >
            <template #icon><PlusOutlined /></template>
            工具
          </a-button>
          <a-button size="small" ghost type="warning" @click="openPicker('kb')">
            <template #icon><PlusOutlined /></template>
            知识库
          </a-button>
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

    <!-- 技能/工具/知识库选择弹窗 -->
    <a-modal
      v-model:open="pickerVisible"
      :title="
        pickerType === 'skill'
          ? '挂载技能'
          : pickerType === 'tool'
            ? '挂载工具'
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
            :class="{ active: pickerSelected.some((i) => i.key === s.id) }"
            @click="togglePick({ key: s.id, name: s.name, source: 'skill' })"
          >
            <b>{{ s.name }}</b>
            <span>{{ s.description }}</span>
          </div>
          <a-empty
            v-if="skillOptions.length === 0"
            description="暂无可用技能"
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
            <span>{{ t.type }} · {{ t.url || t.command || '本地命令' }}</span>
          </div>
          <a-empty
            v-if="toolOptions.length === 0"
            description="暂无 MCP 连接，请先到 MCP 连接管理添加"
          />
        </div>
        <div v-else>
          <div
            v-for="k in kbOptions"
            :key="k.kb_id"
            class="hermes-picker-item"
            :class="{
              active: pickerSelected.some((i) => i.key === String(k.kb_id)),
            }"
            @click="
              togglePick({
                key: String(k.kb_id),
                name: k.kb_name,
                source: 'kb',
              })
            "
          >
            <b>{{ k.kb_name }}</b>
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
  flex: 0 0 58%;
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
    gap: 6px;
    padding: 8px 16px 0;
    min-height: 34px;

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
      gap: 8px;
      margin-bottom: 8px;
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
</style>
