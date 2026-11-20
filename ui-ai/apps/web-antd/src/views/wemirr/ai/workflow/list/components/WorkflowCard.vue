<script lang="ts" setup>
/**
 * 工作流卡片组件
 */
import type { WorkflowPageResp } from '#/api/ai-workflow/types';

import { computed } from 'vue';

import { useAccess } from '@vben/access';

import {
  ApartmentOutlined,
  ApiOutlined,
  AppstoreOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlusOutlined,
  ShareAltOutlined,
} from '@ant-design/icons-vue';
import { Tag, Tooltip } from 'ant-design-vue';

import { canAction } from '#/api/acl';

interface Props {
  item: WorkflowPageResp;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'edit', item: WorkflowPageResp): void;
  (e: 'remove', item: WorkflowPageResp): void;
  (e: 'copy', item: WorkflowPageResp): void;
  (e: 'history', item: WorkflowPageResp): void;
  (e: 'chat', item: WorkflowPageResp): void;
  (e: 'api', item: WorkflowPageResp): void;
  (e: 'template', item: WorkflowPageResp): void;
  (e: 'grant', item: WorkflowPageResp): void;
}>();

const { hasPermission } = useAccess();

/**
 * 按钮显隐 = 功能权限 AND 这一条上的动作：前者管「这类事让不让你做」，
 * 后者管「这一条你能不能做」。actions 由列表接口按 ACL 求值下发，
 * 前端不复制判定规则（自己猜「是不是我创建的」早晚和后端分叉）。
 */
function can(perm: string, action: string) {
  return hasPermission(perm) && canAction(props.item, action);
}

const statusConfig: Record<string, { color: string; text: string }> = {
  DRAFT: { color: 'default', text: '草稿' },
  PUBLISHED: { color: 'success', text: '已发布' },
};

function getStatusConfig(status: string) {
  return statusConfig[status] || { color: 'default', text: status };
}

