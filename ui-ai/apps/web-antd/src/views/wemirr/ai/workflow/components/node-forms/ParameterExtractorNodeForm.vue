<script setup lang="ts">
import type {
  ExtractParameter,
  ParameterExtractorConfig,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import {
  DeleteOutlined,
  HolderOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import draggable from 'vuedraggable';

/**
 * 参数提取器节点配置表单
 * 使用 LLM（文生文）从自然语言文本中提取结构化参数
 * 【设计对齐 2026-09】模型固定为 text_to_text，「直连/后缀」概念已废弃
 */
import { CHAT_TYPE_OPTIONS, MT_TEXT_TO_TEXT } from '#/api/ai-workflow/const';

import { ModelSelect } from '../model-select';
import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: ParameterExtractorConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ParameterExtractorConfig): void;
}>();

/** 生成参数行稳定 key（拖拽/渲染用） */
function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 新建参数行：每次给新对象，避开模块级共享引用被互相改掉 */
function createParameter(name = ''): ExtractParameter {
  return {
    id: genParamId(),
    name,
    type: 'string',
    description: '',
    required: false,
  };
}

// 表单数据
const formData = reactive<ParameterExtractorConfig>({
  modelId: undefined,
  modelType: MT_TEXT_TO_TEXT,
  inputVariable: '',
  instructions: '',
  parameters: [createParameter('param1')],
});

/**
 * 本地刚 emit 出去的配置快照（nodeId + 配置内容）。
 * 父层（PropertyPanel / selectedNode.data）会把自己刚收到的配置原样回传，若不加守卫，
 * 回传值会立即重建 formData.parameters（连对象身份一起换掉），参数行的 a-input 在键入
 * 过程中被销毁重建：光标与选区丢失（表现为“输入的内容跳出输入栏”），且 ant-design-vue
 * vc-input 的 setValue 在 nextTick 里读已销毁实例的 inputRef 会抛
 * 「Cannot read properties of null (reading 'input')」，整个表单渲染失败。
 */
let emittedSignature = '';

/** 配置内容签名（含参数行 id，能识别自己的回传，也覆盖标量字段的外部变更） */
function signatureOf(config: ParameterExtractorConfig | undefined): string {
  return JSON.stringify([
    config?.modelId ?? null,
    config?.inputVariable || '',
    config?.instructions || '',
    (config?.parameters || []).map((p) => [
      p.id || '',
      p.name || '',
      p.type || 'string',
      p.description || '',
      !!p.required,
      p.enumValues || [],
    ]),
  ]);
}

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    if (`${props.nodeId}|${signatureOf(config)}` === emittedSignature) {
      // 自己刚 emit 的内容，保留本地编辑状态与参数行对象身份，不重建列表
      return;
    }
    emittedSignature = '';
    Object.assign(formData, {
      modelId: config.modelId,
      modelType: config.modelType || MT_TEXT_TO_TEXT,
      inputVariable: config.inputVariable || '',
      instructions: config.instructions || '',
      parameters:
        config.parameters && config.parameters.length > 0
          ? config.parameters.map((p) => ({ ...p, id: p.id || genParamId() }))
          : [createParameter('param1')],
    });
  },
  { immediate: true, deep: true },
);

// 添加参数
function addParameter() {
  formData.parameters = formData.parameters || [];
  formData.parameters.push(
    createParameter(`param${formData.parameters.length + 1}`),
  );
  handleChange();
}

// 移除参数
function removeParameter(index: number) {
  formData.parameters?.splice(index, 1);
  handleChange();
}

