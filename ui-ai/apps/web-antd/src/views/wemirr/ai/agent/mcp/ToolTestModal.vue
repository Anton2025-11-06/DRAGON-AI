<script lang="ts" setup>
import type { McpToolCallResult, McpToolInfo } from './api';
import type { McpParamRow } from './schema';

import { computed, reactive, ref, watch } from 'vue';

import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import * as api from './api';
import { JSON_VALUE_TYPES, toolParams } from './schema';

interface Props {
  open: boolean;
  serverId?: number;
  serverName?: string;
  /** 被测试的工具（含 inputSchema，决定参数表单） */
  tool?: McpToolInfo | null;
}

const props = defineProps<Props>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

/** 参数输入值：键为参数名，值一律先按字符串/数字存，提交时再按类型转换 */
const values = reactive<Record<string, any>>({});
/** 无参数定义（或用户想看原始结构）时直接编辑 JSON */
const rawJson = ref('{}');
const useRaw = ref(false);
const calling = ref(false);
const result = ref<McpToolCallResult | null>(null);
const failMessage = ref('');
const elapsed = ref(0);

const rows = computed<McpParamRow[]>(() => toolParams(props.tool) ?? []);

/** 没有 properties 的工具居多是无参，也有服务端偷懒不下发 schema 的，给个 JSON 入口兜底 */
const noSchema = computed(() => rows.value.length === 0);

watch(
  () => [props.open, props.tool?.name] as const,
  ([open]) => {
    if (!open) return;
    Object.keys(values).forEach((key) => delete values[key]);
    rows.value.forEach((row) => {
      // 枚举/布尔给个默认选项，其余留空（空=不传，交给服务端默认值）
      if (row.enumValues?.length) values[row.name] = row.enumValues[0];
      else if (row.type === 'boolean') values[row.name] = 'false';
    });
    rawJson.value = '{}';
    useRaw.value = noSchema.value;
    result.value = null;
    failMessage.value = '';
    elapsed.value = 0;
  },
  { immediate: true },
);

/** 类型判定用集合：union 类型（如 "string/null"）也要能命中 */
function typeHit(type: string, targets: string[]): boolean {
  return type.split('/').some((item) => targets.includes(item.trim()));
}

/**
 * 组装调用参数。
 *
 * @returns 校验不通过时返回 null（不发起调用）
 */
function buildArgs(): null | Record<string, any> {
  if (useRaw.value) {
    try {
      const parsed = JSON.parse(rawJson.value || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        message.error('参数必须是 JSON 对象，例如 {"key": "value"}');
        return null;
      }
      return parsed as Record<string, any>;
    } catch {
      message.error('参数不是合法 JSON，请检查引号与逗号');
      return null;
    }
  }
  const args: Record<string, any> = {};
  for (const row of rows.value) {
    const raw = values[row.name];
    if (raw === undefined || raw === null || raw === '') {
      if (row.required) {
        message.warning(`请填写必填参数「${row.name}」`);
        return null;
      }
      // 未填的可选参数不传：传 null 会被部分服务端判成类型错误
      continue;
    }
    if (typeHit(row.type, JSON_VALUE_TYPES)) {
      try {
        args[row.name] = JSON.parse(String(raw));
      } catch {
        message.error(`参数「${row.name}」需要合法 JSON（${row.type}）`);
        return null;
      }
    } else if (typeHit(row.type, ['number', 'integer'])) {
      args[row.name] = Number(raw);
    } else if (typeHit(row.type, ['boolean'])) {
      args[row.name] = raw === true || raw === 'true';
    } else {
      args[row.name] = raw;
    }
  }
  return args;
}

async function handleCall() {
  if (!props.serverId || !props.tool?.name) return;
  const args = buildArgs();
  if (!args) return;
  calling.value = true;
  result.value = null;
  failMessage.value = '';
  const start = performance.now();
  try {
    result.value = await api.CallTool(props.serverId, props.tool.name, args);
    elapsed.value = Math.round(performance.now() - start);
  } catch (error: any) {
    elapsed.value = Math.round(performance.now() - start);
    failMessage.value = error?.message || '工具调用失败';
  } finally {
    calling.value = false;
  }
}

/** 结果里的资源链接分三类：图片直接显示、音频给播放器、其余当链接 */
function urlKind(url: string): 'audio' | 'image' | 'link' {
  if (url.startsWith('data:image/')) return 'image';
  if (url.startsWith('data:audio/')) return 'audio';
  return 'link';
}

/** 示例文案带双引号，写死在模板属性上会和 vue/html-quotes 的实体转换打架，故提到常量绑定 */
const JSON_ARG_EXAMPLE = '{"key": "value"}';
const JSON_TYPE_HINT = `JSON 值，例如 ${JSON_ARG_EXAMPLE} 或 [1, 2]`;

const paramColumns = [
  { dataIndex: 'name', key: 'name', title: '参数名', width: 150 },
  { dataIndex: 'type', key: 'type', title: '类型', width: 80 },
  { key: 'required', title: '必填', width: 60 },
  { key: 'input', title: '取值' },
];

