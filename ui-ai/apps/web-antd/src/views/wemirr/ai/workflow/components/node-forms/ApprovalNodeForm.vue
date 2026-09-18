<script setup lang="ts">
/**
 * APPROVAL 节点配置表单
 * 人工审批：执行到该节点即挂起，结论由「指定 executionId 再提交」接口带回
 *
 * - 审批人（approvers）：数字或字符串混合的集合，命中其一即可审批；留空 = 任何持有
 *   api-key 且知道执行 id 的人皆可审
 * - 只要画布上有审批节点，开始节点就必须有一个「审批人(APPROVER)」入参作为提交时的
 *   身份来源（后端 graph.validate 同一口径 ERROR），表单底部对该约束给出实时提示
 */
import type { ApprovalNodeConfig } from '#/api/ai-workflow/types';

import { computed, reactive, watch } from 'vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';

// Props
interface Props {
  config: ApprovalNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ApprovalNodeConfig): void;
}>();

const workflowStore = useAiWorkflowStore();

// 表单数据
const formData = reactive<ApprovalNodeConfig>({
  approvers: [],
  pauseScope: 'DOWNSTREAM',
  rejectReply: '',
  timeoutHours: 0,
});

/** 审批人下拉：tags 模式允许自由输入，数字字面量归一化为 number（与后端交集比对口径一致） */
const approverValue = computed(() => (formData.approvers || []).map(String));

/** 画布开始节点里是否已声明 APPROVER 入参（审批身份来源） */
const hasApproverInput = computed(() => {
  const canvas = workflowStore.canvasRef;
  if (!canvas) return true; // 画布未就绪时不误报
  const nodes = canvas.getNodes?.() || [];
  return nodes.some((node: any) => {
    if (node.data?.nodeType !== 'START') return false;
    const fields = node.data?.config?.fields || [];
    return fields.some((f: any) => f.type === 'APPROVER');
  });
});

/** 缺审批入参：提交时拿不到审批身份，整条图也过不了校验 */
const approverMisconfigured = computed(() => !hasApproverInput.value);

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      approvers: config.approvers ? [...config.approvers] : [],
      pauseScope: config.pauseScope || 'DOWNSTREAM',
      rejectReply: config.rejectReply || '',
      timeoutHours: config.timeoutHours ?? 0,
    });
  },
  { deep: true, immediate: true },
);

// 处理配置变更
function handleChange() {
  emit('update:config', {
    ...formData,
    approvers: [...(formData.approvers || [])],
  });
}

/** 审批人变更：去掉空白项，纯数字入参转成 number */
function handleApproversChange(value: string[]) {
  formData.approvers = (value || [])
    .map((item) => item.trim())
    .filter((item) => item !== '')
    .map((item) => (/^-?\d+$/.test(item) ? Number(item) : item));
  handleChange();
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="暂停范围" required>
      <a-select v-model:value="formData.pauseScope" @change="handleChange">
        <a-select-option value="DOWNSTREAM">本节点及下游</a-select-option>
        <a-select-option value="ALL">整条工作流</a-select-option>
      </a-select>
      <div class="field-help">
        整条工作流：在跑分支会被取消，恢复后重跑；本节点及下游：仅等待本节点放行
      </div>
    </a-form-item>

    <a-form-item label="审批人">
      <a-select
        mode="tags"
        :value="approverValue"
        :token-separators="[',', ' ']"
        placeholder="输入工号/用户名后回车，可填多个"
        style="width: 100%"
        @change="handleApproversChange"
      />
      <div class="field-help">
        留空 = 任何持有 api-key 且知道执行 id 者皆可审
      </div>
    </a-form-item>

    <a-alert
      v-if="approverMisconfigured"
      type="warning"
      show-icon
      message="开始节点缺少「审批人」入参"
      description="画布上有审批节点就必须能在提交时拿到审批人身份：请到开始节点添加一个类型为 APPROVER 的输入参数，否则无法保存与提交。"
      class="misconfig-alert"
    />

    <a-form-item label="拒绝回复文案">
      <a-textarea
        v-model:value="formData.rejectReply"
        :rows="3"
        placeholder="不同意时作为工作流回复兜底（下游 END/回复节点被取消时使用）"
        @change="handleChange"
      />
    </a-form-item>

    <a-form-item label="审批时限(小时)">
      <a-input-number
        v-model:value="formData.timeoutHours"
        :min="0"
        :max="720"
        style="width: 100%"
        @change="handleChange"
      />
      <div class="field-help">0 = 不限；超时自动裁决二期实现</div>
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

  .field-help {
    margin-top: 4px;
    font-size: 12px;
    line-height: 1.5;
    color: #8c8c8c;
  }

  .misconfig-alert {
    margin-bottom: 16px;
    font-size: 12px;
  }
}
</style>
