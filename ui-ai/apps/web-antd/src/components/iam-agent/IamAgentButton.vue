<script lang="ts" setup>
import { VbenIcon } from '@vben/icons';

defineEmits<{ click: [] }>();
</script>

<template>
  <a-tooltip title="打开 AI 助手">
    <a-button
      aria-label="打开 AI 助手"
      class="mr-1 flex size-8 items-center justify-center"
      shape="circle"
      type="text"
      @click="$emit('click')"
    >
      <VbenIcon icon="lucide:bot" class="size-4" />
    </a-button>
  </a-tooltip>
</template>
