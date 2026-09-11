<script setup lang="ts">
/**
 * Code 节点配置表单
 * Python 代码执行（参照 MaxKB ToolExecutor）：
 * - 代码块直接写 import + def 方法，入口函数自动识别（main 优先，无 main 取最后定义的顶层函数）
 * - 参数：参数名 / 类型 / 是否必填 / 来源（引用参数 或 自定义值）
 * - 返回值任意类型，节点输出统一为 { result: <返回值> }
 */
import type {
  CodeInputSource,
  CodeInputVariable,
  CodeNodeConfig,
  CodeParameterType,
} from '#/api/ai-workflow/types';

import {
  DeleteOutlined,
  FullscreenOutlined,
  HolderOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { reactive, ref, watch } from 'vue';
import draggable from 'vuedraggable';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: CodeNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: CodeNodeConfig): void;
}>();

// 参数类型选项
const PARAM_TYPE_OPTIONS: { value: CodeParameterType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔值' },
  { value: 'array', label: '数组' },
  { value: 'object', label: '对象' },
];

// 表单数据
const formData = reactive<CodeNodeConfig>({
  code: '',
  inputs: [],
  timeout: 10000,
});

// 代码全屏编辑弹窗
const codeModalOpen = ref(false);

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      code: config.code || '',
      inputs: (config.inputs || []).map((p) => ({ ...p, id: p.id || genParamId() })),
      timeout: config.timeout ?? 10000,
    });
  },
  { immediate: true, deep: true },
);

/** 生成参数行稳定 key（拖拽/渲染用） */
function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 默认新参数 */
function createParameter(): CodeInputVariable {
  return {
    id: genParamId(),
    name: '',
    type: 'string',
    required: false,
    sourceType: 'REFERENCE',
    sourceVariable: '',
  };
}

// 添加参数
function addParameter() {
  formData.inputs = formData.inputs || [];
  formData.inputs.push(createParameter());
  handleChange();
}

// 移除参数
function removeParameter(index: number) {
  formData.inputs?.splice(index, 1);
  handleChange();
}

/** 自定义值输入提示（按类型） */
function typeHint(type?: CodeParameterType): string {
  switch (type) {
    case 'array':
    case 'object':
      return '请输入 JSON 字符串，如 [1,2,3] 或 {"a":1}';
    case 'number':
      return '请输入数字，执行时自动转换';
    case 'boolean':
      return '请输入 true / false';
    default:
      return '字符串直接输入即可';
  }
}

