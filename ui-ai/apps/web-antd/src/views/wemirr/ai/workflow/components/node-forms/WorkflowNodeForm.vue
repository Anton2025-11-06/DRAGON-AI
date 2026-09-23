<script setup lang="ts">
/**
 * WORKFLOW 工作流节点配置表单
 * 嵌套调用平台内另一条已发布的工作流：引擎在 workflow 服务内部直接起子执行（不走网关），
 * 表单只配「调谁、用哪套凭证、哪个版本、入参怎么给」这四件事。
 * - 子工作流下拉来自 GET /workflows/executable-list（已发布 + 至少一把可用 API Key），
 *   可用 key 由同一个接口内联返回：再拉一次 /workflow-api-keys 要多一个 apikey:list 权限
 * - 版本策略：始终使用最新版本（跟发布走）/ 指定版本（锁定某个已发布版本，不随漂移）
 * - 入参要手配（引用上游变量 / 自定义值），语义与工具节点一致：子工作流的开始节点
 *   入参在引擎执行时才读，配置期拿不到，故不做自动生成绑定行
 * 提交字段与后端 WorkflowNodeExecutor 逐字对齐：
 * workflowId / workflowName / apiKeyId / versionMode / version / inputs / timeout / outputVariable
 */
import type {
  CodeInputVariable,
  CodeParameterType,
  ExecutableWorkflowOption,
  WorkflowNodeConfig,
  WorkflowVersionMode,
  WorkflowVersionResp,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import {
  getWorkflowVersionHistory,
  listExecutableWorkflows,
} from '#/api/ai-workflow';

import ParameterBindList from './ParameterBindList.vue';

// Props
interface Props {
  config: WorkflowNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: WorkflowNodeConfig): void;
}>();

const workflows = ref<ExecutableWorkflowOption[]>([]);
const loadingWorkflows = ref(false);
/** 子工作流的版本列表（只在切到「指定版本」时才拉，接口把 graphSnapshot 一起返回） */
const versions = ref<WorkflowVersionResp[]>([]);
const loadingVersions = ref(false);
const loadErrorMessage = ref('');

// 表单数据（workflowId 未选时为 undefined，与提交后的 config 同形状）
interface WorkflowFormState {
  apiKeyId?: number;
  inputs: CodeInputVariable[];
  outputVariable: string;
  timeout?: number;
  version?: number;
  versionMode: WorkflowVersionMode;
  workflowId?: number | string;
  workflowName?: string;
}

const formData = reactive<WorkflowFormState>({
  apiKeyId: undefined,
  inputs: [],
  outputVariable: 'result',
  versionMode: 'LATEST',
  workflowName: '',
});

const selectedWorkflow = computed(() =>
  workflows.value.find(
    (item) => String(item.id) === String(formData.workflowId),
  ),
);

/** 可用 API Key（executable-list 内联返回，已过滤停用/过期） */
const apiKeyOptions = computed(() =>
  (selectedWorkflow.value?.apiKeys || []).map((item) => ({
    label: `${item.name}（${item.rateLimit > 0 ? `${item.rateLimit} 次/分钟` : '不限流'}${
      item.expireTime ? `，${item.expireTime} 过期` : ''
    }）`,
    value: item.id,
  })),
);

/** 「指定版本」的下拉项：只有已发布的版本能被调用 */
const versionOptions = computed(() =>
  versions.value
    .filter((item) => item.published)
    .map((item) => ({
      label: `v${item.version}${
        item.changeLog ? ` - ${item.changeLog}` : ''
      }（${item.createdTime}）`,
      value: item.version,
    })),
);

/** LATEST 模式下展示实际会跑的版本，免得用户以为「最新版本」是个虚指 */
const latestVersionLabel = computed(() => {
  const version = selectedWorkflow.value?.currentVersion;
  return version ? `当前将执行 v${version}` : '';
});

/** 下游引用本节点输出的写法提示（含真实节点 id） */
const outputRefHint = computed(
  () => `{{${props.nodeId}.${formData.outputVariable || 'result'}}}`,
);

function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 本地刚 emit 出去的配置快照（nodeId + 配置内容），与工具/MCP 节点同一套守卫口径。
 * 父层会把自己刚收到的配置原样回传，不拦住的话下面的 watch 会重建 formData.inputs，
 * 用户正在编的那一行被新对象替掉（光标丢失、编辑态被自身提交结果冲掉）。
 */
let emittedSignature = '';

