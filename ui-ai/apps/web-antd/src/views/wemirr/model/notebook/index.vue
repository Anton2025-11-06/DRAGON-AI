<script lang="ts" setup name="ModelNotebookPage">
/**
 * NoteBook 开发（对应后端 service_notebook）
 * 后端未开发，先提供占位页；开发完成后替换为真实 notebook 界面
 */
import { CodeOutlined } from '@ant-design/icons-vue';

import ComingSoon from '../components/ComingSoon.vue';
</script>

<template>
  <ComingSoon
    title="NoteBook 开发"
    service="service_notebook"
    description="提供在线 Notebook 环境，支持 Python 代码编写、数据探索、模型试验等能力，对应后端微服务 service_notebook（接口前缀 /api/notebook）。"
    :features="[
      { icon: '💻', title: '在线编码', description: '基于浏览器的代码编辑器，支持代码高亮与补全' },
      { icon: '🔬', title: '数据探索', description: '连接数据集工厂数据，进行数据洞察与可视化' },
      { icon: '🧠', title: '模型试验', description: '快速开展模型调参试验并沉淀试验记录' },
    ]"
  >
    <template #icon><CodeOutlined /></template>
  </ComingSoon>
</template>