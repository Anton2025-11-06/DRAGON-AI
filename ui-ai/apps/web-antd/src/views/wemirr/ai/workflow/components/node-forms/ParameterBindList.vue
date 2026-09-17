<script setup lang="ts">
/**
 * 参数绑定行列表（CODE 节点参数区同款卡片，供 TOOL / MCP_TOOL 节点表单复用）
 *
 * 行结构统一口径：参数名 / 类型 / 是否必填 / 来源（引用参数 或 自定义值），
 * 与后端 py_sandbox.resolve_kwargs 消费的 inputs 字段一一对应：
 * {name,type,required,sourceType,sourceVariable,value}
 * 参数行由父组件通过 v-model:params 持有，任一行变动后 emit change 让父组件回写节点配置。
 */
import type {
  CodeInputSource,
  CodeInputVariable,
  CodeParameterType,
} from '#/api/ai-workflow/types';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

interface Props {
  /** 当前节点 id（变量选择器据此排除自身输出） */
  currentNodeId: string;
  /** 参数名 → 说明（工具定义 / MCP inputSchema 提供，展示在参数名下） */
  descriptions?: Record<string, string>;
}

withDefaults(defineProps<Props>(), {
  descriptions: () => ({}),
});

const emit = defineEmits<{
  (e: 'change'): void;
}>();

/** 参数绑定行 */
const params = defineModel<CodeInputVariable[]>('params', { required: true });

const PARAM_TYPE_OPTIONS: { label: string; value: CodeParameterType }[] = [
  { label: '字符串', value: 'string' },
  { label: '数字', value: 'number' },
  { label: '布尔值', value: 'boolean' },
  { label: '数组', value: 'array' },
  { label: '对象', value: 'object' },
];

function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 自定义值输入提示（按类型） */
function typeHint(type?: CodeParameterType): string {
  switch (type) {
    case 'array':
    case 'object': {
      return '请输入 JSON 字符串，如 [1,2,3] 或 {"a":1}';
    }
    case 'boolean': {
      return '请输入 true / false';
    }
    case 'number': {
      return '请输入数字，执行时自动转换';
    }
    default: {
      return '字符串直接输入即可';
    }
  }
}

function addParameter() {
  params.value.push({
    id: genParamId(),
    name: '',
    required: false,
    sourceType: 'REFERENCE' as CodeInputSource,
    type: 'string' as CodeParameterType,
  });
  emit('change');
}

function removeParameter(index: number) {
  params.value.splice(index, 1);
  emit('change');
}
</script>

<template>
  <div class="parameters-section">
    <div
      v-for="(param, index) in params"
      :key="param.id || `${param.name}-${index}`"
      class="parameter-item"
    >
      <div class="parameter-header">
        <span class="parameter-index">参数 {{ index + 1 }}</span>
        <a-button
          danger
          size="small"
          type="text"
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
              @change="emit('change')"
            />
          </a-col>
          <a-col :span="8">
            <a-select
              v-model:value="param.type"
              size="small"
              style="width: 100%"
              @change="emit('change')"
            >
              <a-select-option
                v-for="opt in PARAM_TYPE_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </a-select-option>
            </a-select>
          </a-col>
          <a-col :span="6">
            <a-checkbox
              v-model:checked="param.required"
              size="small"
              @change="emit('change')"
            >
              必填
            </a-checkbox>
          </a-col>
        </a-row>
        <div v-if="descriptions[param.name || '']" class="param-desc">
          {{ descriptions[param.name || ''] }}
        </div>
        <div class="source-row">
          <a-radio-group
            v-model:value="param.sourceType"
            size="small"
            class="source-type"
            @change="emit('change')"
          >
            <a-radio-button value="REFERENCE">引用参数</a-radio-button>
            <a-radio-button value="CONSTANT">自定义值</a-radio-button>
          </a-radio-group>
        </div>
        <template v-if="param.sourceType === 'CONSTANT'">
          <a-input
            v-model:value="param.value"
            placeholder="自定义值"
            size="small"
            @change="emit('change')"
          />
          <div class="form-hint">{{ typeHint(param.type) }}</div>
        </template>
        <template v-else>
          <VariableInput
            v-model="param.sourceVariable"
            :current-node-id="currentNodeId"
            placeholder="{{nodeName.output}}"
            @change="emit('change')"
          />
        </template>
      </div>
    </div>

    <a-button block dashed size="small" type="dashed" @click="addParameter">
      <PlusOutlined /> 添加参数
    </a-button>
  </div>
</template>

<style scoped lang="less">
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

      .parameter-index {
        flex: 1;
        font-size: 12px;
        font-weight: 500;
        color: #595959;
      }
    }

    .parameter-content {
      .source-row {
        margin: 8px 0;

        .source-type {
          :deep(.ant-radio-button-wrapper) {
            font-size: 12px;
          }
        }
      }

      .param-desc {
        margin-top: 4px;
        font-size: 11px;
        color: #8c8c8c;
      }

      .form-hint {
        margin-top: 4px;
        font-size: 11px;
        color: #8c8c8c;
      }

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
</style>
