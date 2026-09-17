<script lang="ts" setup>
/**
 * 工具运行测试弹窗：解析 parameters_schema 动态渲染参数表单（或切回 JSON 模式），
 * 调用后端在受限环境执行函数并展示结果（可一键复制）
 */
import { computed, reactive, ref, watch } from 'vue';

import { CopyOutlined, PlayCircleOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import * as api from './api';

interface ParamField {
  name: string;
  type?: string;
  required?: boolean;
  default?: unknown;
  description?: string;
}

const props = defineProps<{
  tool: any; // 当前工具行数据
  visible: boolean;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

/** 输入模式：表单（按 schema 动态渲染）或 JSON（原文输入兜底） */
const mode = ref<'form' | 'json'>('form');
const fields = ref<ParamField[]>([]);
const formValues = reactive<Record<string, any>>({});
/** 参数 JSON 文本（JSON 模式输入） */
const paramsText = ref('{}');
/** 执行耗时展示 */
const resultState = ref<'error' | 'idle' | 'loading' | 'success'>('idle');
const resultValue = ref('');
const durationMs = ref(0);

const open = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
});

/** 类型推断（按 example 默认值） → 表单控件类型 */
function guessType(value: unknown): string {
  if (value === null || value === undefined) return 'string';
  if (Array.isArray(value)) return 'array';
  const t = typeof value;
  if (t === 'number') return 'number';
  if (t === 'boolean') return 'boolean';
  if (t === 'object') return 'object';
  return 'string';
}

/** 解析 parameters_schema：{"parameters":[{name,type,required,default,description}]}，兼容 {"example": {...}} 旧格式 */
function parseSchema(raw?: string): {
  example: Record<string, any>;
  fields: ParamField[];
} {
  let schema: any = {};
  try {
    schema = JSON.parse(raw || '{}');
  } catch {
    schema = {};
  }
  if (
    schema &&
    typeof schema === 'object' &&
    Array.isArray(schema.parameters)
  ) {
    const example: Record<string, any> = {};
    const list: ParamField[] = (schema.parameters as Array<Record<string, any>>)
      .filter((p) => p && typeof p.name === 'string' && p.name)
      .map((p) => {
        const type = p.type || guessType(p.default);
        if (p.default !== undefined) example[p.name] = p.default;
        return {
          name: p.name,
          type,
          required: Boolean(p.required),
          default: p.default,
          description: p.description || '',
        };
      });
    return { fields: list, example };
  }
  // 兼容旧格式：example 对象 / 整个 schema 即示例对象
  const example = (schema && schema.example) || schema;
  if (example && typeof example === 'object' && !Array.isArray(example)) {
    const list: ParamField[] = Object.keys(example).map((name) => ({
      name,
      type: guessType(example[name]),
      default: example[name],
      required: false,
    }));
    return { fields: list, example };
  }
  return { fields: [], example: {} };
}

watch(
  () => props.visible,
  (v) => {
    if (!v) return;
    // 清空旧表单值
    Object.keys(formValues).forEach((k) => delete formValues[k]);
    const { fields: fs, example } = parseSchema(props.tool?.parameters_schema);
    fields.value = fs;
    for (const f of fs) {
      const def = f.default ?? example[f.name];
      formValues[f.name] =
        def === undefined ? (f.type === 'number' ? undefined : '') : def;
    }
    paramsText.value = JSON.stringify(example || {}, null, 2);
    mode.value = fs.length > 0 ? 'form' : 'json';
    resultState.value = 'idle';
    resultValue.value = '';
    durationMs.value = 0;
  },
);

/** 表单值 → 参数对象（number/boolean 类型转换，空值跳过；array/object 解析 JSON 字符串） */
function collectFormParams(): Record<string, any> {
  const params: Record<string, any> = {};
  for (const f of fields.value) {
    const v = formValues[f.name];
    if (v === '' || v === undefined || v === null) continue;
    switch (f.type) {
      case 'array':
      case 'object': {
        try {
          params[f.name] = JSON.parse(String(v));
        } catch {
          params[f.name] = v;
        }

        break;
      }
      case 'boolean': {
        params[f.name] = Boolean(v);

        break;
      }
      case 'number': {
        params[f.name] = Number(v);

        break;
      }
      default: {
        params[f.name] = v;
      }
    }
  }
  return params;
}

