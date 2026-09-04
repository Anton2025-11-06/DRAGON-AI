<script lang="ts" setup name="ModelEvalPage">
/**
 * 模型评测（对应后端 service_eval_model）
 * 后端未开发，先提供占位页；开发完成后替换为真实列表/表单
 */
import { AuditOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="模型评测"
    service="service_eval_model"
    description="提供评测集管理、评测任务、指标报告、模型对比等能力，对应后端微服务 service_eval_model（接口前缀 /api/eval_model）。"
    :features="[
      { icon: '📚', title: '评测集管理', description: '维护标准评测数据集与评测样例' },
      { icon: '🧪', title: '评测任务', description: '提交模型评测任务并跟踪执行进度' },
      { icon: '🏆', title: '指标报告', description: '生成准确率、BLEU 等指标报告，支持多模型对比' },
    ]"
  >
    <template #icon><AuditOutlined /></template>
  </ComingSoon>
</template>