<script lang="ts" setup name="ModelArchivePage">
/**
 * 模型归档（对应后端 service_train 的模型产物管理）
 * 后端未开发，先提供占位页；开发完成后替换为真实列表/表单
 */
import { FolderOpenOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="模型归档"
    service="service_train"
    description="提供模型版本归档、产物下载、版本回滚等能力，归档数据由 service_train 产出并登记（接口前缀 /api/train）。"
    :features="[
      { icon: '🗂️', title: '归档管理', description: '登记训练产出的模型版本与元数据' },
      { icon: '⬇️', title: '产物下载', description: '下载指定版本的模型权重与配置文件' },
      { icon: '🔄', title: '版本回滚', description: '支持在部署与评测中选择历史归档版本' },
    ]"
  >
    <template #icon><FolderOpenOutlined /></template>
  </ComingSoon>
</template>