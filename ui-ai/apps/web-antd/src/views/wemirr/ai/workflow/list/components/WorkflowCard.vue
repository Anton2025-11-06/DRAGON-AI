<script lang="ts" setup>
/**
 * 工作流卡片组件
 */
import type { WorkflowPageResp } from '#/api/ai-workflow/types';

import {
  ApiOutlined,
  ApartmentOutlined,
  AppstoreOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons-vue';
import { Tag, Tooltip } from 'ant-design-vue';

interface Props {
  item: WorkflowPageResp;
}

defineProps<Props>();

const emit = defineEmits<{
  (e: 'edit', item: WorkflowPageResp): void;
  (e: 'remove', item: WorkflowPageResp): void;
  (e: 'copy', item: WorkflowPageResp): void;
  (e: 'history', item: WorkflowPageResp): void;
  (e: 'execute', item: WorkflowPageResp): void;
  (e: 'api', item: WorkflowPageResp): void;
  (e: 'template', item: WorkflowPageResp): void;
}>();

const statusConfig: Record<string, { color: string; text: string }> = {
  DRAFT: { color: 'default', text: '草稿' },
  PUBLISHED: { color: 'success', text: '已发布' },
};

function getStatusConfig(status: string) {
  return statusConfig[status] || { color: 'default', text: status };
}

function formatTime(time: string) {
  if (!time) return '-';
  return new Date(time).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}
</script>

<template>
  <div
    class="workflow-card"
    data-testid="workflow-card"
    :data-workflow-id="item.id"
    :data-workflow-status="item.status"
  >
    <div class="card-header">
      <div class="card-icon">
        <ApartmentOutlined />
      </div>
      <div class="card-title-wrapper">
        <h3 class="card-title">{{ item.name }}</h3>
        <Tag :color="getStatusConfig(item.status).color">
          {{ getStatusConfig(item.status).text }}
        </Tag>
      </div>
    </div>

    <div class="card-body">
      <p class="card-desc">{{ item.description || '暂无描述' }}</p>
      <div class="card-meta">
        <span class="meta-item">
          <ApartmentOutlined />
          {{ item.nodeCount || 0 }} 个节点
        </span>
        <span class="meta-item"> v{{ item.currentVersion || 1 }} </span>
      </div>
    </div>

    <div class="card-footer">
      <span class="update-time">{{ formatTime(item.updateTime) }}</span>
      <div class="card-actions">
        <Tooltip title="编辑">
          <a-button
            data-testid="workflow-card-edit"
            type="text"
            size="small"
            @click="emit('edit', item)"
          >
            <template #icon><EditOutlined /></template>
          </a-button>
        </Tooltip>
        <!-- 执行按钮常显：未发布时点击由列表页提示先发布（需求 2.1） -->
        <Tooltip title="执行">
          <a-button
            data-testid="workflow-card-execute"
            type="text"
            size="small"
            @click="emit('execute', item)"
          >
            <template #icon><PlayCircleOutlined /></template>
          </a-button>
        </Tooltip>
        <!-- 发布已迁移至工作流编辑页（保存按钮右侧），需求：列表页不再提供发布入口 -->
        <Tooltip title="复制">
          <a-button
            data-testid="workflow-card-copy"
            type="text"
            size="small"
            @click="emit('copy', item)"
          >
            <template #icon><CopyOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip title="API 访问">
          <a-button
            data-testid="workflow-card-api"
            type="text"
            size="small"
            @click="emit('api', item)"
          >
            <template #icon><ApiOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip title="保存为模块">
          <a-button
            data-testid="workflow-card-template"
            type="text"
            size="small"
            @click="emit('template', item)"
          >
            <template #icon><AppstoreOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip title="执行历史">
          <a-button
            data-testid="workflow-card-history"
            type="text"
            size="small"
            @click="emit('history', item)"
          >
            <template #icon><HistoryOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip title="删除">
          <a-button
            data-testid="workflow-card-remove"
            type="text"
            size="small"
            danger
            @click="emit('remove', item)"
          >
            <template #icon><DeleteOutlined /></template>
          </a-button>
        </Tooltip>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.workflow-card {
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

  .card-header {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    margin-bottom: 12px;

    .card-icon {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 40px;
      height: 40px;
      font-size: 20px;
      color: #1890ff;
      background: linear-gradient(
        135deg,
        rgb(24 144 255 / 10%) 0%,
        rgb(114 46 209 / 10%) 100%
      );
      border-radius: 8px;
    }

    .card-title-wrapper {
      display: flex;
      flex: 1;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      min-width: 0;

      .card-title {
        margin: 0;
        overflow: hidden;
        font-size: 16px;
        font-weight: 600;
        color: #333;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }
  }

  .card-body {
    flex: 1;
    margin-bottom: 12px;

    .card-desc {
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

    .card-meta {
      display: flex;
      gap: 16px;
      font-size: 12px;
      color: #999;

      .meta-item {
        display: flex;
        gap: 4px;
        align-items: center;
      }
    }
  }

  .card-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 12px;
    border-top: 1px solid #f0f0f0;

    .update-time {
      font-size: 12px;
      color: #999;
    }

    .card-actions {
      display: flex;
      gap: 2px;
      flex-wrap: wrap;
      justify-content: flex-end;
      max-width: 60%;
    }
  }
}
</style>
