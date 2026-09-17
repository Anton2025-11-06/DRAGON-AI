<script setup lang="ts">
/**
 * TOOL 工具节点配置表单
 * 选择「工具库」登记的动态 Python 函数工具（与代码节点同一沙箱口径）：
 * - 工具下拉来自 GET /tools/options，只列启用中的工具
 * - 选中后按工具登记的参数定义自动生成参数绑定行（引用上游变量 / 自定义值）
 * - 超时留空则取工具登记的超时；输出统一 { [outputVariable]: 返回值 }，默认 result
 * 提交字段与后端 ToolNodeExecutor 逐字对齐：toolId / toolName / inputs / timeout / outputVariable
 */
import type {
  CodeInputVariable,
  CodeParameterType,
  DynamicToolOption,
  ToolNodeConfig,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { listDynamicTools } from '#/api/ai-workflow';

import ParameterBindList from './ParameterBindList.vue';

// Props
interface Props {
  config: ToolNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ToolNodeConfig): void;
}>();

const toolOptions = ref<DynamicToolOption[]>([]);
const loadingTools = ref(false);

// 表单数据（toolId 未选时为 undefined，与提交后的 config 同形状）
interface ToolFormState {
  inputs: CodeInputVariable[];
  outputVariable: string;
  timeout?: number;
  toolId?: number;
  toolName?: string;
}

const formData = reactive<ToolFormState>({
  inputs: [],
  outputVariable: 'result',
  toolName: '',
});

const selectedTool = computed(() =>
  toolOptions.value.find((item) => item.id === formData.toolId),
);

/** 下游引用本节点输出的写法提示（含真实节点 id） */
const outputRefHint = computed(
  () => `{{${props.nodeId}.${formData.outputVariable || 'result'}}}`,
);

/** 参数名 → 说明（展示在绑定行参数名下） */
const descriptions = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const def of selectedTool.value?.parameters || []) {
    if (def.description) map[def.name] = def.description;
  }
  return map;
});

function genParamId(): string {
  return `param_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// 监听配置变化（不回写 inputs，避免用户在面板里的编辑被自身提交结果冲掉）
watch(
  () => props.config,
  (config) => {
    formData.toolId = config.toolId as number;
    formData.toolName = config.toolName || '';
    formData.inputs = (config.inputs || []).map((p) => ({
      ...p,
      id: p.id || genParamId(),
    }));
    formData.outputVariable = config.outputVariable || 'result';
    formData.timeout = config.timeout;
  },
  { immediate: true, deep: true },
);

// 恢复旧图：已选工具但没有参数行（老配置用 toolParams，无 inputs）时按参数定义补行
watch([() => formData.toolId, toolOptions], () => {
  if (!formData.toolId || (formData.inputs || []).length > 0) return;
  const tool = selectedTool.value;
  if (!tool) return;
  syncParameters(tool);
  handleChange();
});

/**
 * 工具参数定义 → 绑定行：同名行保留已配好的来源，
 * 新增行按定义预置类型/必填，带默认值的直接给「自定义值」，无默认值走「引用参数」
 */
function syncParameters(tool: DynamicToolOption) {
  const existing = new Map<string, CodeInputVariable>(
    (formData.inputs || []).map((p) => [p.name, p]),
  );
  formData.inputs = (tool.parameters || []).map((def) => {
    const type = (def.type || 'string') as CodeParameterType;
    const old = existing.get(def.name);
    if (old) {
      return { ...old, required: !!def.required, type };
    }
    const hasDefault =
      def.default !== undefined && def.default !== null && def.default !== '';
    return {
      id: genParamId(),
      name: def.name,
      required: !!def.required,
      sourceType: hasDefault ? ('CONSTANT' as const) : ('REFERENCE' as const),
      sourceVariable: hasDefault ? undefined : '',
      value: hasDefault ? def.default : undefined,
      type,
    };
  });
}

async function loadTools() {
  loadingTools.value = true;
  try {
    toolOptions.value = await listDynamicTools();
  } catch {
    toolOptions.value = [];
  } finally {
    loadingTools.value = false;
  }
}

/** 切换工具：名称一并落库（展示 + 旧图按名称加载的兼容口径） */
function handleToolChange(toolId: number) {
  const tool = toolOptions.value.find((item) => item.id === toolId);
  formData.toolName = tool?.name || '';
  syncParameters(tool || ({ parameters: [] } as unknown as DynamicToolOption));
  handleChange();
}

function handleChange() {
  const config: ToolNodeConfig = {
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
    outputVariable: formData.outputVariable || 'result',
    timeout: formData.timeout,
    toolId: formData.toolId,
    toolName: formData.toolName,
  };
  emit('update:config', config);
}

onMounted(() => {
  loadTools();
});
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="工具" required>
      <a-select
        v-model:value="formData.toolId"
        :loading="loadingTools"
        show-search
        placeholder="选择工具库中启用中的工具"
        option-filter-prop="label"
        :options="
          toolOptions.map((item) => ({
            label: item.name,
            value: item.id,
          }))
        "
        @change="handleToolChange"
      />
      <div v-if="selectedTool?.description" class="form-hint">
        {{ selectedTool.description }}
      </div>
      <div v-else-if="!formData.toolId" class="form-hint">
        工具在「智能体 - 工具」页登记，代码运行环境与代码执行节点一致
      </div>
    </a-form-item>

    <a-divider
      v-if="formData.toolId"
      orientation="left"
      class="section-divider"
    >
      参数
    </a-divider>

    <ParameterBindList
      v-if="formData.toolId"
      v-model:params="formData.inputs"
      :current-node-id="nodeId"
      :descriptions="descriptions"
      @change="handleChange"
    />

    <a-divider
      v-if="formData.toolId"
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
        :placeholder="`默认取工具超时 ${selectedTool?.timeout ?? 10_000}ms`"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="form-hint">留空则使用工具登记时配置的超时时间</div>
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: result"
        @change="handleChange"
      />
      <div class="form-hint">
        下游节点用 <code>{{ outputRefHint }}</code> 引用本节点返回值
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

  :deep(.ant-divider-inner-text) {
    color: #8c8c8c;
  }

  .section-divider {
    margin: 4px 0 12px;
    font-size: 12px;
  }
}
</style>
