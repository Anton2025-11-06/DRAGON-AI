<script lang="ts" setup>
/**
 * 文件上传弹窗：按知识库类型限制可选后缀，一次多文件，逐文件回执。
 *
 * 上传接口只「收文件 + 建行 + 入队」，解析/分块/向量化由 rag 流水线跑，
 * 所以这里成功仅代表任务已投递，进度要回列表页轮询（SPEC §11.2）。
 */
import { computed, ref, watch } from 'vue';

import { InboxOutlined } from '@ant-design/icons-vue';
import { Alert, message, Modal, Upload } from 'ant-design-vue';

import { GetRuntimeConfig, KB_TYPE_ALLOWED_EXTS_FALLBACK, UploadDocuments } from './api';
import type { KbType } from './api';

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

const accept = computed(() =>
  allowedExts.value.map((e) => `.${e}`).join(','),
);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    fileList.value = [];
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

async function handleOk() {
  if (!props.kbId) return;
  const files = fileList.value
    .map((f) => f.originFileObj || f)
    .filter((f): f is File => f instanceof File);
  if (files.length === 0) {
    message.warning('请先选择文件');
    return;
  }
  uploading.value = true;
  try {
    const res = await UploadDocuments(props.kbId, files);
    const rejected = res.rejected || 0;
    if (res.accepted) {
      message.success(`已接收 ${res.accepted} 个文件，解析任务已投递`);
    }
    if (rejected) {
      const first = res.items?.find((i) => i.status === 'REJECTED');
      message.warning(`${rejected} 个文件被拒绝：${first?.message || '见逐条回执'}`);
    }
    emit('update:open', false);
    emit('uploaded');
  } catch (e: any) {
    message.error(e?.message || '上传失败');
  } finally {
    uploading.value = false;
  }
}
</script>

<template>
  <Modal
    :open="open"
    :confirm-loading="uploading"
    :title="`上传文件${kbName ? ' - ' + kbName : ''}`"
    @update:open="emit('update:open', $event)"
    @ok="handleOk"
  >
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
  </Modal>
</template>
