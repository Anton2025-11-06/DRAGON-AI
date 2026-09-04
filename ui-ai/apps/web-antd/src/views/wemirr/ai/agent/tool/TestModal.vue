<script lang="ts" setup>
/**
 * 工具运行测试弹窗：输入 JSON 参数，调用后端在受限环境执行函数并展示结果
 */
import { computed, ref, watch } from 'vue';

import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import * as api from './api';

const props = defineProps<{
  tool: any; // 当前工具行数据
  visible: boolean;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

/** 参数 JSON 文本（textarea 输入） */
const paramsText = ref('{}');
/** 执行耗时展示 */
const resultState = ref<'error' | 'idle' | 'loading' | 'success'>('idle');
const resultValue = ref('');
const durationMs = ref(0);

const open = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
});

watch(
  () => props.visible,
  (v) => {
    if (v) {
      paramsText.value = props.tool?.parameters_schema || '{}';
      // 参数说明可能是 schema/示例，尝试解析出示例对象作为默认参数
      try {
        const parsed = JSON.parse(paramsText.value);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const example = parsed.example ?? parsed.default ?? parsed;
          if (example && typeof example === 'object') {
            paramsText.value = JSON.stringify(example, null, 2);
          }
        }
      } catch {
        // 非法 JSON 保持原样交给用户编辑
      }
      resultState.value = 'idle';
      resultValue.value = '';
      durationMs.value = 0;
    }
  },
);

async function runTest() {
  let parameters: Record<string, any> = {};
  try {
    parameters = JSON.parse(paramsText.value || '{}');
  } catch {
    message.error('参数不是合法 JSON，请检查格式');
    return;
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
      <div class="section-title">测试参数（JSON 对象，将按关键字传入函数）</div>
      <a-textarea
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

.result-area {
  margin-top: 12px;
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
