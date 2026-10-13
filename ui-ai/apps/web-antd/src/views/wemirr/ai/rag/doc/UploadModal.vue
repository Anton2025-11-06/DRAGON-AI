<script lang="ts" setup>
/**
 * 新增知识弹窗：两种来源共用一个入口。
 * - 文件上传：按知识库类型限制可选后缀，一次多文件，逐文件回执（走 OSS + 流水线）。
 * - URL 导入：一次粘多行链接，逐行回执（不落 OSS，worker 解析时直接 GET URL 取字节）。
 *
 * 两个接口都只「建行 + 入队」，解析/分块/向量化由 rag 流水线跑，
 * 所以这里成功仅代表任务已投递，进度要回列表页轮询（SPEC §11.2）。
 */
import { computed, ref, watch } from 'vue';

import { InboxOutlined } from '@ant-design/icons-vue';
import {
  Alert,
  message,
  Modal,
  TabPane,
  Tabs,
  Textarea,
  Upload,
} from 'ant-design-vue';

import {
  GetRuntimeConfig,
  KB_TYPE_ALLOWED_EXTS_FALLBACK,
  UploadDocuments,
  UploadDocumentsByUrl,
} from './api';
import type { DocUrlItem, KbType, UploadResp } from './api';

const props = defineProps<{
  open: boolean;
  kbId: number | null;
  kbType: KbType;
  kbName?: string;
}>();

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void;
  (e: 'uploaded'): void;
}>();

const fileList = ref<any[]>([]);
const uploading = ref(false);
const allowedExts = ref<string[]>([]);
const maxMb = ref(200);
// 当前来源页签：file=文件上传 / url=链接导入
const activeTab = ref<'file' | 'url'>('file');
// URL 导入文本框：一行一个链接
const urlText = ref('');

const accept = computed(() =>
  allowedExts.value.map((e) => `.${e}`).join(','),
);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    fileList.value = [];
    activeTab.value = 'file';
    urlText.value = '';
    try {
      const cfg = await GetRuntimeConfig({ kbType: props.kbType });
      allowedExts.value =
        cfg.allowedExts?.[props.kbType] ||
        KB_TYPE_ALLOWED_EXTS_FALLBACK[props.kbType] ||
        [];
      maxMb.value = cfg.maxFileSizeMb || 200;
    } catch {
      allowedExts.value = KB_TYPE_ALLOWED_EXTS_FALLBACK[props.kbType] || [];
    }
  },
);

function beforeUpload(file: File) {
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  if (allowedExts.value.length && !allowedExts.value.includes(ext)) {
    message.error(`该知识库不支持 .${ext}，允许：${allowedExts.value.join(' / ')}`);
    return Upload.LIST_IGNORE as any;
  }
  if (file.size > maxMb.value * 1024 * 1024) {
    message.error(`${file.name} 超过 ${maxMb.value}MB 上限`);
    return Upload.LIST_IGNORE as any;
  }
  return false; // 阻止自动上传，改由 onOk 统一提交
}

/** 逐条回执：成功/被拒分开提示，两个来源共用（URL 被拒项后端不回 docId） */
function reportReceipt(res: UploadResp, unit: string) {
  const rejected = res.rejected || 0;
  if (res.accepted) {
    message.success(`已接收 ${res.accepted} 个${unit}，解析任务已投递`);
  }
  if (rejected) {
    const first = res.items?.find(
      (i) => i.status === 'REJECTED' || (!i.docId && i.message),
    );
    message.warning(`${rejected} 个${unit}被拒绝：${first?.message || '见逐条回执'}`);
  }
  emit('update:open', false);
  emit('uploaded');
}

async function handleOk() {
  if (!props.kbId) return;
  uploading.value = true;
  try {
    if (activeTab.value === 'url') {
      await submitUrls();
    } else {
      await submitFiles();
    }
  } catch (e: any) {
    message.error(e?.message || '提交失败');
  } finally {
    uploading.value = false;
  }
}

async function submitFiles() {
  const files = fileList.value
    .map((f) => f.originFileObj || f)
    .filter((f): f is File => f instanceof File);
  if (files.length === 0) {
    message.warning('请先选择文件');
    return;
  }
  const res = await UploadDocuments(props.kbId!, files);
  reportReceipt(res, '文件');
}

async function submitUrls() {
  const items: DocUrlItem[] = urlText.value
    .split('\n')
    .map((u) => u.trim())
    .filter((u) => u.length > 0)
    .map((url) => ({ url }));
  if (items.length === 0) {
    message.warning('请至少填写一个文件 URL');
    return;
  }
  const res = await UploadDocumentsByUrl(props.kbId!, items);
  reportReceipt(res, '链接');
}
</script>

<template>
  <Modal
    :open="open"
    :confirm-loading="uploading"
    :title="`新增知识${kbName ? ' - ' + kbName : ''}`"
    @update:open="emit('update:open', $event)"
    @ok="handleOk"
  >
    <Tabs v-model:active-key="activeTab">
      <TabPane key="file" tab="文件上传">
        <Alert
          class="mb-3"
          type="info"
          :message="`支持格式：${allowedExts.join(' / ') || '按库类型'}；单文件上限 ${maxMb}MB；可多选批量上传`"
        />
        <Upload
          v-model:file-list="fileList"
          :accept="accept"
          :before-upload="beforeUpload"
          :multiple="true"
          draggable
        >
          <p class="ant-upload-drag-icon"><InboxOutlined /></p>
          <p class="ant-upload-text">点击或拖拽文件到此区域</p>
        </Upload>
      </TabPane>
      <TabPane key="url" tab="URL 导入">
        <Alert
          class="mb-3"
          type="info"
          message="一行一个文件 URL（http/https），需公网/内网可直接访问；导入后由后台流水线直接 GET 该链接解析，列表「查看原文件」会打开原始链接。"
        />
        <Textarea
          v-model:value="urlText"
          :auto-size="{ minRows: 6, maxRows: 12 }"
          placeholder="https://example.com/docs/report.pdf&#10;https://example.com/files/notes.md"
        />
      </TabPane>
    </Tabs>
  </Modal>
</template>
