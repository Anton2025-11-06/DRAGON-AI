<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 输入字段列表 -->
    <div class="fields-section">
      <div class="section-header">
        <span class="section-title">输入字段</span>
        <a-button type="primary" size="small" @click="addField">
          <template #icon><PlusOutlined /></template>
          添加字段
        </a-button>
      </div>

      <div
        v-if="formData.fields && formData.fields.length > 0"
        class="fields-list"
      >
        <draggable
          v-model="formData.fields"
          item-key="name"
          handle=".drag-handle"
          @end="handleChange"
        >
          <template #item="{ element, index }">
            <div class="field-item">
              <div class="field-header">
                <HolderOutlined class="drag-handle" />
                <span class="field-index">{{ index + 1 }}</span>
                <a-tag :color="getFieldTypeColor(element.type)" size="small">
                  {{ getFieldTypeLabel(element.type) }}
                </a-tag>
                <span class="field-name">{{
                  element.name || '未命名字段'
                }}</span>
                <a-tag v-if="element.required" color="red" size="small"
                  >必填</a-tag
                >
                <div class="field-actions">
                  <a-button type="text" size="small" @click="editField(index)">
                    <EditOutlined />
                  </a-button>
                  <a-button
                    type="text"
                    size="small"
                    danger
                    @click="removeField(index)"
                  >
                    <DeleteOutlined />
                  </a-button>
                </div>
              </div>
              <div v-if="element.description" class="field-description">
                {{ element.description }}
              </div>
            </div>
          </template>
        </draggable>
      </div>

      <a-empty v-else description="暂无输入字段，点击上方按钮添加" />
    </div>

    <!-- 字段编辑弹窗 -->
    <a-modal
      v-model:open="fieldModalVisible"
      :title="editingFieldIndex >= 0 ? '编辑字段' : '添加字段'"
      :width="560"
      @ok="saveField"
      @cancel="cancelFieldEdit"
    >
      <a-form layout="vertical" :model="editingField" class="field-edit-form">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="字段名称" required>
              <a-input
                v-model:value="editingField.name"
                placeholder="变量名，如: query"
              />
              <div class="form-hint">用于在工作流中引用此字段</div>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="显示标签" required>
              <a-input
                v-model:value="editingField.label"
                placeholder="显示名称，如: 查询内容"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="字段类型" required>
          <a-select
            v-model:value="editingField.type"
            placeholder="选择字段类型"
          >
            <a-select-option value="SHORT_TEXT">
              <FontSizeOutlined /> 短文本
            </a-select-option>
            <a-select-option value="PARAGRAPH">
              <AlignLeftOutlined /> 长文本
            </a-select-option>
            <a-select-option value="NUMBER">
              <NumberOutlined /> 数字
            </a-select-option>
            <a-select-option value="SELECT">
              <UnorderedListOutlined /> 下拉选择
            </a-select-option>
            <a-select-option value="CHECKBOX">
              <CheckSquareOutlined /> 复选框
            </a-select-option>
            <a-select-option value="SINGLE_FILE">
              <FileOutlined /> 单文件
            </a-select-option>
            <a-select-option value="FILE_LIST">
              <FolderOutlined /> 多文件
            </a-select-option>
          </a-select>
        </a-form-item>

        <a-form-item label="字段描述">
          <a-textarea
            v-model:value="editingField.description"
            :rows="2"
            placeholder="描述此字段的用途"
          />
        </a-form-item>

        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="是否必填">
              <a-switch v-model:checked="editingField.required" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="默认值">
              <a-input
                v-model:value="editingField.defaultValue"
                placeholder="可选"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <!-- 文本类型特有配置 -->
        <template
          v-if="
            editingField.type === 'SHORT_TEXT' ||
            editingField.type === 'PARAGRAPH'
          "
        >
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="最大长度">
                <a-input-number
                  v-model:value="editingField.maxLength"
                  :min="1"
                  :max="editingField.type === 'SHORT_TEXT' ? 256 : 100000"
                  placeholder="可选"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="正则验证">
                <a-input
                  v-model:value="editingField.pattern"
                  placeholder="如: ^[a-zA-Z]+$"
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item v-if="editingField.pattern" label="验证失败提示">
            <a-input
              v-model:value="editingField.patternMessage"
              placeholder="格式不正确"
            />
          </a-form-item>
        </template>

        <!-- 数字类型特有配置 -->
        <template v-if="editingField.type === 'NUMBER'">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="最小值">
                <a-input-number
                  v-model:value="editingField.minValue"
                  placeholder="可选"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="最大值">
                <a-input-number
                  v-model:value="editingField.maxValue"
                  placeholder="可选"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
          </a-row>
        </template>

        <!-- 下拉选择类型特有配置 -->
        <template v-if="editingField.type === 'SELECT'">
          <a-form-item label="选项列表" required>
            <a-select
              v-model:value="editingField.options"
              mode="tags"
              placeholder="输入选项后按回车添加"
              style="width: 100%"
            />
            <div class="form-hint">输入选项后按回车添加，可添加多个选项</div>
          </a-form-item>
        </template>

        <!-- 文件类型特有配置 -->
        <template
          v-if="
            editingField.type === 'SINGLE_FILE' ||
            editingField.type === 'FILE_LIST'
          "
        >
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="允许的文件类型">
                <a-select
                  v-model:value="editingField.allowedFileTypes"
                  mode="tags"
                  placeholder="如: .pdf, .docx"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="最大文件大小(MB)">
                <a-input-number
                  v-model:value="maxFileSizeMB"
                  :min="1"
                  :max="100"
                  placeholder="默认10MB"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item
            v-if="editingField.type === 'FILE_LIST'"
            label="最大文件数量"
          >
            <a-input-number
              v-model:value="editingField.maxFileCount"
              :min="1"
              :max="20"
              placeholder="默认5"
              style="width: 100%"
            />
          </a-form-item>
        </template>
      </a-form>
    </a-modal>

    <!-- 使用说明 -->
    <a-alert type="info" show-icon class="start-help">
      <template #message>START 节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>定义工作流的输入参数，用户执行工作流时需要填写这些字段</li>
          <li>
            字段名称用于在下游节点中引用，格式:
            <code v-pre>{{ start.fieldName }}</code>
          </li>
          <li>拖拽字段可调整顺序</li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * START 节点配置表单
 * 定义工作流输入字段，支持多种输入类型和字段拖拽排序
 */
