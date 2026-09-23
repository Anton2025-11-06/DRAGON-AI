<script setup lang="ts">
/**
 * MCP_TOOL 节点配置表单
 * 选定一个 MCP 连接 + 该连接 tools/list 里的工具，参数按工具的 inputSchema 生成绑定行：
 * - 连接下拉来自工作流侧的 MCP 分页接口，工具列表实时向连接要（连接不通时给出提示）
 * - 参数绑定行与 CODE / TOOL 节点同构（引用上游变量 / 自定义值）
 * 提交字段与后端 McpToolNodeExecutor 逐字对齐：mcpServerId / toolName / inputs / timeout / outputVariable
 * 输出：result（structuredContent 优先，否则文本 content）、content、urls
 */
import type {
  CodeInputVariable,
  CodeParameterType,
  McpNodeConfig,
  McpServerOption,
  McpToolOption,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { listMcpServers, listMcpServerTools } from '#/api/ai-workflow';
import { schemaParams } from '#/views/wemirr/ai/agent/mcp/schema';

import ParameterBindList from './ParameterBindList.vue';

// Props
interface Props {
  config: McpNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: McpNodeConfig): void;
}>();

/** JSON Schema 类型 → 绑定行类型（integer 归到 number，未知类型按字符串处理） */
function toParamType(type?: string): CodeParameterType {
  switch (type) {
    case 'array': {
      return 'array';
    }
    case 'boolean': {
      return 'boolean';
    }
    case 'integer':
    case 'number': {
      return 'number';
    }
    case 'object': {
      return 'object';
    }
    default: {
      return 'string';
    }
  }
}

// 表单数据
interface McpFormState {
  inputs: CodeInputVariable[];
  mcpServerId?: number;
  outputVariable: string;
  timeout?: number;
  toolName?: string;
}

const formData = reactive<McpFormState>({
  inputs: [],
  outputVariable: 'result',
});

const servers = ref<McpServerOption[]>([]);
const loadingServers = ref(false);
const tools = ref<McpToolOption[]>([]);
const loadingTools = ref(false);
const toolsError = ref('');

const selectedTool = computed(() =>
  tools.value.find((item) => item.name === formData.toolName),
);

/** 参数名 → 说明（来自 inputSchema 的 description） */
const descriptions = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const row of toolParams()) {
    if (row.desc) map[row.name] = row.desc;
  }
  return map;
});

/** 当前工具的 inputSchema → 参数定义行 */
function toolParams() {
  return schemaParams(selectedTool.value?.inputSchema as any) || [];
}

function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 本地刚 emit 出去的配置快照（nodeId + 配置内容），与参数提取器/代码节点同一套守卫口径。
 * 父层（PropertyPanel / selectedNode.data）会把自己刚收到的配置原样回传，不拦住的话
 * 下方 watch 会重建 formData.inputs，用户正在编的那一行被新对象替掉（光标丢失、编辑态被
 * 自身提交结果冲掉），与「不回写 inputs」的本意相左。
 */
let emittedSignature = '';