// 处理配置变更
function handleChange() {
  const config: CodeNodeConfig = {
    language: 'PYTHON',
    code: formData.code || '',
    // 不过滤空名参数：刚添加的参数 name 为空，过滤会导致 config 回写时
    // watch 重置 formData，新加的参数行被冲掉（“添加参数没反应”的根因）；
    // 空名参数由后端执行时跳过（if not name: continue）
    inputs: (formData.inputs || []).map((p) => ({
      id: p.id,
      name: p.name || '',
      type: (p.type || 'string') as CodeParameterType,
      required: !!p.required,
      sourceType: ((p.sourceType === 'CONSTANT' ? 'CONSTANT' : 'REFERENCE') as CodeInputSource),
      sourceVariable: p.sourceType === 'CONSTANT' ? undefined : (p.sourceVariable || ''),
      value: p.sourceType === 'CONSTANT' ? p.value : undefined,
    })),
    timeout: formData.timeout ?? 10000,
  };
  emit('update:config', config);
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="代码" required>
      <div class="code-editor-wrap">
        <a-textarea
          v-model:value="formData.code"
          :rows="10"
          class="code-editor"
          spellcheck="false"
          placeholder="import json&#10;&#10;def main(**kwargs):&#10;    # kwargs 中注入下方「参数定义」配置的参数&#10;    return 'hello'"
          @change="handleChange"
        />
        <a-tooltip title="放大编辑">
          <a-button
            type="text"
            class="code-expand-btn"
            @click="codeModalOpen = true"
          >
            <template #icon>
              <FullscreenOutlined />
            </template>
          </a-button>
        </a-tooltip>
      </div>
      <div class="form-hint">直接写 import + def 方法；入口函数自动识别：main 优先，无 main 取最后定义的顶层函数</div>
    </a-form-item>

    <!-- 代码全屏编辑 -->
    <a-modal
      v-model:open="codeModalOpen"
      title="代码编辑（Python）"
      :fullscreen="true"
      :footer="null"
      class="code-fullscreen-modal"
    >
      <div class="fullscreen-editor">
        <a-textarea
          v-model:value="formData.code"
          :rows="32"
          autofocus
          class="code-editor"
          spellcheck="false"
          placeholder="import json&#10;&#10;def main(**kwargs):&#10;    # kwargs 中注入下方「参数定义」配置的参数&#10;    return 'hello'"
          @change="handleChange"
        />
        <div class="form-hint">
          入口方法通过 kwargs 接收参数（与下方「参数定义」一一对应）；返回值任意类型，
          节点输出统一为 <code v-pre>{{nodeId.result}}</code>，下游直接引用
        </div>
      </div>
    </a-modal>

    <a-divider orientation="left" style="margin: 4px 0 12px; font-size: 12px">
      参数定义
    </a-divider>

    <div class="parameters-section">
      <draggable
        v-model="formData.inputs"
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
                    @change="handleChange"
                  >
                    必填
                  </a-checkbox>
                </a-col>
              </a-row>
              <div class="source-row">
                <a-radio-group
                  v-model:value="param.sourceType"
                  size="small"
                  class="source-type"
                  @change="handleChange"
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
                  @change="handleChange"
                />
                <div class="form-hint">{{ typeHint(param.type) }}</div>
              </template>
              <template v-else>
                <VariableInput
                  v-model="param.sourceVariable"
                  :current-node-id="nodeId"
                  :placeholder="'{{nodeName.output}}'"
                  @change="handleChange"
                />
              </template>
            </div>
          </div>
        </template>
      </draggable>

      <a-button type="dashed" size="small" block @click="addParameter">
        <PlusOutlined /> 添加参数
      </a-button>
    </div>

    <a-divider orientation="left" style="margin: 16px 0 12px; font-size: 12px">
      执行设置
    </a-divider>

    <a-form-item label="超时时间（毫秒）">
      <a-input-number
        v-model:value="formData.timeout"
        :min="100"
        :max="600000"
        :step="1000"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <a-alert type="info" show-icon class="code-help">
      <template #message>代码节点说明</template>
      <template #description>
        <ul class="help-list">
          <li>
            入口方法入参通过 <code>kwargs</code> 注入，与「参数定义」一一对应：
            <code>def main(**kwargs)</code>（无 main 时自动取最后定义的顶层函数）
          </li>
          <li>
            支持 <code>import</code> 导包（如 json/math/re/random/collections 等常用库，
            危险模块拦截）
          </li>
          <li>
            返回值类型不限，节点输出统一为 <code>{ result: 返回值 }</code>，
            下游用 <code v-pre>{{codeNodeId.result}}</code> 引用
          </li>
          <li>仅支持 Python；输出限制：字符串 ≤200KB / 数组 ≤100 元素</li>
          <li>代码统一书写规范：import + def 方法，返回值在入口方法内 return</li>
        </ul>
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

  .code-editor-wrap {
    position: relative;

    .code-expand-btn {
      position: absolute;
      top: 4px;
      right: 4px;
      color: #bfbfbf;

      &:hover:not(:disabled) {
        color: #1890ff;
        background-color: rgba(24, 144, 255, 0.1);
      }
    }
  }

  // 代码编辑器：等宽深色主题
  .code-editor {
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', Consolas, monospace;
    font-size: 12px;
    line-height: 1.5;
    background-color: #1e1e1e;
    color: #d4d4d4;
    border-color: #333;

    &::placeholder {
      color: #6a6a6a;
    }

    &:hover,
    &:focus {
      border-color: #1890ff;
    }
  }

  // 全屏编辑弹窗内
  .fullscreen-editor {
    padding: 8px 4px 0;

    .code-editor {
      font-size: 13px;
      line-height: 1.6;
    }
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
        .source-row {
          margin: 8px 0;

          .source-type {
            :deep(.ant-radio-button-wrapper) {
              font-size: 12px;
            }
          }
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

  .code-help {
    margin-top: 8px;

    .help-list {
      padding-left: 16px;
      margin: 0;
      font-size: 12px;

      li {
        margin-bottom: 4px;

        &:last-child {
          margin-bottom: 0;
        }
      }

      code {
        padding: 1px 4px;
        font-family: monospace;
        background-color: #f5f5f5;
        border-radius: 2px;
      }
    }
  }
}

:deep(.code-fullscreen-modal) {
  .ant-modal-content {
    padding: 16px;
  }
}
</style>