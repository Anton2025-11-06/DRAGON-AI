<script lang="ts" setup name="ModelTrainPage">
/**
 * 模型训练（对应后端 service_train）
 * 后端未开发，先提供占位页；开发完成后替换为真实列表/表单
 */
import { RocketOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="模型训练"
    service="service_train"
    description="提供训练任务管理、超参数配置、训练进度监控、模型产物产出等能力，对应后端微服务 service_train（接口前缀 /api/train）。"
    :features="[
      { icon: '📋', title: '训练任务管理', description: '创建、暂停、恢复、删除训练任务，查看任务列表与状态' },
      { icon: '⚙️', title: '超参数配置', description: '配置学习率、批次大小、Epoch 等训练参数' },
      { icon: '📈', title: '训练监控', description: '实时查看 Loss、精度曲线与训练日志' },
    ]"
  >
    <template #icon><RocketOutlined /></template>
  </ComingSoon>
</template>