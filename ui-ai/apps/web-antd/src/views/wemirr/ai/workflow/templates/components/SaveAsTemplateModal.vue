<script lang="ts" setup>
/**
 * 保存为模板弹窗组件
 * 用于将工作流保存为自定义模板
 */
import { reactive, ref, watch } from 'vue';

import { Form, Input, message, Select } from 'ant-design-vue';

import { createTemplateFromWorkflow } from '#/api/ai-workflow';

interface Props {
  open: boolean;
  workflowId: string | number;
  workflowName?: string;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'success'): void;
}>();

const formRef = ref();
const loading = ref(false);

// 表单数据
const formData = reactive({
  name: '',
  description: '',
  category: 'RAG',
});

// 分类选项
const categoryOptions = [
  { value: 'RAG', label: 'RAG问答' },
  { value: 'SUMMARY', label: '文档摘要' },
  { value: 'EXTRACTION', label: '数据提取' },
  { value: 'CONVERSATION', label: '多轮对话' },
  { value: 'GENERATION', label: '内容生成' },
];

// 表单规则
const rules = {
  name: [{ required: true, message: '请输入模板名称' }],
  category: [{ required: true, message: '请选择模板分类' }],
};

// 提交表单
async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  loading.value = true;
  try {
    await createTemplateFromWorkflow(
      props.workflowId,
      formData.name,
      formData.category,
      formData.description,
    );
    message.success('保存模板成功');
    emit('success');
    emit('update:open', false);
  } catch {
    message.error('保存模板失败');
  } finally {
    loading.value = false;
  }
}

// 关闭弹窗
function handleCancel() {
  emit('update:open', false);
}

// 监听弹窗打开，重置表单
watch(
  () => props.open,
  (open) => {
    if (open) {
      formData.name = props.workflowName ? `${props.workflowName} - 模板` : '';
      formData.description = '';
      formData.category = 'RAG';
    }
  },
);
</script>

<template>
  <a-modal
    :open="open"
    title="保存为模板"
    :confirm-loading="loading"
    @ok="handleSubmit"
    @cancel="handleCancel"
  >
    <Form ref="formRef" :model="formData" :rules="rules" layout="vertical">
      <a-form-item label="模板名称" name="name">
        <Input
          v-model:value="formData.name"
          placeholder="请输入模板名称"
          :maxlength="50"
        />
      </a-form-item>

      <a-form-item label="模板分类" name="category">
        <Select
          v-model:value="formData.category"
          placeholder="请选择模板分类"
          :options="categoryOptions"
        />
      </a-form-item>

      <a-form-item label="模板描述" name="description">
        <Input.TextArea
          v-model:value="formData.description"
          placeholder="请输入模板描述"
          :rows="3"
          :maxlength="200"
          show-count
        />
      </a-form-item>
    </Form>
  </a-modal>
</template>
