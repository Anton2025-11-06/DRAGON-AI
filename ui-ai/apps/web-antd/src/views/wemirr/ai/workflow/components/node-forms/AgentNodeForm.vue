<script setup lang="ts">
/**
 * Agent 节点配置表单
 * 配置智能体调用参数
 */
import type {
  AgentNodeConfig,
  WorkflowAgentOption,
} from '#/api/ai-workflow/types';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { listWorkflowAgents } from '#/api/ai-workflow';

// Props
interface Props {
  config: AgentNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: AgentNodeConfig): void;
}>();

// 智能体列表
const agents = ref<WorkflowAgentOption[]>([]);
const loadingAgents = ref(false);

// 表单数据
const formData = reactive<AgentNodeConfig>({
  agentId: undefined,
  outputVariable: '',
});

// 选中的智能体
const selectedAgent = computed(() => {
  return agents.value.find((a) => a.id === formData.agentId);
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      agentId: config.agentId,
      outputVariable: config.outputVariable || '',
    });
  },
  { immediate: true, deep: true },
);

// 加载智能体列表
async function loadAgents() {
  loadingAgents.value = true;
  try {
    agents.value = await listWorkflowAgents();
  } catch {
    agents.value = [];
  } finally {
    loadingAgents.value = false;
  }
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}

onMounted(() => {
  loadAgents();
});
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="智能体" required>
      <a-select
        v-model:value="formData.agentId"
        placeholder="选择智能体"
        :loading="loadingAgents"
        @change="handleChange"
      >
        <a-select-option
          v-for="agent in agents"
          :key="agent.id"
          :value="agent.id"
        >
          <div class="agent-option">
            <a-avatar v-if="agent.avatar" :src="agent.avatar" :size="20" />
            <a-avatar v-else :size="20">
              {{ agent.name.charAt(0) }}
            </a-avatar>
            <span class="agent-name">{{ agent.name }}</span>
          </div>
        </a-select-option>
      </a-select>
    </a-form-item>

    <a-form-item v-if="selectedAgent" label="智能体信息">
      <div class="agent-info">
        <div class="info-row">
          <span class="label">描述:</span>
          <span class="value">{{
            selectedAgent.description || '暂无描述'
          }}</span>
        </div>
      </div>
    </a-form-item>

    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="默认: agent_output"
        @change="handleChange"
      />
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

  .agent-option {
    display: flex;
    gap: 8px;
    align-items: center;

    .agent-name {
      font-size: 13px;
    }
  }

  .agent-info {
    padding: 12px;
    background-color: #fafafa;
    border-radius: 6px;

    .info-row {
      display: flex;
      gap: 8px;
      margin-bottom: 8px;

      &:last-child {
        margin-bottom: 0;
      }

      .label {
        flex-shrink: 0;
        font-size: 12px;
        color: #8c8c8c;
      }

      .value {
        font-size: 12px;
        color: #262626;
      }
    }
  }
}
</style>
