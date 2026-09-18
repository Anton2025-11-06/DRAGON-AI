<script setup lang="ts">
import type { UploadFile } from 'ant-design-vue';
/**
 * DynamicInputForm 动态输入表单组件
 * 根据 START 节点的字段定义动态生成表单
 * 支持 TEXT（文本）、NUMBER、SELECT、CHECKBOX（开关）、SINGLE_FILE、FILE_LIST、APPROVER（审批人数组）类型；
 * 旧图的 SHORT_TEXT / PARAGRAPH 经 normalizeInputFieldType 归一为 TEXT
 *
 */
import type { FormInstance, Rule } from 'ant-design-vue/es/form';

import type { WorkflowFileUpload } from '#/api/ai-workflow';
import type { InputField } from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  FileOutlined,
  LoadingOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import { message, Upload } from 'ant-design-vue';

import { uploadWorkflowFile } from '#/api/ai-workflow';

import {
  getMaxFileCount,
  getTextMaxLength,
  isArrayInputType,
  isTextInputType,
  normalizeInputFieldType,
} from '../../domain/input-field-type';

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

/**
 * 上传名额预占计数（字段名 -> 进行中的上传数）
 *
 * BUG6：一次选择多个文件时 ant-design-vue 会并发触发多个 custom-request，
 * 若只按“已上传数量”判断上限，所有文件都会放行，导致开始节点配置的
 * 「最多文件数量」不生效，故在上传开始前同步预占名额。
 */
