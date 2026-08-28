<script setup lang="ts">
interface VariableInfo {
  name: string;
  type?: string;
  direction?: 'input' | 'output';
}

interface BranchInfo {
  id: string;
  name: string;
  color: string;
}

interface Props {
  inputVariables?: VariableInfo[];
  summary?: string;
  branches?: BranchInfo[];
}

withDefaults(defineProps<Props>(), {
  inputVariables: () => [],
  summary: '',
  branches: () => [],
});
</script>

<template>
  <div class="node-content">
    <!-- 输入变量 -->
    <div v-if="inputVariables.length > 0" class="content-section">
      <div class="variable-list">
        <div
          v-for="variable in inputVariables"
          :key="variable.name"
          class="variable-item"
          :class="variable.direction"
        >
          <span class="var-direction">{{
            variable.direction === 'output' ? '输出' : '输入'
          }}</span>
          <span v-if="variable.type" class="var-type">{{ variable.type }}</span>
          <span class="var-name">{{ variable.name }}</span>
        </div>
      </div>
    </div>

    <!-- 配置摘要 -->
    <div v-if="summary" class="content-section">
      <div class="config-summary">{{ summary }}</div>
    </div>

    <!-- 分支列表 -->
    <div v-if="branches.length > 0" class="content-section">
      <div class="branch-list">
        <div
          v-for="branch in branches"
          :key="branch.id"
          class="branch-item"
          :style="{ borderLeftColor: branch.color }"
        >
          {{ branch.name }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.node-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.content-section {
  padding: 0 2px;
}

.variable-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.variable-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  font-size: 10px;
  border-radius: 3px;

  &.input {
    background: #f0f5ff;
    color: #1890ff;
  }

  &.output {
    background: #f6ffed;
    color: #52c41a;
  }

  .var-direction,
  .var-type {
    color: #8c8c8c;
    font-size: 9px;
  }

  .var-name {
    font-weight: 500;
  }
}

.config-summary {
  padding: 2px 6px;
  font-size: 10px;
  color: #595959;
  background: #fafafa;
  border-radius: 3px;
}

.branch-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.branch-item {
  padding: 2px 6px;
  font-size: 10px;
  color: #595959;
  background: #fafafa;
  border-left: 2px solid #1890ff;
  border-radius: 0 3px 3px 0;
}
</style>