// 处理配置变更
function handleChange() {
  const config: ParameterExtractorConfig = {
    modelId: formData.modelId,
    modelType: MT_TEXT_TO_TEXT,
    inputVariable: formData.inputVariable,
    instructions: formData.instructions,
    // 不过滤空名参数：a-input 每次键入都会触发 change，过滤会让 config 回写时
    // 把正在编辑的参数行连带卸载，ant-design-vue 在 nextTick 里读已销毁实例的
    // inputRef 就抛「Cannot read properties of null (reading 'input')」；
    // 空名行属于待配置状态，由变量下拉与保存校验排除，后端执行时跳过
    // （回写本身由上方 emittedSignature 守卫拦住，两层一起才不会重建参数行）
    parameters: (formData.parameters || []).map((p) => ({
      id: p.id,
      name: p.name || '',
      type: p.type || 'string',
      description: p.description || '',
      required: !!p.required,
      enumValues: p.enumValues,
    })),
  };
  emittedSignature = `${props.nodeId}|${signatureOf(config)}`;
  emit('update:config', config);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <ModelSelect
      v-model:model-value="formData.modelId"
      v-model:model-type="formData.modelType"
      :type-options="CHAT_TYPE_OPTIONS"
      placeholder="选择用于参数提取的模型"
      @change="handleChange"
    />

    <a-form-item label="输入变量" required>
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        placeholder="{{start.text}} 或 {{nodeName.output}}"
        @change="handleChange"
      />
      <div class="form-hint">输入要提取参数的文本变量引用</div>
    </a-form-item>

    <a-form-item label="提取指导说明">
      <a-textarea
        v-model:value="formData.instructions"
        :rows="3"
        placeholder="描述要提取的参数和提取规则，帮助模型更准确地提取"
        @change="handleChange"
      />
      <div class="form-hint">
        固定以 Prompt 方式提取：提示词里带上参数 schema，并约束模型输出 JSON
      </div>
    </a-form-item>

    <!-- 参数结构定义 -->
    <a-divider orientation="left" style="margin: 16px 0 12px; font-size: 12px">
      参数结构定义
    </a-divider>

    <div class="parameters-section">
      <draggable
        v-model="formData.parameters"
        item-key="id"
        handle=".drag-handle"
        @change="handleChange"
      >
        <template #item="{ element: param, index }">
          <div class="parameter-item">
            <div class="parameter-header">
              <HolderOutlined class="drag-handle" />
              <span class="parameter-index">参数 {{ index + 1 }}</span>
              <a-button
                type="text"
                danger
                size="small"
                @click="removeParameter(index)"
              >
                <DeleteOutlined />
              </a-button>
            </div>
            <div class="parameter-content">
              <a-row :gutter="8">
                <a-col :span="10">
                  <a-input
                    v-model:value="param.name"
                    placeholder="参数名"
                    size="small"
                    @change="handleChange"
                  />
                </a-col>
                <a-col :span="8">
                  <a-select
                    v-model:value="param.type"
                    size="small"
                    style="width: 100%"
                    @change="handleChange"
                  >
                    <a-select-option value="string">字符串</a-select-option>
                    <a-select-option value="number">数字</a-select-option>
                    <a-select-option value="boolean">布尔值</a-select-option>
                    <a-select-option value="array">数组</a-select-option>
                    <a-select-option value="object">对象</a-select-option>
                  </a-select>
                </a-col>
                <a-col :span="6">
                  <a-checkbox
                    v-model:checked="param.required"
                    size="small"
                    @change="handleChange"
                  >
                    必填
                  </a-checkbox>
                </a-col>
              </a-row>
              <a-input
                v-model:value="param.description"
                placeholder="参数描述（帮助模型理解要提取什么）"
                size="small"
                style="margin-top: 8px"
                @change="handleChange"
              />
              <a-select
                v-if="param.type === 'string'"
                v-model:value="param.enumValues"
                mode="tags"
                placeholder="枚举值（可选）"
                size="small"
                style="width: 100%; margin-top: 8px"
                @change="handleChange"
              />
            </div>
          </div>
        </template>
      </draggable>

      <a-button type="dashed" size="small" block @click="addParameter">
        <PlusOutlined /> 添加参数
      </a-button>
    </div>

    <div class="form-hint" style="margin-top: 8px">
      每个参数将作为独立的输出变量，可在下游节点中引用
    </div>

    <!-- 说明：平台无会话级存储，不提供“对话记忆”配置（后端也不读该字段） -->
    <a-alert type="info" show-icon style="margin-top: 16px">
      <template #message>
        <span style="font-size: 12px">
          提取结果将包含内置状态变量：<code>__is_success</code>（是否成功）和
          <code>__reason</code>（失败原因）；提取未成功时节点不报错，可在下游用
          <code>__is_success</code> 做条件分支
        </span>
      </template>
    </a-alert>
  </a-form>
</template>

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

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  .parameters-section {
    .parameter-item {
      margin-bottom: 12px;
      padding: 12px;
      background: #fafafa;
      border: 1px solid #f0f0f0;
      border-radius: 6px;

      .parameter-header {
        display: flex;
        align-items: center;
        margin-bottom: 8px;

        .drag-handle {
          cursor: move;
          color: #bfbfbf;
          margin-right: 8px;

          &:hover {
            color: #1890ff;
          }
        }

        .parameter-index {
          flex: 1;
          font-size: 12px;
          font-weight: 500;
          color: #595959;
        }
      }

      .parameter-content {
        :deep(.ant-input) {
          font-size: 12px;
        }

        :deep(.ant-select) {
          font-size: 12px;
        }

        :deep(.ant-checkbox-wrapper) {
          font-size: 12px;
        }
      }
    }
  }

  :deep(.ant-alert-message) {
    code {
      background: #f5f5f5;
      padding: 2px 4px;
      border-radius: 3px;
      font-family: monospace;
    }
  }
}
</style>
