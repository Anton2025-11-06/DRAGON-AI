<script lang="ts" setup name="ModelDeployPage">
/**
 * 模型部署（对应后端 service_inference）
 * 后端未开发，先提供占位页；开发完成后替换为真实列表/表单
 */
import { DeploymentUnitOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="模型部署"
    service="service_inference"
    description="提供模型服务部署、实例扩缩容、在线推理、调用监控等能力，对应后端微服务 service_inference（接口前缀 /api/inference）。"
    :features="[
      { icon: '🚀', title: '部署管理', description: '将训练/归档模型部署为在线服务实例' },
      { icon: '⚡', title: '在线推理', description: '提供 HTTP 推理接口，支持流式与非流式输出' },
      { icon: '📊', title: '调用监控', description: '监控实例 QPS、延迟、成功率与资源水位' },
    ]"
  >
    <template #icon><DeploymentUnitOutlined /></template>
  </ComingSoon>
</template>