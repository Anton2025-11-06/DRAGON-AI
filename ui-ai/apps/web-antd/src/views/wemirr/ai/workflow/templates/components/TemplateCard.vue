<script lang="ts" setup>
/**
 * 模板卡片组件
 * 展示单个工作流模板的信息
 */
import type { WorkflowTemplateResp } from '#/api/ai-workflow/types';

import {
  AppstoreOutlined,
  BookOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileTextOutlined,
  MessageOutlined,
  MoreOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { Tag } from 'ant-design-vue';

interface Props {
  item: WorkflowTemplateResp;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'use', item: WorkflowTemplateResp): void;
  (e: 'remove', item: WorkflowTemplateResp): void;
  (e: 'export', item: WorkflowTemplateResp): void;
  (e: 'copy', item: WorkflowTemplateResp): void;
}>();

// 分类配置
const categoryConfig: Record<string, { color: string; icon: any }> = {
  rag: { color: 'blue', icon: BookOutlined },
  summary: { color: 'green', icon: FileTextOutlined },
  extraction: { color: 'orange', icon: DatabaseOutlined },
  conversation: { color: 'purple', icon: MessageOutlined },
  generation: { color: 'cyan', icon: FileTextOutlined },
};

// 获取分类图标
function getCategoryIcon() {
  return categoryConfig[props.item.category]?.icon || AppstoreOutlined;
}

// 获取分类颜色
function getCategoryColor() {
  return categoryConfig[props.item.category]?.color || 'default';
}

// 使用模板
function handleUse() {
  emit('use', props.item);
}

// 删除模板
function handleRemove() {
  emit('remove', props.item);
}

// 导出模板
function handleExport() {
  emit('export', props.item);
}

// 复制模板
function handleCopy() {
  emit('copy', props.item);
}
</script>

<template>
  <div class="template-card">
    <div class="card-header">
      <div class="icon-wrapper" :class="item.category">
        <component :is="getCategoryIcon()" />
      </div>
      <div class="header-info">
        <div class="title">
          {{ item.name }}
          <Tag v-if="item.builtIn" color="gold" size="small">内置</Tag>
        </div>
        <Tag :color="getCategoryColor()" size="small">
          {{ item.categoryDesc || item.category }}
        </Tag>
      </div>
      <a-dropdown v-if="!item.builtIn" trigger="click">
        <a-button type="text" size="small">
          <template #icon><MoreOutlined /></template>
        </a-button>
        <template #overlay>
          <a-menu>
            <a-menu-item key="copy" @click="handleCopy">
              <CopyOutlined /> 复制
            </a-menu-item>
            <a-menu-item key="export" @click="handleExport">
              <DownloadOutlined /> 导出
            </a-menu-item>
            <a-menu-divider />
            <a-menu-item key="delete" danger @click="handleRemove">
              <DeleteOutlined /> 删除
            </a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
      <a-dropdown v-else trigger="click">
        <a-button type="text" size="small">
          <template #icon><MoreOutlined /></template>
        </a-button>
        <template #overlay>
          <a-menu>
            <a-menu-item key="copy" @click="handleCopy">
              <CopyOutlined /> 复制为自定义模板
            </a-menu-item>
            <a-menu-item key="export" @click="handleExport">
              <DownloadOutlined /> 导出
            </a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
    </div>

    <div class="card-body">
      <p class="description">{{ item.description || '暂无描述' }}</p>
      <div class="meta">
        <span class="node-count">{{ item.nodeCount || 0 }} 个节点</span>
      </div>
    </div>

    <div class="card-footer">
      <a-button type="primary" block @click="handleUse">
        <template #icon><PlusOutlined /></template>
        使用此模板
      </a-button>
    </div>
  </div>
</template>

<style lang="less" scoped>
.template-card {
  display: flex;
  flex-direction: column;
  padding: 16px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  transition: all 0.3s;

  &:hover {
    border-color: #1890ff;
    box-shadow: 0 4px 12px rgb(0 0 0 / 8%);
  }
}

.card-header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 12px;

  .icon-wrapper {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    font-size: 20px;
    border-radius: 8px;

    &.rag {
      color: #1890ff;
      background: #e6f7ff;
    }

    &.summary {
      color: #52c41a;
      background: #f6ffed;
    }

    &.extraction {
      color: #fa8c16;
      background: #fff7e6;
    }

    &.conversation {
      color: #722ed1;
      background: #f9f0ff;
    }

    &.generation {
      color: #13c2c2;
      background: #e6fffb;
    }
  }

  .header-info {
    flex: 1;
    min-width: 0;

    .title {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-bottom: 4px;
      overflow: hidden;
      font-size: 14px;
      font-weight: 500;
      color: #333;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

.card-body {
  flex: 1;
  margin-bottom: 12px;

  .description {
    display: -webkit-box;
    margin: 0 0 8px;
    overflow: hidden;
    font-size: 13px;
    line-height: 1.5;
    color: #666;
    text-overflow: ellipsis;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .meta {
    font-size: 12px;
    color: #999;

    .node-count {
      display: inline-flex;
      gap: 4px;
      align-items: center;
    }
  }
}

.card-footer {
  margin-top: auto;
}
</style>