function handleClose(open: boolean) {
  emit('update:open', open);
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="720"
    :title="`测试工具 - ${props.tool?.name || ''}`"
    @update:open="handleClose"
  >
    <div v-if="props.tool?.description" class="tool-desc">
      {{ props.tool.description }}
    </div>

    <a-form layout="vertical" class="param-form">
      <template v-if="!useRaw">
        <a-table
          :columns="paramColumns"
          :data-source="rows"
          :pagination="false"
          :scroll="{ y: 260 }"
          row-key="name"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'required'">
              <a-tag :color="record.required ? 'orange' : 'default'">
                {{ record.required ? '必填' : '选填' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'input'">
              <a-select
                v-if="record.enumValues?.length"
                v-model:value="values[record.name]"
                :options="
                  record.enumValues.map((item: string) => ({
                    label: item,
                    value: item,
                  }))
                "
                allow-clear
                placeholder="请选择"
                size="small"
                style="width: 100%"
              />
              <a-input-number
                v-else-if="
                  record.type.includes('number') ||
                  record.type.includes('integer')
                "
                v-model:value="values[record.name]"
                placeholder="留空则不传"
                size="small"
                style="width: 100%"
              />
              <a-select
                v-else-if="record.type.includes('boolean')"
                v-model:value="values[record.name]"
                :options="[
                  { label: 'true', value: 'true' },
                  { label: 'false', value: 'false' },
                ]"
                size="small"
                style="width: 100%"
              />
              <a-textarea
                v-else-if="
                  JSON_VALUE_TYPES.some((item) => record.type.includes(item))
                "
                v-model:value="values[record.name]"
                :autosize="{ minRows: 2, maxRows: 5 }"
                :placeholder="JSON_TYPE_HINT"
                size="small"
              />
              <a-input
                v-else
                v-model:value="values[record.name]"
                placeholder="留空则不传"
                size="small"
              />
              <div v-if="record.desc" class="param-desc">{{ record.desc }}</div>
            </template>
          </template>
        </a-table>
      </template>

      <a-form-item v-else label="调用参数（JSON）">
        <a-textarea
          v-model:value="rawJson"
          :autosize="{ minRows: 4, maxRows: 12 }"
          :placeholder="JSON_ARG_EXAMPLE"
        />
      </a-form-item>

      <div class="form-foot">
        <a-checkbox v-model:checked="useRaw">
          直接编辑 JSON{{ noSchema ? '（该工具未提供参数定义）' : '' }}
        </a-checkbox>
        <a-button :loading="calling" type="primary" @click="handleCall">
          <PlayCircleOutlined /> 测试
        </a-button>
      </div>
    </a-form>

    <div v-if="calling" class="result-empty">
      调用中，STDIO 连接还需先拉起服务进程…
    </div>

    <div v-else-if="failMessage" class="result-block">
      <a-alert :message="failMessage" show-icon type="error" />
    </div>

    <div v-else-if="result" class="result-block">
      <a-alert
        :message="result.isError ? '工具返回错误' : `调用成功（${elapsed}ms）`"
        :type="result.isError ? 'error' : 'success'"
        show-icon
      />
      <div v-if="result.content" class="result-section">
        <div class="result-title">返回内容</div>
        <pre class="result-pre">{{ result.content }}</pre>
      </div>
      <div v-if="result.urls?.length" class="result-section">
        <div class="result-title">资源（{{ result.urls.length }}）</div>
        <div class="result-urls">
          <template v-for="(url, index) in result.urls" :key="index">
            <img
              v-if="urlKind(url) === 'image'"
              :src="url"
              class="result-img"
              alt=""
            />
            <audio
              v-else-if="urlKind(url) === 'audio'"
              :src="url"
              controls
              class="result-audio"
            ></audio>
            <a v-else :href="url" class="result-link" target="_blank">{{
              url
            }}</a>
          </template>
        </div>
      </div>
      <div v-if="result.structured" class="result-section">
        <div class="result-title">结构化输出</div>
        <pre class="result-pre">{{
          JSON.stringify(result.structured, null, 2)
        }}</pre>
      </div>
      <div
        v-if="!result.content && !result.urls?.length && !result.structured"
        class="result-empty"
      >
        工具没有返回任何内容
      </div>
    </div>

    <div v-else class="result-empty">填写参数后点「测试」查看调用结果</div>
  </a-modal>
</template>

<style scoped lang="less">
.tool-desc {
  margin-bottom: 12px;
  font-size: 12px;
  color: #8c8c8c;
}

.param-form {
  .param-desc {
    margin-top: 2px;
    font-size: 11px;
    line-height: 16px;
    color: #a0a0a0;
  }
}

.form-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.result-block {
  margin-top: 14px;
}

.result-section {
  margin-top: 10px;

  .result-title {
    margin-bottom: 4px;
    font-size: 12px;
    color: #8c8c8c;
  }
}

// 与 API Key 用法说明同一套深底白字口径：内层不能有浅色底，否则白字读不出来
.result-pre {
  max-height: 260px;
  padding: 10px 12px;
  margin: 0;
  overflow: auto;
  font-size: 12px;
  line-height: 20px;
  color: #fff;
  white-space: pre-wrap;
  word-break: break-all;
  background: #1e1e1e !important;
  border-radius: 8px;
}

.result-urls {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-img {
  max-width: 240px;
  border-radius: 6px;
}

.result-audio {
  width: 100%;
}

.result-link {
  font-size: 12px;
  word-break: break-all;
}

.result-empty {
  margin-top: 14px;
  font-size: 12px;
  color: #bfbfbf;
}
</style>
