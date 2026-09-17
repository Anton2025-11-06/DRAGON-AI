<script lang="ts" setup>
/**
 * 工具新增 / 编辑弹窗
 * 排版对齐工作流「代码执行」节点（深色代码框 + 参数定义卡片 + 执行设置），
 * 底部「测试」放在「确定」左边：代码不保存也能直接跑一次，避开“先存再测”的往返。
 * 参数定义存进 parameters_schema：{"parameters":[{name,type,required,default,description}]}
 * —— 工具页据此渲染测试表单，工作流工具节点据此渲染参数绑定行。
 */
import type { ToolParamDef } from './api';

import { reactive, ref, watch } from 'vue';

import {
  DeleteOutlined,
  FullscreenOutlined,
  PlayCircleOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import * as api from './api';

interface ParamRow extends ToolParamDef {
  /** 行 key（渲染/删除用，不入库） */
  key: string;
}

const props = defineProps<{
  open: boolean;
  /** 有值为编辑，无值为新增 */
  toolId?: null | number;
}>();

const emit = defineEmits<{
  (e: 'update:open', open: boolean): void;
  (e: 'saved'): void;
}>();

const PARAM_TYPE_OPTIONS = [
  { label: '字符串', value: 'string' },
  { label: '数字', value: 'number' },
  { label: '布尔值', value: 'boolean' },
  { label: '数组', value: 'array' },
  { label: '对象', value: 'object' },
];

const CODE_PLACEHOLDER = `import json

def main(**kwargs):
    # kwargs 中注入下方「参数定义」登记的参数
    return 'hello'`;

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const codeModalOpen = ref(false);
const detailName = ref('');

const form = reactive({
  description: '',
  function_code: '',
  name: '',
  status: true,
  timeout: 10_000,
});
const rows = ref<ParamRow[]>([]);

/** 测试结果：null=未跑过 */
const testState = ref<'error' | 'success'>('success');
const testMessage = ref('');
const testOutput = ref('');
const testedOnce = ref(false);

function genKey(): string {
  return `p_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function createRow(): ParamRow {
  return {
    description: '',
    key: genKey(),
    name: '',
    required: false,
    type: 'string',
  };
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    resetResult();
    Object.assign(form, {
      description: '',
      function_code: '',
      name: '',
      status: true,
      timeout: 10_000,
    });
    rows.value = [];
    detailName.value = '';
    if (!props.toolId) return;
    loading.value = true;
    try {
      const detail: any = await api.GetDetail(props.toolId);
      Object.assign(form, {
        description: detail.description || '',
        function_code: detail.function_code || '',
        name: detail.name || '',
        status: detail.status !== false,
        timeout: detail.timeout ?? 10_000,
      });
      detailName.value = detail.name || '';
      // 详情接口已摊平参数定义，前端不再解 JSON 字符串
      rows.value = (detail.parameters || []).map((item: ToolParamDef) => ({
        ...item,
        default: item.default ?? '',
        key: genKey(),
      }));
    } catch (error: any) {
      message.error(error?.message || '工具详情加载失败');
    } finally {
      loading.value = false;
    }
  },
);

function resetResult() {
  testedOnce.value = false;
  testMessage.value = '';
  testOutput.value = '';
}

function addRow() {
  rows.value.push(createRow());
}

function removeRow(index: number) {
  rows.value.splice(index, 1);
}

/** 参数行 → 参数定义（默认值按类型转换：number/boolean/array/object 交后端兜底解析） */
function buildDefinitions() {
  return rows.value
    .filter((row) => row.name?.trim())
    .map((row) => ({
      default: row.default === '' ? null : row.default,
      description: row.description || '',
      name: row.name.trim(),
      required: !!row.required,
      type: row.type || 'string',
    }));
}

/** 测试入参：取参数定义里填了默认值的行（空值不传，让函数的形参默认值生效） */
function buildTestArgs(): Record<string, any> {
  const args: Record<string, any> = {};
  for (const row of rows.value) {
    if (!row.name?.trim()) continue;
    if (
      row.default === '' ||
      row.default === undefined ||
      row.default === null
    ) {
      continue;
    }
    args[row.name.trim()] = row.default;
  }
  return args;
}

function validateRows(): boolean {
  const names = new Set<string>();
  for (const row of rows.value) {
    const name = row.name?.trim();
    if (!name) {
      message.warning('参数名不能为空');
      return false;
    }
    if (names.has(name)) {
      message.warning(`参数「${name}」重复`);
      return false;
    }
    names.add(name);
  }
  return true;
}

async function handleTest() {
  if (!form.function_code?.trim()) {
    message.warning('请先填写函数代码');
    return;
  }
  if (!validateRows()) return;
  testing.value = true;
  resetResult();
  try {
    const result = await api.TestDefinition(
      form.function_code,
      buildTestArgs(),
      form.timeout,
    );
    testedOnce.value = true;
    testState.value = result.success ? 'success' : 'error';
    if (result.success) {
      testMessage.value = `执行成功（${result.duration_ms ?? 0}ms）`;
      testOutput.value =
        typeof result.result === 'string'
          ? result.result
          : JSON.stringify(result.result, null, 2);
    } else {
      testMessage.value = `执行失败（${result.duration_ms ?? 0}ms）`;
      testOutput.value = result.error || '执行失败';
    }
  } catch (error: any) {
    testedOnce.value = true;
    testState.value = 'error';
    testMessage.value = '执行失败';
    testOutput.value = error?.message || '请求失败';
  } finally {
    testing.value = false;
  }
}

async function handleSave() {
  if (!form.name?.trim()) {
    message.warning('请输入工具名称');
    return;
  }
  if (!form.function_code?.trim()) {
    message.warning('请输入函数代码');
    return;
  }
  if (!validateRows()) return;
  const payload: api.ToolSaveReq = {
    description: form.description || undefined,
    function_code: form.function_code,
    name: form.name.trim(),
    parameters_schema: JSON.stringify({ parameters: buildDefinitions() }),
    status: form.status,
    timeout: form.timeout,
  };
  saving.value = true;
  try {
    await (props.toolId
      ? api.UpdateObj(props.toolId, payload)
      : api.AddObj(payload));
    message.success(props.toolId ? '保存成功' : '新增成功');
    emit('update:open', false);
    emit('saved');
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  } finally {
    saving.value = false;
  }
}

function handleClose() {
  emit('update:open', false);
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="820"
    :title="props.toolId ? `编辑工具：${detailName || ''}` : '新增工具'"
    @update:open="handleClose"
  >
    <a-spin :spinning="loading">
      <a-form class="tool-form" layout="vertical">
        <a-row :gutter="12">
          <a-col :span="10">
            <a-form-item label="工具名称" required>
              <a-input
                v-model:value="form.name"
                placeholder="唯一名称，工作流工具节点按此选择"
              />
            </a-form-item>
          </a-col>
          <a-col :span="14">
            <a-form-item label="描述">
              <a-input
                v-model:value="form.description"
                placeholder="工具用途说明（将展示给工作流编排使用）"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="代码" required>
          <div class="code-editor-wrap">
            <a-textarea
              v-model:value="form.function_code"
              :autosize="{ minRows: 10, maxRows: 16 }"
              :placeholder="CODE_PLACEHOLDER"
              class="code-editor"
              spellcheck="false"
            />
            <a-tooltip title="放大编辑">
              <a-button
                class="code-expand-btn"
                type="text"
                @click="codeModalOpen = true"
              >
                <template #icon>
                  <FullscreenOutlined />
                </template>
              </a-button>
            </a-tooltip>
          </div>
          <div class="form-hint">
            直接写 import + def 方法；入口方法自动识别：main 优先，其次
            run，无则取最后定义的顶层函数
          </div>
        </a-form-item>

        <a-divider orientation="left" class="section-divider">
          参数定义
        </a-divider>

        <div class="parameters-section">
          <div
            v-for="(row, index) in rows"
            :key="row.key"
            class="parameter-item"
          >
            <div class="parameter-header">
              <span class="parameter-index">参数 {{ index + 1 }}</span>
              <a-button
                danger
                size="small"
                type="text"
                @click="removeRow(index)"
              >
                <DeleteOutlined />
              </a-button>
            </div>
            <a-row :gutter="8">
              <a-col :span="8">
                <a-input
                  v-model:value="row.name"
                  placeholder="参数名"
                  size="small"
                />
              </a-col>
              <a-col :span="6">
                <a-select
                  v-model:value="row.type"
                  :options="PARAM_TYPE_OPTIONS"
                  size="small"
                  style="width: 100%"
                />
              </a-col>
              <a-col :span="4">
                <a-checkbox v-model:checked="row.required" size="small">
                  必填
                </a-checkbox>
              </a-col>
              <a-col :span="6">
                <a-input
                  v-model:value="row.default"
                  placeholder="默认值"
                  size="small"
                />
              </a-col>
            </a-row>
            <a-input
              v-model:value="row.description"
              class="param-desc-input"
              placeholder="参数说明（节点表单与测试弹窗会作为提示展示）"
              size="small"
            />
          </div>
          <a-button block size="small" type="dashed" @click="addRow">
            <PlusOutlined /> 添加参数
          </a-button>
        </div>

        <a-divider orientation="left" class="section-divider">
          执行设置
        </a-divider>

        <a-row :gutter="12">
          <a-col :span="10">
            <a-form-item label="超时时间（毫秒）">
              <a-input-number
                v-model:value="form.timeout"
                :max="600000"
                :min="100"
                :step="1000"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="启用状态">
              <a-switch
                v-model:checked="form.status"
                checked-children="启用"
                un-checked-children="禁用"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-alert class="code-help" show-icon type="info">
          <template #message>工具说明</template>
          <template #description>
            <ul class="help-list">
              <li>
                执行环境与工作流「代码执行」节点完全一致：同一份 builtins
                白名单、
                同一套危险模块拦截、同一个入口识别规则，能在工作流里跑的代码登记成工具同样能跑
              </li>
              <li>
                入口方法入参通过
                <code>kwargs</code> 注入，与「参数定义」一一对应：
                <code>def main(**kwargs)</code>
              </li>
              <li>
                参数默认值双重生效：测试时作为入参、工作流节点未绑定该参数时作为兜底取值
              </li>
              <li>输出限制：字符串 ≤200KB / 数组 ≤100 元素</li>
            </ul>
          </template>
        </a-alert>

        <div v-if="testedOnce" class="test-result">
          <a-alert
            :message="testMessage"
            :type="testState === 'success' ? 'success' : 'error'"
            show-icon
          />
          <pre class="result-pre">{{ testOutput || '（无输出）' }}</pre>
        </div>

        <div class="modal-foot">
          <a-button :loading="testing" @click="handleTest">
            <PlayCircleOutlined /> 测试
          </a-button>
          <a-space>
            <a-button @click="handleClose">取消</a-button>
            <a-button :loading="saving" type="primary" @click="handleSave">
              确定
            </a-button>
          </a-space>
        </div>
      </a-form>
    </a-spin>

    <a-modal
      :footer="null"
      :fullscreen="true"
      :open="codeModalOpen"
      title="代码编辑（Python）"
      class="code-fullscreen-modal"
      @update:open="codeModalOpen = $event"
    >
      <a-textarea
        v-model:value="form.function_code"
        :placeholder="CODE_PLACEHOLDER"
        class="code-editor fullscreen-editor"
        spellcheck="false"
        :autosize="{ minRows: 28, maxRows: 40 }"
      />
    </a-modal>
  </a-modal>
</template>

<style scoped lang="less">
.tool-form {
  :deep(.ant-form-item) {
    margin-bottom: 14px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  .section-divider {
    margin: 4px 0 12px;
    font-size: 12px;

    :deep(.ant-divider-inner-text) {
      color: #8c8c8c;
    }
  }

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
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

  // 代码编辑器：等宽深色主题（与工作流代码节点同一观感）
  .code-editor {
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', Consolas, monospace;
    font-size: 12px;
    line-height: 1.5;
    color: #d4d4d4;
    background-color: #1e1e1e;
    border-color: #333;

    &::placeholder {
      color: #6a6a6a;
    }

    &:hover,
    &:focus {
      border-color: #1890ff;
    }
  }

  .fullscreen-editor {
    padding: 8px 4px 0;
    font-size: 13px;
    line-height: 1.6;
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

        .parameter-index {
          flex: 1;
          font-size: 12px;
          font-weight: 500;
          color: #595959;
        }
      }

      .param-desc-input {
        margin-top: 8px;
      }

      :deep(.ant-input),
      :deep(.ant-select),
      :deep(.ant-checkbox-wrapper) {
        font-size: 12px;
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

  .test-result {
    margin-top: 12px;

    // 与 MCP 工具测试弹窗同一套深底白字口径
    .result-pre {
      max-height: 200px;
      padding: 10px 12px;
      margin: 8px 0 0;
      overflow: auto;
      font-size: 12px;
      line-height: 20px;
      color: #fff;
      white-space: pre-wrap;
      word-break: break-all;
      background: #1e1e1e !important;
      border-radius: 8px;
    }
  }

  .modal-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 18px;
  }
}
</style>
