<script setup lang="ts">
import type { UploadFile } from 'ant-design-vue';
/**
 * DynamicInputForm 动态输入表单组件
 * 根据 START 节点的字段定义动态生成表单
 * 支持 SHORT_TEXT、PARAGRAPH、NUMBER、SELECT、CHECKBOX、SINGLE_FILE、FILE_LIST 类型
 *
 */
import type { FormInstance, Rule } from 'ant-design-vue/es/form';

import type { InputField } from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  FileOutlined,
  LoadingOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { uploadWorkflowFile } from '#/api/ai-workflow';

// ==================== Props ====================

interface Props {
  /** 字段定义列表 */
  fields: InputField[];
  /** 表单值 */
  values?: Record<string, any>;
}

const props = withDefaults(defineProps<Props>(), {
  fields: () => [],
  values: () => ({}),
});

// ==================== Emits ====================

const emit = defineEmits<{
  (e: 'update:values', values: Record<string, any>): void;
}>();

// ==================== Refs ====================

const formRef = ref<FormInstance>();

// ==================== State ====================

const formValues = ref<Record<string, any>>({});

/** 文件列表缓存（用于 a-upload 显示） */
const fileListCache = reactive<Record<string, UploadFile[]>>({});

/** 正在上传的字段 */
const uploadingFields = ref<Record<string, boolean>>({});

// ==================== Computed ====================

/**
 * 生成表单验证规则
 */
const formRules = computed(() => {
  const rules: Record<string, Rule[]> = {};

  props.fields.forEach((field) => {
    const fieldRules: Rule[] = [];

    // 必填验证
    if (field.required) {
      fieldRules.push({
        required: true,
        message: `${field.label}不能为空`,
        trigger: field.type === 'SELECT' ? 'change' : 'blur',
      });
    }

    // 文本类型的正则验证
    if (
      (field.type === 'SHORT_TEXT' || field.type === 'PARAGRAPH') &&
      field.pattern
    ) {
      fieldRules.push({
        pattern: new RegExp(field.pattern),
        message: field.patternMessage || '格式不正确',
        trigger: 'blur',
      });
    }

    // 数字类型的范围验证
    if (field.type === 'NUMBER') {
      if (field.minValue !== undefined) {
        fieldRules.push({
          type: 'number',
          min: field.minValue,
          message: `${field.label}不能小于${field.minValue}`,
          trigger: 'blur',
        });
      }
      if (field.maxValue !== undefined) {
        fieldRules.push({
          type: 'number',
          max: field.maxValue,
          message: `${field.label}不能大于${field.maxValue}`,
          trigger: 'blur',
        });
      }
    }

    if (fieldRules.length > 0) {
      rules[field.name] = fieldRules;
    }
  });

  return rules;
});

// ==================== Watch ====================

// 监听外部 values 变化，同步到内部状态
watch(
  () => props.values,
  (newValues) => {
    if (newValues) {
      formValues.value = { ...newValues };
    }
  },
  { immediate: true, deep: true },
);

// 监听字段定义变化，初始化默认值
watch(
  () => props.fields,
  (newFields) => {
    if (newFields && newFields.length > 0) {
      initDefaultValues(newFields);
    }
  },
  { immediate: true },
);

// 监听内部值变化，同步到外部
watch(
  formValues,
  (newValues) => {
    emit('update:values', { ...newValues });
  },
  { deep: true },
);

// ==================== Methods ====================

/**
 * 初始化默认值
 */
function initDefaultValues(fields: InputField[]) {
  fields.forEach((field) => {
    // 如果当前值为空且有默认值，则设置默认值
    if (
      formValues.value[field.name] === undefined &&
      field.defaultValue !== undefined
    ) {
      formValues.value[field.name] = field.defaultValue;
    }

    // 为特定类型设置初始值
    if (formValues.value[field.name] === undefined) {
      switch (field.type) {
        case 'CHECKBOX': {
          formValues.value[field.name] = false;
          break;
        }
        case 'FILE_LIST':
        case 'SINGLE_FILE': {
          formValues.value[field.name] = [];
          break;
        }
      }
    }
  });
}

/**
 * 获取文件上传接受的类型
 */