async function runTest() {
  let parameters: Record<string, any> = {};
  if (mode.value === 'json') {
    try {
      parameters = JSON.parse(paramsText.value || '{}');
    } catch {
      message.error('参数不是合法 JSON，请检查格式');
      return;
    }
  } else {
    parameters = collectFormParams();
  }
  resultState.value = 'loading';
  resultValue.value = '';
  try {
    const result = await api.TestTool(props.tool.id, parameters);
    durationMs.value = result.duration_ms ?? 0;
    if (result.success) {
      resultState.value = 'success';
      resultValue.value =
        typeof result.result === 'string'
          ? result.result
          : JSON.stringify(result.result, null, 2);
    } else {
      resultState.value = 'error';
      resultValue.value = result.error || '执行失败';
    }
  } catch (error: any) {
    resultState.value = 'error';
    resultValue.value = error.message || '请求失败';
  }
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(resultValue.value);
    message.success('结果已复制到剪贴板');
  } catch {
    message.error('复制失败，请手动选择复制');
  }
}
</script>

<template>
  <a-modal
    :open="open"
    :title="`运行测试：${tool?.name || ''}`"
    width="680px"
    :footer="null"
    @cancel="open = false"
  >
    <div class="test-modal">
      <div class="mode-bar">
        <span class="section-title">测试参数</span>
        <a-radio-group
          v-model:value="mode"
          size="small"
          :disabled="resultState === 'loading'"
        >
          <a-radio-button value="form">表单模式</a-radio-button>
          <a-radio-button value="json">JSON模式</a-radio-button>
        </a-radio-group>
      </div>

      <!-- 动态表单模式：按 parameters_schema 渲染，参数以关键字传入函数 -->
      <a-form v-if="mode === 'form'" layout="vertical" class="param-form">
        <a-form-item
          v-for="f in fields"
          :key="f.name"
          :label="f.name"
          :required="f.required"
        >
          <template #extra>
            <span class="field-desc">{{
              f.description || '按关键字参数传入函数'
            }}</span>
          </template>
          <a-input-number
            v-if="f.type === 'number'"
            v-model:value="formValues[f.name]"
            :style="{ width: '100%' }"
            :placeholder="`${f.name}（数字）`"
            :disabled="resultState === 'loading'"
          />
          <a-switch
            v-else-if="f.type === 'boolean'"
            v-model:checked="formValues[f.name]"
            :disabled="resultState === 'loading'"
          />
          <a-textarea
            v-else-if="f.type === 'array' || f.type === 'object'"
            v-model:value="formValues[f.name]"
            :rows="2"
            :placeholder="`${f.name}（JSON，例如: [] 或 {}）`"
            :disabled="resultState === 'loading'"
          />
          <a-input
            v-else
            v-model:value="formValues[f.name]"
            :placeholder="`${f.name}（文本）`"
            :disabled="resultState === 'loading'"
          />
        </a-form-item>
        <a-empty
          v-if="fields.length === 0"
          description="Schema 无参数定义，请切换到 JSON 模式输入"
        />
      </a-form>

      <!-- JSON 兜底模式 -->
      <a-textarea
        v-else
        v-model:value="paramsText"
        :rows="5"
        placeholder="例如: {'a': 1, 'b': 2}"
        :disabled="resultState === 'loading'"
      />

      <div class="result-area">
        <div class="section-title">
          执行结果
          <template v-if="resultState === 'success'">
            <a-tag color="green">成功 · {{ durationMs }}ms</a-tag>
          </template>
          <template v-else-if="resultState === 'error'">
            <a-tag color="red">失败 · {{ durationMs }}ms</a-tag>
          </template>
          <a-button
            v-if="resultValue"
            type="text"
            size="small"
            class="copy-btn"
            @click="copyResult"
          >
            <template #icon><CopyOutlined /></template>
            复制
          </a-button>
        </div>
        <a-spin :spinning="resultState === 'loading'">
          <pre class="result-box" :class="{ error: resultState === 'error' }">{{
            resultValue || '暂无结果'
          }}</pre>
        </a-spin>
      </div>

      <div class="actions">
        <a-button
          type="primary"
          :loading="resultState === 'loading'"
          @click="runTest"
        >
          <template #icon><PlayCircleOutlined /></template>
          运行
        </a-button>
        <a-button @click="open = false">关闭</a-button>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.test-modal .section-title {
  margin: 8px 0 6px;
  font-size: 13px;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mode-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.param-form {
  margin-top: 4px;
  max-height: 320px;
  overflow: auto;
  padding-right: 4px;
}

.param-form :deep(.ant-form-item) {
  margin-bottom: 14px;
}

.field-desc {
  color: #999;
  font-size: 12px;
}

.result-area {
  margin-top: 12px;
}

.copy-btn {
  margin-left: auto;
}

.result-box {
  min-height: 120px;
  max-height: 260px;
  overflow: auto;
  margin: 0;
  padding: 10px 12px;
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.result-box.error {
  color: #f56c6c;
}

.actions {
  margin-top: 14px;
  text-align: right;
}
</style>