import type {
  InputField,
  InputFieldType,
  StartNodeConfig,
} from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import {
  AlignLeftOutlined,
  CheckSquareOutlined,
  DeleteOutlined,
  EditOutlined,
  FileOutlined,
  FolderOutlined,
  FontSizeOutlined,
  HolderOutlined,
  NumberOutlined,
  PlusOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons-vue';
import draggable from 'vuedraggable';

// Props
interface Props {
  config: StartNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: StartNodeConfig): void;
}>();

// 表单数据
const formData = reactive<StartNodeConfig>({
  fields: [],
});

// 字段编辑弹窗
const fieldModalVisible = ref(false);
const editingFieldIndex = ref(-1);
const editingField = reactive<InputField>({
  name: '',
  label: '',
  type: 'SHORT_TEXT',
  required: false,
  defaultValue: undefined,
  description: '',
  options: [],
  maxLength: undefined,
  minValue: undefined,
  maxValue: undefined,
  allowedFileTypes: [],
  maxFileSize: undefined,
  maxFileCount: undefined,
  pattern: undefined,
  patternMessage: undefined,
});

// 文件大小（MB）
const maxFileSizeMB = computed({
  get: () =>
    editingField.maxFileSize
      ? editingField.maxFileSize / (1024 * 1024)
      : undefined,
  set: (val) => {
    editingField.maxFileSize = val ? val * 1024 * 1024 : undefined;
  },
});

// 字段类型标签映射
const fieldTypeLabels: Record<InputFieldType, string> = {
  SHORT_TEXT: '短文本',
  PARAGRAPH: '长文本',
  NUMBER: '数字',
  SELECT: '下拉选择',
  CHECKBOX: '复选框',
  SINGLE_FILE: '单文件',
  FILE_LIST: '多文件',
};

// 字段类型颜色映射
const fieldTypeColors: Record<InputFieldType, string> = {
  SHORT_TEXT: 'blue',
  PARAGRAPH: 'cyan',
  NUMBER: 'green',
  SELECT: 'purple',
  CHECKBOX: 'orange',
  SINGLE_FILE: 'magenta',
  FILE_LIST: 'red',
};

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    formData.fields = config.fields ? [...config.fields] : [];
  },
  { immediate: true, deep: true },
);

/**
 * 获取字段类型标签
 */
function getFieldTypeLabel(type: InputFieldType): string {
  return fieldTypeLabels[type] || type;
}

/**
 * 获取字段类型颜色
 */
function getFieldTypeColor(type: InputFieldType): string {
  return fieldTypeColors[type] || 'default';
}

/**
 * 添加字段
 */
function addField() {
  editingFieldIndex.value = -1;
  Object.assign(editingField, {
    name: '',
    label: '',
    type: 'SHORT_TEXT',
    required: false,
    defaultValue: undefined,
    description: '',
    options: [],
    maxLength: undefined,
    minValue: undefined,
    maxValue: undefined,
    allowedFileTypes: [],
    maxFileSize: undefined,
    maxFileCount: undefined,
    pattern: undefined,
    patternMessage: undefined,
  });
  fieldModalVisible.value = true;
}

