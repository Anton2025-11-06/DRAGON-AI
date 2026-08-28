<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="MCP 服务器" required>
      <a-select
        v-model:value="formData.mcpServerId"
        placeholder="选择 MCP 服务器"
        :loading="loadingServers"
        @change="handleServerChange"
      >
        <a-select-option
          v-for="server in mcpServers"
          :key="server.id"
          :value="server.id"
        >
          {{ server.name }}
        </a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item v-if="formData.mcpServerId" label="工具">
      <a-select
        v-model:value="formData.toolName"
        placeholder="选择工具"
        :loading="loadingTools"
        @change="handleChange"
      >
        <a-select-option
          v-for="tool in tools"
          :key="tool.name"
          :value="tool.name"
        >
          <div class="tool-option">
            <span class="tool-name">{{ tool.name }}</span>
            <span class="tool-desc">{{ tool.description }}</span>
          </div>
        </a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item v-if="formData.toolName && selectedTool" label="工具参数">
      <div class="tool-params">
        <div
          v-for="param in selectedTool.parameters"
          :key="param.name"
          class="param-item"
        >
          <a-form-item :label="param.name" :required="param.required">
            <a-input
              v-if="param.type === 'string'"
              v-model:value="formData.toolParams[param.name]"
              :placeholder="param.description"
              @change="handleChange"
            />
            <a-input-number
              v-else-if="param.type === 'number'"
              v-model:value="formData.toolParams[param.name]"
              :placeholder="param.description"
              style="width: 100%"
              @change="handleChange"
            />
            <a-switch
              v-else-if="param.type === 'boolean'"
              v-model:checked="formData.toolParams[param.name]"
              @change="handleChange"
            />
            <a-textarea
              v-else
              v-model:value="formData.toolParams[param.name]"
              :placeholder="param.description"
              :rows="2"
              @change="handleChange"
            />
            <div v-if="param.description" class="param-desc">
              {{ param.description }}
            </div>
          </a-form-item>
        </div>
        <div v-if="!selectedTool.parameters?.length" class="no-params">
          该工具无需参数
        </div>
      </div>
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: tool_output"
        @change="handleChange"
      />
    </a-form-item>
  </a-form>
</template>

<script setup lang="ts">
/**
 * Tool 节点配置表单
 * 配置 MCP 工具调用参数
 */
import type {
  McpServerOption,
  McpToolOption,
  ToolNodeConfig,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { listMcpServers, listMcpServerTools } from '#/api/ai-workflow';

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

const mcpServers = ref<McpServerOption[]>([]);
const loadingServers = ref(false);
const tools = ref<McpToolOption[]>([]);
const loadingTools = ref(false);

type ToolNodeFormData = Omit<ToolNodeConfig, 'toolParams'> & {
  toolParams: Record<string, any>;
};

// 表单数据
const formData = reactive<ToolNodeFormData>({
  mcpServerId: undefined,
  toolName: undefined,
  toolParams: {},
  outputVariable: '',
});

// 选中的工具
const selectedTool = computed(() => {
  return tools.value.find((t) => t.name === formData.toolName);
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      mcpServerId: config.mcpServerId,
      toolName: config.toolName,
      toolParams: config.toolParams || {},
      outputVariable: config.outputVariable || '',
    });
    // 如果有服务器ID，加载工具列表
    if (config.mcpServerId) {
      loadTools(config.mcpServerId);
    }
  },
  { immediate: true, deep: true },
);

// 加载 MCP 服务器列表
async function loadMcpServers() {
  loadingServers.value = true;
  try {
    mcpServers.value = await listMcpServers();
  } catch {
    mcpServers.value = [];
  } finally {
    loadingServers.value = false;
  }
}

// 加载工具列表
async function loadTools(serverId: number) {
  loadingTools.value = true;
  try {
    tools.value = await listMcpServerTools(serverId);
  } catch {
    tools.value = [];
  } finally {
    loadingTools.value = false;
  }
}

// 处理服务器变更
function handleServerChange(serverId: number) {
  formData.toolName = undefined;
  formData.toolParams = {};
  loadTools(serverId);
  handleChange();
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}

onMounted(() => {
  loadMcpServers();
});
</script>

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

  .tool-option {
    display: flex;
    flex-direction: column;

    .tool-name {
      font-weight: 500;
    }

    .tool-desc {
      font-size: 11px;
      color: #8c8c8c;
    }
  }

  .tool-params {
    padding: 12px;
    background-color: #fafafa;
    border-radius: 6px;

    .param-item {
      :deep(.ant-form-item) {
        margin-bottom: 12px;
      }
    }

    .param-desc {
      margin-top: 2px;
      font-size: 11px;
      color: #8c8c8c;
    }

    .no-params {
      font-size: 12px;
      color: #8c8c8c;
      text-align: center;
    }
  }
}
</style>