const reservedUploads = reactive<Record<string, number>>({});

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
      // 数组类字段（审批人）空数组也算未填，需显式声明 type 才能被 async-validator 拦住
      fieldRules.push(
        isArrayInputType(field.type)
          ? {
              type: 'array',
              required: true,
              message: `${field.label}不能为空`,
              trigger: 'change',
            }
          : {
              required: true,
              message: `${field.label}不能为空`,
              trigger: field.type === 'SELECT' ? 'change' : 'blur',
            },
      );
    }

    // 文本类型的正则验证（短/长文本已合并为 TEXT）
    if (isTextInputType(field.type) && field.pattern) {
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

/**
 * 浅比较两个表单值对象（输入表单的值均为原始类型/扁平结构）
 * 用于打破「props.values -> formValues -> emit -> props.values」的 watch 回环，
 * 避免 Vue "Maximum recursive updates exceeded" 死循环
 */
function shallowEqualValues(
  a: Record<string, any>,
  b: Record<string, any>,
): boolean {
  const ka = Object.keys(a || {});
  const kb = Object.keys(b || {});
  if (ka.length !== kb.length) return false;
  return ka.every((k) => a[k] === b[k]);
}

// 监听外部 values 变化，同步到内部状态（内容未变化时跳过，防止 watch 回环）
watch(
  () => props.values,
  (newValues) => {
    if (newValues && !shallowEqualValues(newValues, formValues.value)) {
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
 * 空值判断：undefined/null/空串 均视为未填写，此时才用字段默认值预填
 * （v-model 挂载时会先把空串写入 formValues，若只判 undefined 会导致默认值不生效）
 */
function initDefaultValues(fields: InputField[]) {
  fields.forEach((field) => {
    const current = formValues.value[field.name];
    const isEmpty = current === undefined || current === null || current === '';

    // 如果当前值为空且有默认值，则设置默认值
    if (isEmpty && field.defaultValue !== undefined) {
      formValues.value[field.name] = field.defaultValue;
    }

    // 为特定类型设置初始值（重新读取，避免默认值被类型初始值覆盖）
    const afterDefault = formValues.value[field.name];
    if (
      (afterDefault === undefined || afterDefault === null) &&
      field.type === 'CHECKBOX'
    ) {
      formValues.value[field.name] = false;
    }
    if (
      (afterDefault === undefined || afterDefault === null) &&
      (field.type === 'FILE_LIST' || field.type === 'SINGLE_FILE')
    ) {
      formValues.value[field.name] = [];
    }
    // 审批人字段语义上永远是数组，未填时给空数组而不是空串
    if (isArrayInputType(field.type) && !Array.isArray(afterDefault)) {
      formValues.value[field.name] = [];
    }
  });
}

/** 上传结果 → 工作流文件变量（下游节点/文档提取器按 url 读取，fileName 用于下载与删除） */
function toFileVar(info: WorkflowFileUpload) {
  return {
    url: info.url,
    fileName: info.fileName,
    name: info.name,
    size: info.size,
    expiresAt: info.expiresAt,
  };
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

/** 字节数转可读大小（用于校验提示） */
function formatFileSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${size}B`;
}

/**
 * 上传前校验：开始节点配置的文件类型白名单与单文件大小上限。
 *
 * 后端只做存储，不按节点配置校验类型/大小，因此限制只能在这里兑现；
 * 不合规时返回 LIST_IGNORE（既不发请求，也不把被拒文件留在列表里）。
 */
function handleBeforeUpload(file: File, field: InputField): boolean | string {
  const types = (field.allowedFileTypes || [])
    .map((item) => String(item).trim().toLowerCase())
    .filter(Boolean);
  if (types.length > 0) {
    const name = (file.name || '').toLowerCase();
    const mime = (file.type || '').toLowerCase();
    const matched = types.some((item) => {
      if (item === '*' || item === '*/*') return true;
      if (item.startsWith('.')) return name.endsWith(item);
      if (item.includes('/')) return mime === item;
      return name.endsWith(`.${item}`) || mime === item;
    });
    if (!matched) {
      message.warning(
        `「${field.label || field.name}」仅支持 ${types.join(' / ')} 类型的文件`,
      );
      return Upload.LIST_IGNORE;
    }
  }

  const maxSize = Number(field.maxFileSize);
  if (Number.isFinite(maxSize) && maxSize > 0 && file.size > maxSize) {
    message.warning(
      `「${field.label || field.name}」单个文件不能超过 ${formatFileSize(maxSize)}`,
    );
    return Upload.LIST_IGNORE;
  }
  return true;
}

/** 字段限制说明（类型 / 大小 / 数量），与上传校验同一口径 */
function getFileLimitsText(field: InputField): string {
  const parts: string[] = [];
  const types = (field.allowedFileTypes || []).filter(Boolean);
  if (types.length > 0) parts.push(`类型 ${types.join(' / ')}`);
  const maxSize = Number(field.maxFileSize);
  if (Number.isFinite(maxSize) && maxSize > 0) {
    parts.push(`单个≤${formatFileSize(maxSize)}`);
  }
  return parts.join('，');
}

/**
 * 字段说明文字。
 *
 * 历史口径把 description 回填成了 label（编辑器、对话窗口的入参映射都这么写过），
 * 标题已经显示过一遍的名字不能再在输入框下面重复一次；跟变量名同名的说明同理滤掉。
 */
function fieldHelp(field: InputField): string {
  const desc = (field.description || '').trim();
  if (!desc) return '';
  const label = (field.label || '').trim();
  const name = (field.name || '').trim();
  if (desc === label || desc === name) return '';
  return desc;
}

/** 占位提示：说明与标题重复时退回「请输入 xx」，不让输入框里再抄一遍标题 */
function fieldPlaceholder(field: InputField, action = '请输入'): string {
  return fieldHelp(field) || `${action}${field.label || field.name}`;
}

/**
 * 当前字段已占用（已上传 + 上传中）的文件数
 */
function countOccupiedFiles(fieldName: string, isMultiple: boolean): number {
  const value = formValues.value[fieldName];
  let uploaded = 0;
  if (Array.isArray(value)) {
    uploaded = value.length;
  } else if (isMultiple) {
    uploaded = 0;
  } else if (value !== undefined && value !== null && value !== '') {
    uploaded = 1;
  }
  return uploaded + (reservedUploads[fieldName] || 0);
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
  const field = props.fields.find((item) => item.name === fieldName);
  const limit = isMultiple && field ? getMaxFileCount(field) : 1;

  // 同步预占名额：超出「最多文件数量」直接拒绝，不发起上传
  if (countOccupiedFiles(fieldName, isMultiple) >= limit) {
    const error = new Error(`最多上传 ${limit} 个文件`);
    onError(error);
    message.warning(`「${field?.label || fieldName}」最多上传 ${limit} 个文件`);
    return;
  }
  reservedUploads[fieldName] = (reservedUploads[fieldName] || 0) + 1;

  uploadingFields.value[fieldName] = true;

  try {
    // 调用上传 API（返回匿名可访问 url + 有效期，文件名即唯一标识）
    const fileInfo = await uploadWorkflowFile(file);

    // 更新文件列表缓存
    const uploadFile: UploadFile = {
      uid: fileInfo.fileName,
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

    // 更新表单值：下游节点引用的就是这份数据，url 即文件可访问地址
    if (isMultiple) {
      if (!formValues.value[fieldName]) {
        formValues.value[fieldName] = [];
      }
      formValues.value[fieldName].push(toFileVar(fileInfo));
    } else {
      formValues.value[fieldName] = toFileVar(fileInfo);
    }

    onSuccess(fileInfo);
    message.success(`文件 ${fileInfo.name} 上传成功`);
  } catch (error: any) {
    onError(error);
    message.error(`文件上传失败: ${error.message || '未知错误'}`);
  } finally {
    uploadingFields.value[fieldName] = false;
    reservedUploads[fieldName] = Math.max(
      0,
      (reservedUploads[fieldName] || 0) - 1,
    );
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
          (f: { fileName: string }) => f.fileName !== file.uid,
        )
      : undefined;
  // 单文件场景取掉后重置上传中标记，保证重新选择仍能通过名额校验
  if (!isMultiple) {
    reservedUploads[fieldName] = 0;
  }

  return true;
}

/**
 * 字段渲染类型（旧图的 SHORT_TEXT / PARAGRAPH 归一为 TEXT）
 */
function fieldType(field: InputField) {
  return normalizeInputFieldType(field.type);
}

/**
 * 审批人输入归一：纯数字字面量转 number，其余保留字符串并去空白去重。
 * 后端 normalize_approvers 会统一 str 化后取交集，故两种元素类型都安全。
 */
function normalizeApproverList(value: any): Array<number | string> {
  const list = (Array.isArray(value) ? value : [])
    .map((item) => String(item ?? '').trim())
    .filter((item) => item !== '')
    .map((item) => (/^-?\d+$/.test(item) ? Number(item) : item));
  return [...new Set(list.map(String))].map((item) => {
    const matched = list.find((v) => String(v) === item);
    return matched as number | string;
  });
}

function handleApproversChange(field: InputField, value: any) {
  formValues.value[field.name] = normalizeApproverList(value);
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
 *
 * 除了字段值（含默认值回填），已上传文件列表与上传中计数也要清掉：
 * 只清值不清列表的话，输入框空了但文件条目还挂着，下一次发送会带上上一轮的文件。
 */
function resetFields() {
  formRef.value?.resetFields();
  formValues.value = {};
  for (const key of Object.keys(fileListCache)) {
    fileListCache[key] = [];
  }
  for (const key of Object.keys(uploadingFields.value)) {
    uploadingFields.value[key] = false;
  }
  for (const key of Object.keys(reservedUploads)) {
    reservedUploads[key] = 0;
  }
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

/**
 * 是否还有文件在上传中：外层提交前拦一道，
 * 否则可选的文件字段会静默丢掉最后一个还没传完的文件。
 */
function hasUploading(): boolean {
  return Object.values(uploadingFields.value).some(Boolean);
}

// ==================== Expose ====================

defineExpose({
  validate,
  resetFields,
  getValues,
  setValues,
  clearValidate,
  hasUploading,
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
        <span v-if="fieldHelp(field)" class="field-description">
          {{ fieldHelp(field) }}
        </span>
        <span
          v-if="
            fieldType(field) === 'FILE_LIST' ||
            fieldType(field) === 'SINGLE_FILE'
          "
          class="field-description"
        >
          <template v-if="fieldType(field) === 'FILE_LIST'">
            最多上传 {{ getMaxFileCount(field) }} 个文件
          </template>
          <template v-if="getFileLimitsText(field)">
            {{ fieldType(field) === 'FILE_LIST' ? '，' : ''
            }}{{ getFileLimitsText(field) }}
          </template>
        </span>
        <span v-if="fieldType(field) === 'APPROVER'" class="field-description">
          可填写多个（用户 ID 或账号），提交时与审批节点的审批人取交集判定权限
        </span>
      </template>

      <!-- 文本 TEXT（短文本 + 长文本已合并） -->
      <a-textarea
        v-if="fieldType(field) === 'TEXT'"
        v-model:value="formValues[field.name]"
        :rows="3"
        :maxlength="getTextMaxLength(field)"
        :placeholder="fieldPlaceholder(field)"
        show-count
        allow-clear
      />

      <!-- 数字 NUMBER -->
      <a-input-number
        v-else-if="fieldType(field) === 'NUMBER'"
        v-model:value="formValues[field.name]"
        :min="field.minValue"
        :max="field.maxValue"
        :placeholder="fieldPlaceholder(field)"
        style="width: 100%"
      />

      <!-- 下拉选择 SELECT -->
      <a-select
        v-else-if="fieldType(field) === 'SELECT'"
        v-model:value="formValues[field.name]"
        :placeholder="fieldPlaceholder(field, '请选择')"
        allow-clear
      >
        <a-select-option v-for="opt in field.options" :key="opt" :value="opt">
          {{ opt }}
        </a-select-option>
      </a-select>

      <!-- 开关 CHECKBOX（历史类型名，UI 设成开关） -->
      <a-switch
        v-else-if="fieldType(field) === 'CHECKBOX'"
        v-model:checked="formValues[field.name]"
        checked-children="开"
        un-checked-children="关"
      />

      <!-- 单文件上传 SINGLE_FILE -->
      <a-upload
        v-else-if="fieldType(field) === 'SINGLE_FILE'"
        v-model:file-list="fileListCache[field.name]"
        :max-count="1"
        :accept="getAcceptTypes(field)"
        :before-upload="(file: any) => handleBeforeUpload(file, field)"
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
            <a-tag v-if="file.response?.fileName" color="success" size="small">
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
        v-else-if="fieldType(field) === 'FILE_LIST'"
        v-model:file-list="fileListCache[field.name]"
        :max-count="getMaxFileCount(field)"
        :accept="getAcceptTypes(field)"
        :before-upload="(file: any) => handleBeforeUpload(file, field)"
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
            <a-tag v-if="file.response?.fileName" color="success" size="small">
              已上传
            </a-tag>
            <a-button type="link" size="small" danger @click="actions.remove">
              <DeleteOutlined />
            </a-button>
          </div>
        </template>
      </a-upload>

      <!-- 审批人 APPROVER（值永远是数组，元素可数字可字符串） -->
      <a-select
        v-else-if="fieldType(field) === 'APPROVER'"
        mode="tags"
        :value="(formValues[field.name] || []).map((item: any) => String(item))"
        :placeholder="
          fieldPlaceholder(field, '请输入审批人 ID / 账号，回车确认')
        "
        :token-separators="[',', ' ', ';']"
        style="width: 100%"
        @change="(val: any) => handleApproversChange(field, val)"
      />
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

  :deep(.ant-switch) {
    font-size: 13px;
  }
}
</style>