/** 配置内容签名（含参数行 id，既识别自己的回传，也不吞单字段的外部变更） */
function signatureOf(config: undefined | WorkflowNodeConfig): string {
  return JSON.stringify([
    // workflowId 后端返字符串、前端可能是数字，签名里归一口径
    String(config?.workflowId ?? ''),
    config?.workflowName || '',
    config?.apiKeyId ?? null,
    config?.versionMode || 'LATEST',
    config?.version ?? null,
    config?.outputVariable || '',
    config?.timeout ?? null,
    (config?.inputs || []).map((p) => [
      p.id || '',
      p.name || '',
      p.type || 'string',
      !!p.required,
      p.sourceType || 'REFERENCE',
      p.sourceVariable ?? null,
      p.value ?? null,
    ]),
  ]);
}

// 监听配置变化（自己刚提交的回声不回写 inputs，避免用户在面板里的编辑被自身提交结果冲掉）
watch(
  () => props.config,
  (config) => {
    if (`${props.nodeId}|${signatureOf(config)}` === emittedSignature) {
      // 自己刚 emit 的内容：本地即真源，保留编辑态与行对象身份
      return;
    }
    emittedSignature = '';
    formData.workflowId = config.workflowId;
    formData.workflowName = config.workflowName || '';
    formData.apiKeyId = config.apiKeyId;
    formData.versionMode =
      config.versionMode === 'SPECIFIC' ? 'SPECIFIC' : 'LATEST';
    formData.version = config.version;
    formData.inputs = (config.inputs || []).map((p) => ({
      ...p,
      id: p.id || genParamId(),
    }));
    formData.outputVariable = config.outputVariable || 'result';
    formData.timeout = config.timeout;
    // 恢复旧图时停在「指定版本」上：列表还没拉，补一次
    if (formData.versionMode === 'SPECIFIC' && formData.workflowId) {
      void loadVersions(formData.workflowId);
    }
  },
  { immediate: true, deep: true },
);

async function loadWorkflows() {
  loadingWorkflows.value = true;
  try {
    workflows.value = await listExecutableWorkflows();
    loadErrorMessage.value = '';
  } catch (error) {
    workflows.value = [];
    loadErrorMessage.value =
      error instanceof Error ? error.message : '加载可调用工作流失败';
  } finally {
    loadingWorkflows.value = false;
  }
}

/** 版本列表按需加载：接口把每个版本的图快照一起带回来，不该在打开面板时就拉 */
async function loadVersions(workflowId: number | string) {
  loadingVersions.value = true;
  try {
    versions.value = await getWorkflowVersionHistory(workflowId);
  } catch {
    versions.value = [];
  } finally {
    loadingVersions.value = false;
  }
}

/** 切换子工作流：key 与版本一并清空（它们都属于上一条目） */
function handleWorkflowChange(workflowId: number | string) {
  const item = workflows.value.find(
    (row) => String(row.id) === String(workflowId),
  );
  formData.workflowName = item?.name || '';
  formData.apiKeyId = undefined;
  formData.version = undefined;
  versions.value = [];
  if (formData.versionMode === 'SPECIFIC' && workflowId) {
    void loadVersions(workflowId);
  }
  handleChange();
}

function handleVersionModeChange(mode: WorkflowVersionMode) {
  formData.versionMode = mode;
  if (mode === 'SPECIFIC') {
    if (formData.workflowId && versions.value.length === 0) {
      void loadVersions(formData.workflowId);
    }
  } else {
    // 「指定版本」才有的字段不再展示即置空：留着它，切回 LATEST 的图会带着旧版本号跑
    formData.version = undefined;
  }
  handleChange();
}

/** 已选 key 被删掉/过期后不在下拉里：显式标出来，别让用户以为还配着 */
const apiKeyMissing = computed(
  () =>
    formData.apiKeyId !== undefined &&
    !apiKeyOptions.value.some((opt) => opt.value === formData.apiKeyId),
);

function handleChange() {
  const config: WorkflowNodeConfig = {
    apiKeyId: formData.apiKeyId,
    inputs: (formData.inputs || [])
      .filter((p) => !!String(p.name || '').trim())
      .map((p) => ({
        id: p.id,
        name: p.name || '',
        required: !!p.required,
        sourceType: p.sourceType === 'CONSTANT' ? 'CONSTANT' : 'REFERENCE',
        sourceVariable:
          p.sourceType === 'CONSTANT' ? undefined : p.sourceVariable || '',
        type: (p.type || 'string') as CodeParameterType,
        value: p.sourceType === 'CONSTANT' ? p.value : undefined,
      })),
    outputVariable: formData.outputVariable || 'result',
    timeout: formData.timeout,
    version: formData.versionMode === 'SPECIFIC' ? formData.version : undefined,
    versionMode: formData.versionMode,
    workflowId: formData.workflowId,
    workflowName: formData.workflowName,
  };
  emittedSignature = `${props.nodeId}|${signatureOf(config)}`;
  emit('update:config', config);
}

