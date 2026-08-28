<script setup lang="ts">
import { DownOutlined, UpOutlined } from '@ant-design/icons-vue';

interface Props {
  collapsed: boolean;
}

defineProps<Props>();

const emit = defineEmits<{
  (e: 'toggle'): void;
}>();

function handleClick() {
  emit('toggle');
}
</script>

<template>
  <span
    class="collapse-control"
    :title="collapsed ? '展开' : '折叠'"
    @click.stop="handleClick"
  >
    <DownOutlined v-if="collapsed" />
    <UpOutlined v-else />
  </span>
</template>

<style scoped lang="less">
.collapse-control {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 10px;
  color: #8c8c8c;
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.2s;

  &:hover {
    background: #f5f5f5;
    color: #262626;
  }
}
</style>
