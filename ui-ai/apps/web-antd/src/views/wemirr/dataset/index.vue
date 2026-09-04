<script lang="ts" setup name="DatasetPage">
/**
 * 数据集工厂（对应后端 service_datasets）
 * 后端未开发，先提供占位页；开发完成后替换为真实列表/表单
 */
import { DatabaseOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../model/components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="数据集管理"
    service="service_datasets"
    description="提供数据集上传、版本管理、数据校验、标注协同、导出下载等能力，对应后端微服务 service_datasets（接口前缀 /api/datasets）。"
    :features="[
      { icon: '📦', title: '数据集管理', description: '创建、上传、删除数据集并维护版本' },
      { icon: '✅', title: '数据校验', description: '自动校验数据格式、标签完整性与分布统计' },
      { icon: '🏷️', title: '标注协同', description: '多人协作标注，标注进度实时可见' },
    ]"
  >
    <template #icon><DatabaseOutlined /></template>
  </ComingSoon>
</template>