function getAcceptTypes(field: InputField): string {
  if (field.allowedFileTypes && field.allowedFileTypes.length > 0) {
    return field.allowedFileTypes.join(',');
  }
  return '*';
}

/**
 * 处理文件上传
 */
async function handleFileUpload(
  fieldName: string,
  options: {
    file: File;
    onError: (error: any) => void;
    onSuccess: (response: any) => void;
  },
  isMultiple: boolean,
) {
  const { file, onSuccess, onError } = options;

  uploadingFields.value[fieldName] = true;

  try {
    // 调用上传 API
    const fileInfo = await uploadWorkflowFile(file);

    // 更新文件列表缓存
    const uploadFile: UploadFile = {
      uid: fileInfo.fileId,
      name: fileInfo.name,
      status: 'done',
      response: fileInfo,
    };

    if (!fileListCache[fieldName]) {
      fileListCache[fieldName] = [];
    }

    if (isMultiple) {
      fileListCache[fieldName].push(uploadFile);
    } else {
      fileListCache[fieldName] = [uploadFile];
    }

    // 更新表单值（存储 fileId）
    if (isMultiple) {
      if (!formValues.value[fieldName]) {
        formValues.value[fieldName] = [];
      }
      formValues.value[fieldName].push({
        fileId: fileInfo.fileId,
        name: fileInfo.name,
      });
    } else {
      formValues.value[fieldName] = {
        fileId: fileInfo.fileId,
        name: fileInfo.name,
      };
    }

    onSuccess(fileInfo);
    message.success(`文件 ${fileInfo.name} 上传成功`);
  } catch (error: any) {
    onError(error);
    message.error(`文件上传失败: ${error.message || '未知错误'}`);
  } finally {
    uploadingFields.value[fieldName] = false;
  }
}

/**
 * 处理文件删除
 */
function handleFileRemove(
  fieldName: string,
  file: UploadFile,
  isMultiple: boolean,
) {
  // 从缓存中移除
  if (fileListCache[fieldName]) {
    fileListCache[fieldName] = fileListCache[fieldName].filter(
      (f: UploadFile) => f.uid !== file.uid,
    );
  }

  // 从表单值中移除
  formValues.value[fieldName] =
    isMultiple && Array.isArray(formValues.value[fieldName])
      ? formValues.value[fieldName].filter(
          (f: { fileId: string }) => f.fileId !== file.uid,
        )
      : undefined;

  return true;
}

/**
 * 验证表单
 */
async function validate(): Promise<boolean> {
  try {
    await formRef.value?.validate();
    return true;
  } catch {
    return false;
  }
}

/**
 * 重置表单
 */
function resetFields() {
  formRef.value?.resetFields();
  formValues.value = {};
  initDefaultValues(props.fields);
}

/**
 * 获取表单值
 */
function getValues(): Record<string, any> {
  return { ...formValues.value };
}

/**
 * 设置表单值
 */
function setValues(values: Record<string, any>) {
  formValues.value = { ...values };
}

/**
 * 清除验证状态
 */
function clearValidate(names?: string[]) {
  formRef.value?.clearValidate(names);
}

// ==================== Expose ====================

defineExpose({
  validate,
  resetFields,
  getValues,
  setValues,
  clearValidate,
});
</script>