onMounted(() => {
  loadWorkflows();
});
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-alert
      v-if="loadErrorMessage"
      type="error"
      show-icon
      :message="loadErrorMessage"
      class="form-error"
    />

    <a-form-item label="子工作流" required>
      <a-select
        v-model:value="formData.workflowId"
        :loading="loadingWorkflows"
        show-search
        placeholder="选择已发布且配有 API Key 的工作流"
        option-filter-prop="label"
        :options="
          workflows.map((item) => ({
            label: `${item.name}（v${item.currentVersion}）`,
            value: item.id,
          }))
        "
        @change="handleWorkflowChange"
      />
      <div v-if="selectedWorkflow?.description" class="form-hint">
        {{ selectedWorkflow.description }}
      </div>
      <div
        v-else-if="workflows.length === 0 && !loadingWorkflows"
        class="form-hint"
      >
        暂无可调用工作流：子工作流需要先发布，并在其详情页创建一个启用中的 API
        Key
      </div>
      <div v-else-if="!formData.workflowId" class="form-hint">
        子执行在 workflow 服务内部直接跑，子流程的节点明细与事件照常落库
      </div>
    </a-form-item>

    <a-form-item v-if="formData.workflowId" label="执行用 API Key" required>
      <a-select
        v-model:value="formData.apiKeyId"
        placeholder="选择用哪套凭证调用子工作流"
        :options="apiKeyOptions"
        @change="handleChange"
      />
      <div v-if="apiKeyMissing" class="form-hint form-hint-warn">
        原选择的 API Key 已不可用（被删除/停用/过期），请重新选择
      </div>
      <div v-else class="form-hint">
        仅决定用哪套凭证与限流额度：执行前引擎仍会校验它启用、未过期、未超限
      </div>
    </a-form-item>

    <template v-if="formData.workflowId">
      <a-form-item label="版本策略">
        <a-radio-group
          :value="formData.versionMode"
          @change="(e: any) => handleVersionModeChange(e.target.value)"
        >
          <a-radio value="LATEST">始终使用最新版本</a-radio>
          <a-radio value="SPECIFIC">指定版本</a-radio>
        </a-radio-group>
        <div
          v-if="formData.versionMode === 'LATEST' && latestVersionLabel"
          class="form-hint"
        >
          {{ latestVersionLabel }}，子工作流重新发布后自动跟上
        </div>
      </a-form-item>

      <a-form-item
        v-if="formData.versionMode === 'SPECIFIC'"
        label="版本号"
        required
      >
        <a-select
          v-model:value="formData.version"
          :loading="loadingVersions"
          placeholder="选择一个已发布版本"
          :options="versionOptions"
          @change="handleChange"
        />
        <div class="form-hint">
          锁定某个已发布版本（发布快照不可变，不随子工作流继续修改而漂移）
        </div>
      </a-form-item>

      <a-divider orientation="left" class="section-divider">入参</a-divider>

      <ParameterBindList
        v-model:params="formData.inputs"
        :current-node-id="nodeId"
        @change="handleChange"
      />
      <div class="form-hint">
        参数名要与子工作流开始节点的入参字段一致；未绑定的入参由子工作流自己按默认处理
      </div>

      <a-divider orientation="left" class="section-divider">执行设置</a-divider>

      <a-form-item label="超时时间（毫秒）">
        <a-input-number
          v-model:value="formData.timeout"
          :min="1000"
          :max="3_600_000"
          :step="1000"
          placeholder="留空取节点默认超时"
          style="width: 100%"
          @change="handleChange"
        />
        <div class="form-hint">
          子工作流整体跑完的等待上限；子流程卡在审批时父节点会一起挂起，不占这个超时
        </div>
      </a-form-item>

      <a-form-item label="输出变量名">
        <a-input
          v-model:value="formData.outputVariable"
          placeholder="默认: result"
          @change="handleChange"
        />
        <div class="form-hint">
          <code>{{ outputRefHint }}</code> 取子工作流 END 的全部输出，
          <code>{{ nodeId }}.text</code> 取其中可读的文本回答
        </div>
      </a-form-item>
    </template>
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

  .form-error {
    margin-bottom: 12px;
  }

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;

    code {
      padding: 1px 4px;
      font-family: monospace;
      background-color: #f5f5f5;
      border-radius: 2px;
    }
  }

  .form-hint-warn {
    color: #d46b08;
  }

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  .section-divider {
    margin: 4px 0 12px;
    font-size: 12px;
  }
}
</style>