/**
 * 编辑字段
 */
function editField(index: number) {
  editingFieldIndex.value = index;
  const field = formData.fields?.[index];
  if (!field) {
    return;
  }
  Object.assign(editingField, {
    name: field.name || '',
    label: field.label || '',
    type: field.type || 'SHORT_TEXT',
    required: field.required || false,
    defaultValue: field.defaultValue,
    description: field.description || '',
    options: field.options || [],
    maxLength: field.maxLength,
    minValue: field.minValue,
    maxValue: field.maxValue,
    allowedFileTypes: field.allowedFileTypes || [],
    maxFileSize: field.maxFileSize,
    maxFileCount: field.maxFileCount,
    pattern: field.pattern,
    patternMessage: field.patternMessage,
  });
  fieldModalVisible.value = true;
}

/**
 * 保存字段
 */
function saveField() {
  if (!editingField.name || !editingField.label) {
    return;
  }

  const field: InputField = {
    name: editingField.name,
    label: editingField.label,
    type: editingField.type,
    required: editingField.required,
    defaultValue: editingField.defaultValue,
    description: editingField.description,
  };

  // 根据类型添加特定配置
  if (editingField.type === 'SHORT_TEXT' || editingField.type === 'PARAGRAPH') {
    if (editingField.maxLength) field.maxLength = editingField.maxLength;
    if (editingField.pattern) {
      field.pattern = editingField.pattern;
      field.patternMessage = editingField.patternMessage;
    }
  } else if (editingField.type === 'NUMBER') {
    if (editingField.minValue !== undefined)
      field.minValue = editingField.minValue;
    if (editingField.maxValue !== undefined)
      field.maxValue = editingField.maxValue;
  } else if (editingField.type === 'SELECT') {
    field.options = editingField.options;
  } else if (
    editingField.type === 'SINGLE_FILE' ||
    editingField.type === 'FILE_LIST'
  ) {
    if (editingField.allowedFileTypes?.length)
      field.allowedFileTypes = editingField.allowedFileTypes;
    if (editingField.maxFileSize) field.maxFileSize = editingField.maxFileSize;
    if (editingField.type === 'FILE_LIST' && editingField.maxFileCount) {
      field.maxFileCount = editingField.maxFileCount;
    }
  }

  if (!formData.fields) {
    formData.fields = [];
  }

  if (editingFieldIndex.value >= 0) {
    formData.fields[editingFieldIndex.value] = field;
  } else {
    formData.fields.push(field);
  }

  fieldModalVisible.value = false;
  handleChange();
}

/**
 * 取消字段编辑
 */
function cancelFieldEdit() {
  fieldModalVisible.value = false;
}

/**
 * 删除字段
 */
function removeField(index: number) {
  formData.fields?.splice(index, 1);
  handleChange();
}

/**
 * 处理配置变更
 */
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }
}

.fields-section {
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;

    .section-title {
      font-size: 13px;
      font-weight: 500;
      color: #262626;
    }
  }

  .fields-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .field-item {
    padding: 10px 12px;
    background-color: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 6px;
    transition: all 0.2s;

    &:hover {
      background-color: #f5f5f5;
      border-color: #d9d9d9;
    }

    .field-header {
      display: flex;
      gap: 8px;
      align-items: center;

      .drag-handle {
        color: #bfbfbf;
        cursor: grab;

        &:hover {
          color: #8c8c8c;
        }
      }

      .field-index {
        width: 20px;
        height: 20px;
        font-size: 11px;
        line-height: 20px;
        color: #8c8c8c;
        text-align: center;
        background-color: #e6e6e6;
        border-radius: 50%;
      }

      .field-name {
        flex: 1;
        font-size: 13px;
        font-weight: 500;
        color: #262626;
      }

      .field-actions {
        display: flex;
        gap: 4px;
        opacity: 0;
        transition: opacity 0.2s;
      }
    }

    &:hover .field-actions {
      opacity: 1;
    }

    .field-description {
      margin-top: 6px;
      margin-left: 28px;
      font-size: 12px;
      color: #8c8c8c;
    }
  }
}

.field-edit-form {
  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }
}

.start-help {
  margin-top: 16px;

  .help-list {
    padding-left: 16px;
    margin: 0;
    font-size: 12px;

    li {
      margin-bottom: 4px;

      &:last-child {
        margin-bottom: 0;
      }

      code {
        padding: 2px 6px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 3px;
      }
    }
  }
}
</style>