<template>
  <a-form
    ref="formRef"
    :model="formValues"
    :rules="formRules"
    layout="vertical"
    class="dynamic-input-form"
  >
    <a-empty v-if="!fields || fields.length === 0" description="暂无输入字段" />

    <a-form-item
      v-for="field in fields"
      :key="field.name"
      :name="field.name"
      :label="field.label"
      :required="field.required"
    >
      <template #help>
        <span v-if="field.description" class="field-description">
          {{ field.description }}
        </span>
      </template>

      <!-- 短文本 SHORT_TEXT -->
      <a-input
        v-if="field.type === 'SHORT_TEXT'"
        v-model:value="formValues[field.name]"
        :maxlength="field.maxLength || 256"
        :placeholder="field.description || `请输入${field.label}`"
        allow-clear
        show-count
      />

      <!-- 长文本 PARAGRAPH -->
      <a-textarea
        v-else-if="field.type === 'PARAGRAPH'"
        v-model:value="formValues[field.name]"
        :rows="4"
        :maxlength="field.maxLength || 100000"
        :placeholder="field.description || `请输入${field.label}`"
        show-count
        allow-clear
      />

      <!-- 数字 NUMBER -->
      <a-input-number
        v-else-if="field.type === 'NUMBER'"
        v-model:value="formValues[field.name]"
        :min="field.minValue"
        :max="field.maxValue"
        :placeholder="field.description || `请输入${field.label}`"
        style="width: 100%"
      />

      <!-- 下拉选择 SELECT -->
      <a-select
        v-else-if="field.type === 'SELECT'"
        v-model:value="formValues[field.name]"
        :placeholder="field.description || `请选择${field.label}`"
        allow-clear
      >
        <a-select-option v-for="opt in field.options" :key="opt" :value="opt">
          {{ opt }}
        </a-select-option>
      </a-select>

      <!-- 复选框 CHECKBOX -->
      <a-checkbox
        v-else-if="field.type === 'CHECKBOX'"
        v-model:checked="formValues[field.name]"
      >
        {{ field.description || field.label }}
      </a-checkbox>

      <!-- 单文件上传 SINGLE_FILE -->
      <a-upload
        v-else-if="field.type === 'SINGLE_FILE'"
        v-model:file-list="fileListCache[field.name]"
        :max-count="1"
        :accept="getAcceptTypes(field)"
        :custom-request="
          (options: any) => handleFileUpload(field.name, options, false)
        "
        @remove="(file: any) => handleFileRemove(field.name, file, false)"
      >
        <a-button :loading="uploadingFields[field.name]">
          <template v-if="!uploadingFields[field.name]">
            <UploadOutlined /> 上传文件
          </template>
          <template v-else> <LoadingOutlined /> 上传中... </template>
        </a-button>
        <template #itemRender="{ file, actions }">
          <div
            class="file-item"
            :class="{ uploading: file.status === 'uploading' }"
          >
            <FileOutlined />
            <span class="file-name">{{ file.name }}</span>
            <a-tag v-if="file.response?.fileId" color="success" size="small">
              已上传
            </a-tag>
            <a-button type="link" size="small" danger @click="actions.remove">
              <DeleteOutlined />
            </a-button>
          </div>
        </template>
      </a-upload>

      <!-- 多文件上传 FILE_LIST -->
      <a-upload
        v-else-if="field.type === 'FILE_LIST'"
        v-model:file-list="fileListCache[field.name]"
        :max-count="field.maxFileCount || 5"
        :accept="getAcceptTypes(field)"
        :custom-request="
          (options: any) => handleFileUpload(field.name, options, true)
        "
        @remove="(file: any) => handleFileRemove(field.name, file, true)"
        multiple
      >
        <a-button :loading="uploadingFields[field.name]">
          <template v-if="!uploadingFields[field.name]">
            <UploadOutlined /> 上传文件
          </template>
          <template v-else> <LoadingOutlined /> 上传中... </template>
        </a-button>
        <template #itemRender="{ file, actions }">
          <div
            class="file-item"
            :class="{ uploading: file.status === 'uploading' }"
          >
            <FileOutlined />
            <span class="file-name">{{ file.name }}</span>
            <a-tag v-if="file.response?.fileId" color="success" size="small">
              已上传
            </a-tag>
            <a-button type="link" size="small" danger @click="actions.remove">
              <DeleteOutlined />
            </a-button>
          </div>
        </template>
      </a-upload>
    </a-form-item>
  </a-form>
</template>

<style scoped lang="less">
.dynamic-input-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 13px;
      font-weight: 500;
      color: #262626;
    }
  }

  .field-description {
    font-size: 12px;
    color: #8c8c8c;
  }

  .file-item {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 4px 8px;
    background-color: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 4px;

    .file-name {
      flex: 1;
      overflow: hidden;
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  :deep(.ant-upload-list) {
    margin-top: 8px;
  }

  :deep(.ant-input-number) {
    width: 100%;
  }

  :deep(.ant-checkbox-wrapper) {
    font-size: 13px;
  }
}
</style>