/** 卡片只留一行元信息，年份省到月日时分（完整时间戳在执行详情里仍有） */
function formatTime(time: string) {
  if (!time) return '-';
  return new Date(time).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * 节点数/版本/创建人/更新时间拼成一整行文本：分成多个 span 在窄卡上会被挤到
 * 换行（一行变两行又把卡片高度顶回去），拼成字符串才能用 ellipsis 收尾。
 */
const metaText = computed(() =>
  [
    `${props.item.nodeCount || 0} 个节点`,
    `v${props.item.currentVersion || 1}`,
    props.item.creatorName ? `创建人：${props.item.creatorName}` : '',
    formatTime(props.item.updateTime),
  ]
    .filter(Boolean)
    .join(' · '),
);
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
      <h3 class="card-title" :title="item.name">{{ item.name }}</h3>
      <Tag class="card-status" :color="getStatusConfig(item.status).color">
        {{ getStatusConfig(item.status).text }}
      </Tag>
    </div>

    <p class="card-desc" :title="item.description || '暂无描述'">
      {{ item.description || '暂无描述' }}
    </p>

    <div class="card-footer">
      <div class="card-meta" :title="metaText">{{ metaText }}</div>
      <div class="card-actions">
        <Tooltip v-if="can('workflow:workflow:edit', 'edit')" title="编辑">
          <a-button
            data-testid="workflow-card-edit"
            type="text"
            size="small"
            @click="emit('edit', item)"
          >
            <template #icon><EditOutlined /></template>
          </a-button>
        </Tooltip>
        <!-- 去对话：单独一档 chat（后端 chat 蕴含 use），只被授到「使用」的人能引用跑但不开对话 -->
        <Tooltip v-if="can('workflow:execution:run', 'chat')" title="去对话">
          <a-button
            data-testid="workflow-card-chat"
            type="text"
            size="small"
            @click="emit('chat', item)"
          >
            <template #icon><PlusOutlined /></template>
          </a-button>
        </Tooltip>
        <!-- 发布已迁移至工作流编辑页（保存按钮右侧），列表页不再提供发布入口 -->
        <Tooltip v-if="can('workflow:workflow:add', 'copy')" title="复制">
          <a-button
            data-testid="workflow-card-copy"
            type="text"
            size="small"
            @click="emit('copy', item)"
          >
            <template #icon><CopyOutlined /></template>
          </a-button>
        </Tooltip>
        <!-- key 列表带明文，拿到就等于能跑，所以 API 访问按 apikey 而不是 use -->
        <Tooltip v-if="can('workflow:apikey:list', 'apikey')" title="API 访问">
          <a-button
            data-testid="workflow-card-api"
            type="text"
            size="small"
            @click="emit('api', item)"
          >
            <template #icon><ApiOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip
          v-if="can('workflow:template:add', 'template')"
          title="保存为模块"
        >
          <a-button
            data-testid="workflow-card-template"
            type="text"
            size="small"
            @click="emit('template', item)"
          >
            <template #icon><AppstoreOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip
          v-if="can('workflow:execution:list', 'history')"
          title="执行历史"
        >
          <a-button
            data-testid="workflow-card-history"
            type="text"
            size="small"
            @click="emit('history', item)"
          >
            <template #icon><HistoryOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip
          v-if="can('workflow:workflow:grant', 'share')"
          title="授权给别人"
        >
          <a-button
            data-testid="workflow-card-grant"
            type="text"
            size="small"
            @click="emit('grant', item)"
          >
            <template #icon><ShareAltOutlined /></template>
          </a-button>
        </Tooltip>
        <Tooltip v-if="can('workflow:workflow:delete', 'delete')" title="删除">
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
// 紧凑卡片：头部一行（图标+名称+状态）、描述一行、底行（元信息 + 操作图标），
// 单卡高度约 250px 降到 90px 上下，一屏能多看几倍实例
.workflow-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px 12px;
  background: var(--layout-header-bg-color, #fff);
  border: 1px solid var(--border-color, rgb(15 23 42 / 6%));
  border-radius: 10px;
  box-shadow: 0 2px 8px rgb(15 23 42 / 4%);
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease,
    border-color 0.18s ease;

  &:hover {
    border-color: #c7c9f8;
    box-shadow: 0 6px 16px rgb(79 70 229 / 10%);
    transform: translateY(-1px);
  }

  .card-header {
    display: flex;
    gap: 8px;
    align-items: center;
    min-width: 0;

    .card-icon {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      font-size: 14px;
      color: #4f46e5;
      background: linear-gradient(
        145deg,
        rgb(79 70 229 / 12%),
        rgb(124 58 237 / 14%)
      );
      border-radius: 8px;
    }

    .card-title {
      flex: 1;
      min-width: 0;
      margin: 0;
      overflow: hidden;
      font-size: 14px;
      font-weight: 600;
      line-height: 20px;
      color: var(--text-color-1, #1f2329);
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    // 状态标签不参与收缩，否则窄卡上会被标题压成两行
    .card-status {
      flex-shrink: 0;
      margin-inline-end: 0;
      padding: 0 5px;
      font-size: 11px;
      line-height: 16px;
    }
  }

  .card-desc {
    margin: 0;
    overflow: hidden;
    font-size: 12px;
    line-height: 18px;
    color: #666;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .card-footer {
    display: flex;
    gap: 8px;
    align-items: center;
    min-width: 0;
    padding-top: 6px;
    border-top: 1px solid #f0f0f0;

    .card-meta {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      font-size: 11px;
      line-height: 18px;
      color: #999;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    // 操作区改纯图标（与知识库列表同一口径）并收窄内边距，避免挤掉元信息
    .card-actions {
      display: flex;
      flex: 0 0 auto;
      align-items: center;

      :deep(.ant-btn) {
        padding: 0 3px;
        font-size: 13px;
      }
    }
  }
}
</style>