/** 配置内容签名（含参数行 id，既识别自己的回传，也不吞单字段的外部变更） */
function signatureOf(config: McpNodeConfig | undefined): string {
  return JSON.stringify([
    config?.mcpServerId ?? null,
    config?.toolName || '',
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
    formData.mcpServerId = config.mcpServerId;
    formData.toolName = config.toolName;
    formData.inputs = (config.inputs || []).map((p) => ({
      ...p,
      id: p.id || genParamId(),
    }));
    formData.outputVariable = config.outputVariable || 'result';
    formData.timeout = config.timeout;
  },
  { immediate: true, deep: true },
);

async function loadServers() {
  loadingServers.value = true;
  try {
    servers.value = await listMcpServers();
  } catch {
    servers.value = [];
  } finally {
    loadingServers.value = false;
  }
}

async function loadTools(serverId: number) {
  loadingTools.value = true;
  toolsError.value = '';
  try {
    tools.value = await listMcpServerTools(serverId);
  } catch (error: any) {
    tools.value = [];
    toolsError.value = error?.message || '获取工具列表失败，请检查连接状态';
  } finally {
    loadingTools.value = false;
  }
}

/**
 * inputSchema → 绑定行：同名行保留已配好的来源，新增行按 schema 预置类型与必填；
 * 默认全部走「引用参数」，MCP 工具无平台侧默认值概念
 */
function syncParameters() {
  const defs = toolParams();
  if (defs.length === 0) return;
  const existing = new Map<string, CodeInputVariable>(
    (formData.inputs || []).map((p) => [p.name, p]),
  );
  formData.inputs = defs.map((def) => {
    const type = toParamType(def.type);
    const old = existing.get(def.name);
    if (old) {
      return { ...old, required: !!def.required, type };
    }
    return {
      id: genParamId(),
      name: def.name,
      required: !!def.required,
      sourceType: 'REFERENCE' as const,
      sourceVariable: '',
      type,
    };
  });
}

async function handleServerChange(serverId: number) {
  formData.mcpServerId = serverId;
  formData.toolName = undefined;
  formData.inputs = [];
  tools.value = [];
  if (serverId) await loadTools(serverId);
  handleChange();
}

function handleToolChange() {
  syncParameters();
  handleChange();
}

function handleChange() {
  const config: McpNodeConfig = {
    inputs: (formData.inputs || []).map((p) => ({
      id: p.id,
      name: p.name || '',
      required: !!p.required,
      sourceType: p.sourceType === 'CONSTANT' ? 'CONSTANT' : 'REFERENCE',
      sourceVariable:
        p.sourceType === 'CONSTANT' ? undefined : p.sourceVariable || '',
      type: (p.type || 'string') as CodeParameterType,
      value: p.sourceType === 'CONSTANT' ? p.value : undefined,
    })),
    mcpServerId: formData.mcpServerId,
    outputVariable: formData.outputVariable || 'result',
    timeout: formData.timeout,
    toolName: formData.toolName,
  };
  emittedSignature = `${props.nodeId}|${signatureOf(config)}`;
  emit('update:config', config);
}

// 恢复旧图：已选连接和工具但参数行为空（老配置用 toolParams）时，拉到工具后补一次行
watch([tools, () => formData.toolName], () => {
  if (!selectedTool.value || (formData.inputs || []).length > 0) return;
  syncParameters();
  handleChange();
});

onMounted(async () => {
  await loadServers();
  if (formData.mcpServerId) await loadTools(formData.mcpServerId);
});
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="MCP 连接" required>
      <a-select
        :loading="loadingServers"
        v-model:value="formData.mcpServerId"
        show-search
        option-filter-prop="label"
        placeholder="选择已配置的 MCP 连接"
        :options="servers.map((item) => ({ label: item.name, value: item.id }))"
        @change="handleServerChange"
      />
    </a-form-item>

    <a-form-item v-if="formData.mcpServerId" label="MCP 工具" required>
      <a-select
        :loading="loadingTools"
        v-model:value="formData.toolName"
        show-search
        option-filter-prop="label"
        placeholder="选择要调用的工具"
        :options="tools.map((item) => ({ label: item.name, value: item.name }))"
        @change="handleToolChange"
      />
      <div v-if="toolsError" class="form-error">{{ toolsError }}</div>
      <div v-else-if="selectedTool?.description" class="form-hint">
        {{ selectedTool.description }}
      </div>
    </a-form-item>

    <a-divider
      v-if="formData.toolName"
      orientation="left"
      class="section-divider"
    >
      参数
    </a-divider>

    <ParameterBindList
      v-if="formData.toolName"
      v-model:params="formData.inputs"
      :current-node-id="nodeId"
      :descriptions="descriptions"
      @change="handleChange"
    />

    <a-divider
      v-if="formData.toolName"
      orientation="left"
      class="section-divider"
    >
      执行设置
    </a-divider>

    <a-form-item label="超时时间（毫秒）">
      <a-input-number
        v-model:value="formData.timeout"
        :min="100"
        :max="600_000"
        :step="1000"
        placeholder="留空取节点默认超时"
        style="width: 100%"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: result"
        @change="handleChange"
      />
      <div class="form-hint">
        本节点另有 <code>content</code>（文本内容）与 <code>urls</code>
        （图片/音频等资源链接）两个输出可引用
      </div>
    </a-form-item>
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

    code {
      padding: 1px 4px;
      font-family: monospace;
      background-color: #f5f5f5;
      border-radius: 2px;
    }
  }

  .form-error {
    margin-top: 4px;
    font-size: 11px;
    color: #cf1322;
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
