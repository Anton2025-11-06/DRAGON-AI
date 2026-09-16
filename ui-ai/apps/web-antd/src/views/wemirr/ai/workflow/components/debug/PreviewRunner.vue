<script setup lang="ts">
import type { SSEConnectionState } from './use-sse';

/**
 * PreviewRunner 预览运行组件
 * 集成 DynamicInputForm，实现预览运行和 SSE 事件处理功能
 *
 */
import type { InputField } from '#/api/ai-workflow/types';

import { computed, onBeforeUnmount, ref } from 'vue';

import {
  FastForwardOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { cancelExecution, resumeExecution } from '#/api/ai-workflow';
import { useDebugStore } from '#/store/debug-store';

import DynamicInputForm from './DynamicInputForm.vue';
import { useWorkflowWs } from './use-ws';

// ==================== Props ====================

interface Props {
  /** 工作流ID */
  workflowId: string;
  /** 输入字段定义列表 (来自 START 节点) */
  inputFields?: InputField[];
  /** SSE 基础 URL */
  sseBaseUrl?: string;
}

const props = withDefaults(defineProps<Props>(), {
  inputFields: () => [],
  sseBaseUrl: '/api',
});

// ==================== Emits ====================

const emit = defineEmits<{
  /** 执行开始 */
  (e: 'workflowStarted', executionId: string): void;
  /** 执行完成 */
  (e: 'workflowCompleted', result: any): void;
  /** 执行失败 */
  (e: 'workflowFailed', error: any): void;
  /** 节点开始 */
  (e: 'nodeStarted', nodeId: string): void;
  /** 节点完成 */
  (e: 'nodeCompleted', nodeId: string): void;
  /** 节点错误 */
  (e: 'nodeFailed', nodeId: string, error: string): void;
  /** 流式 Token */
  (e: 'nodeDelta', nodeId: string, token: string): void;
  /** 检查点命中 */
  (e: 'workflowPaused', nodeId: string): void;
  /** SSE 连接状态变化 */
  (e: 'connection-state-change', state: SSEConnectionState): void;
}>();

// ==================== Store ====================

const debugStore = useDebugStore();

// ==================== SSE Composable ====================

/**
 * WebSocket 事件处理（预览运行：连接即执行，事件经 WS 回推）
 */
const {
  connectionState,
  connect: connectWs,
  disconnect: disconnectWs,
} = useWorkflowWs(props.sseBaseUrl, {
  onNodeStarted: (data) => {
    emit('nodeStarted', data.nodeId);
  },
  onNodeCompleted: (data) => {
    emit('nodeCompleted', data.nodeId);
  },
  onNodeError: (data) => {
    emit('nodeFailed', data.nodeId, data.error);
  },
  onStreamToken: (data) => {
    emit('nodeDelta', data.nodeId, data.token);
  },
  onBreakpointHit: (data) => {
    emit('workflowPaused', data.nodeId);
    message.info(`命中检查点: ${data.nodeId}`);
  },
  onExecutionCompleted: (data) => {
    emit('workflowCompleted', data);
    message.success('执行完成');
  },
  onExecutionFailed: (data) => {
    emit('workflowFailed', { message: data.error });
    message.error(`执行失败: ${data.error}`);
  },
  onConnectionStateChange: (state) => {
    emit('connection-state-change', state);
  },
});

// ==================== Refs ====================

const inputFormRef = ref<InstanceType<typeof DynamicInputForm>>();

// ==================== State ====================

/** 输入值 */
const inputValues = ref<Record<string, any>>({});

// ==================== Computed ====================

/** 是否可以运行 */
const canRun = computed(() => {
  return props.workflowId && !debugStore.isRunning;
});

/** 是否有输入值 */
const hasInputValues = computed(() => {
  return Object.keys(inputValues.value).some((key) => {
    const value = inputValues.value[key];
    return value !== undefined && value !== null && value !== '';
  });
});

// ==================== Lifecycle ====================

onBeforeUnmount(() => {
  // 断开 WebSocket 连接
  disconnectWs();
});

// ==================== Methods ====================

/**
 * 执行预览运行
 */
async function handleRun() {
  // 验证表单
  const isValid = await inputFormRef.value?.validate();
  if (!isValid) {
    message.warning('请填写必填字段');
    return;
  }

  try {
    // 获取启用的检查点ID列表
    const breakpointIds = debugStore.enabledBreakpointIds;

    // 过滤空值字段：未填写（空串/null/undefined）的字段不传，
    // 交给后端 START 节点用字段默认值兜底（否则 {query: ""} 会覆盖默认值）
    const inputs = Object.fromEntries(
      Object.entries(inputValues.value).filter(
        ([, v]) => v !== '' && v !== null && v !== undefined,
      ),
    );

    // 启动调试状态（executionId 待首个事件 workflow.started 回填到 debugStore）
    debugStore.startPreviewRun('');

    // 建立 WebSocket 并发送执行入参：连接即触发同步执行，事件经该连接实时回推
    connectWs(props.workflowId, {
      inputs,
      breakpoints: breakpointIds,
    });

    // 触发事件
    emit('workflowStarted', debugStore.executionId || '');

    message.success('开始执行');
  } catch (error: any) {
    message.error(error.message || '执行失败');
    emit('workflowFailed', error);
  }
}

/**
 * 停止执行
 *
 * 预览运行走 execute-sync（本进程执行），HTTP /cancel 无法中断本进程的
 * runtime.run()，真正的停止是断开 WS（下一次 send_text 失败即中断执行）。
 * 故：先断连接 + 复位本地状态（保证按钮一定有反馈），再在 executionId
 * 已回填时补一发 HTTP cancel（兼容 worker 异步链路，失败也不影响本地停止）。
 */
async function handleStop() {
  // 先断开 WebSocket 并复位调试状态：无论 executionId 是否已回填都能立即停止
  disconnectWs();
  debugStore.cancelExecution();

  // executionId 由首个 workflow.started 事件回填；存在时再请求后端取消
  const execId = debugStore.executionId;
  if (execId) {
    try {
      await cancelExecution(execId);
    } catch (error: any) {
      // 取消接口失败不阻断本地停止（WS 已断开，执行已中断）
      message.warning(error.message || '停止请求发送失败');
    }
  }
  message.info('已停止执行');
}

/**
 * 继续执行
 */
async function handleContinue() {
  if (!debugStore.executionId) return;

  try {
    await resumeExecution(debugStore.executionId);
    debugStore.resumeExecution();
    message.success('继续执行');
  } catch (error: any) {
    message.error(error.message || '继续执行失败');
  }
}

/**
 * 清空输入
 */
function handleClearInputs() {
  inputFormRef.value?.resetFields();
  inputValues.value = {};
}

// ==================== Expose ====================

defineExpose({
  /** 执行预览运行 */
  run: handleRun,
  /** 停止执行 */
  stop: handleStop,
  /** 获取输入值 */
  getInputValues: () => ({ ...inputValues.value }),
  /** 设置输入值 */
  setInputValues: (values: Record<string, any>) => {
    inputValues.value = { ...values };
    inputFormRef.value?.setValues(values);
  },
  /** 获取执行完成结果 */
  getExecutionResult: () => debugStore.result,
  /** 获取节点追踪 */
  getNodeTraces: () => [...debugStore.nodeTraces.values()],
  /**
   * 重置（BUG5）：每次打开调试面板时调用，断开上一次的 WS 连接并清空输入表单，
   * 不缓存上次的调试数据
   */
  reset: () => {
    disconnectWs();
    handleClearInputs();
  },
  /** SSE 连接状态 */
  connectionState,
});
</script>

<template>
  <div class="preview-runner" data-testid="workflow-preview-runner">
    <!-- 顶部工具栏 -->
    <div class="runner-toolbar">
      <div class="toolbar-left">
        <a-button
          type="primary"
          :loading="debugStore.isRunning"
          :disabled="!canRun"
          @click="handleRun"
        >
          <template #icon><PlayCircleOutlined /></template>
          预览运行
        </a-button>
        <a-button v-if="debugStore.isRunning" danger @click="handleStop">
          <template #icon><StopOutlined /></template>
          停止
        </a-button>
      </div>
    </div>

    <!-- 执行状态指示器 -->
    <div
      v-if="debugStore.isRunning || debugStore.isPaused"
      class="execution-status"
    >
      <a-alert :type="debugStore.isPaused ? 'warning' : 'info'" show-icon>
        <template #message>
          <span v-if="debugStore.isPaused">
            <PauseCircleOutlined /> 执行已暂停 - 命中检查点
          </span>
          <span v-else>
            <LoadingOutlined /> 正在执行...
            <a-tag
              v-if="connectionState === 'connected'"
              color="success"
              size="small"
              >已连接</a-tag
            >
            <a-tag
              v-else-if="connectionState === 'connecting'"
              color="processing"
              size="small"
              >连接中</a-tag
            >
            <a-tag
              v-else-if="connectionState === 'error'"
              color="error"
              size="small"
              >连接错误</a-tag
            >
          </span>
        </template>
        <template v-if="debugStore.isPaused" #description>
          <a-space>
            <a-button size="small" type="primary" @click="handleContinue">
              <FastForwardOutlined /> 继续执行
            </a-button>
          </a-space>
        </template>
      </a-alert>
    </div>

    <!-- 动态输入表单 -->
    <div class="input-form-section">
      <div class="section-header">
        <span class="section-title">输入参数</span>
        <a-button
          v-if="hasInputValues"
          type="link"
          size="small"
          @click="handleClearInputs"
        >
          清空
        </a-button>
      </div>
      <DynamicInputForm
        ref="inputFormRef"
        :fields="inputFields"
        v-model:values="inputValues"
      />
      <a-empty
        v-if="!inputFields || inputFields.length === 0"
        description="START 节点未定义输入字段"
      />
    </div>
  </div>
</template>

<style scoped lang="less">
.preview-runner {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
  overflow: hidden;
}

.runner-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid #f0f0f0;

  .toolbar-left {
    display: flex;
    gap: 8px;
    align-items: center;
  }
}

.execution-status {
  margin-bottom: 16px;

  :deep(.ant-alert) {
    .ant-alert-message {
      display: flex;
      gap: 8px;
      align-items: center;
    }
  }
}

.input-form-section {
  flex: 1;
  overflow-y: auto;

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;

    .section-title {
      font-size: 14px;
      font-weight: 500;
      color: #262626;
    }
  }
}

:deep(.ant-dropdown-menu) {
  max-height: 300px;
  overflow-y: auto;
}
</style>
