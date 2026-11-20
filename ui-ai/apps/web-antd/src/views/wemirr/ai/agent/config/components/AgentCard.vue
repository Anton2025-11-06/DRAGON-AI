<script lang="ts" setup>
/**
 * 智能体卡片：统一后台卡片层级，操作保持查看 / 编辑 / 对话 / 删除。
 */
import {
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  MessageOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import { Avatar, Card, Tag, Tooltip } from 'ant-design-vue';

defineProps<{
  item: any;
}>();

defineEmits(['view', 'edit', 'remove', 'chat']);

const getAvatarText = (name: string) =>
  name ? name.charAt(0).toUpperCase() : 'A';

/** 稳定的低饱和实色，不再使用随机渐变和装饰性在线状态。 */
const getAvatarColor = (id: number) => {
  const colors = ['#4758c7', '#1677a8', '#147d73', '#8a5a16', '#7550a5'];
  return colors[Math.abs(id || 0) % colors.length];
};
</script>

<template>
  <Card class="agent-card" :bordered="true">
    <div class="card-content">
      <div class="agent-heading">
        <Avatar
          v-if="item.avatar"
          :src="item.avatar"
          :size="56"
          class="agent-avatar"
        />
        <Avatar
          v-else
          :size="56"
          class="agent-avatar default-avatar"
          :style="{ background: getAvatarColor(item.id) }"
        >
          {{ getAvatarText(item.name) }}
        </Avatar>
        <div class="agent-heading-text">
          <h3 class="agent-name" :title="item.name">
            <RobotOutlined class="name-icon" />
            <span>{{ item.name }}</span>
          </h3>
          <span class="agent-caption">对话智能体</span>
        </div>
      </div>

      <p class="agent-description" :title="item.description">
        {{ item.description || '暂未填写智能体描述' }}
      </p>

      <div class="agent-tags">
        <Tooltip v-if="item.kbId" title="已关联知识库">
          <Tag color="purple" class="feature-tag">
            <DatabaseOutlined />
            <span>知识库</span>
          </Tag>
        </Tooltip>
        <Tooltip v-if="item.tools?.length" :title="`已配置 ${item.tools.length} 个工具`">
          <Tag color="blue" class="feature-tag">
            <ToolOutlined />
            <span>{{ item.tools.length }} 工具</span>
          </Tag>
        </Tooltip>
        <Tag v-if="!item.kbId && !item.tools?.length" class="feature-tag">
          <MessageOutlined />
          <span>基础对话</span>
        </Tag>
      </div>
    </div>

    <template #actions>
      <Tooltip title="查看详情">
        <span
          class="action-item action-view"
          role="button"
          tabindex="0"
          @click.stop="$emit('view', item)"
          @keydown.enter.stop="$emit('view', item)"
        >
          <EyeOutlined />
        </span>
      </Tooltip>
      <Tooltip title="编辑配置">
        <span
          class="action-item action-edit"
          role="button"
          tabindex="0"
          @click.stop="$emit('edit', item)"
          @keydown.enter.stop="$emit('edit', item)"
        >
          <EditOutlined />
        </span>
      </Tooltip>
      <Tooltip title="开始对话">
        <span
          class="action-item action-chat"
          role="button"
          tabindex="0"
          @click.stop="$emit('chat', item)"
          @keydown.enter.stop="$emit('chat', item)"
        >
          <MessageOutlined />
        </span>
      </Tooltip>
      <Tooltip title="删除">
        <span
          class="action-item action-delete"
          role="button"
          tabindex="0"
          @click.stop="$emit('remove', item)"
          @keydown.enter.stop="$emit('remove', item)"
        >
          <DeleteOutlined />
        </span>
      </Tooltip>
    </template>
  </Card>
</template>

<style lang="less" scoped>
.agent-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--component-background, #fff);
  border-color: var(--border-color, rgb(15 23 42 / 9%));
  border-radius: 12px;
  box-shadow: 0 2px 10px rgb(15 23 42 / 4%);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;

  &:hover {
    border-color: hsl(var(--primary) / 35%);
    box-shadow: 0 8px 24px rgb(15 23 42 / 8%);
    transform: translateY(-2px);
  }

  :deep(.ant-card-body) {
    flex: 1;
    padding: 0;
  }
}

.card-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  padding: 18px;
}

.agent-heading {
  display: flex;
  gap: 12px;
  align-items: center;
  min-width: 0;
  margin-bottom: 14px;
}

.agent-heading-text {
  flex: 1;
  min-width: 0;
}

.agent-avatar {
  flex-shrink: 0;
  box-shadow: none;

  &.default-avatar {
    font-size: 22px;
    font-weight: 600;
    color: #fff;
  }
}

.agent-name {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--text-color-1, #1f2329);

  > span:last-child {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .name-icon {
    flex-shrink: 0;
    font-size: 15px;
    color: hsl(var(--primary));
  }
}

.agent-caption {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-color-3, #8a9099);
}

.agent-description {
  display: -webkit-box;
  flex: 1;
  min-height: 42px;
  margin: 0 0 16px;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-color-2, #4e5969);
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.agent-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;

  .feature-tag {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    padding: 2px 8px;
    margin: 0;
    font-size: 12px;
    border-radius: 6px;
  }
}

:deep(.ant-card-actions) {
  padding: 0;
  margin: 0;
  background: var(--hover-bg-color, #fafbfc);
  border-top-color: var(--border-color, #edf0f4);
  border-radius: 0 0 12px 12px;

  > li {
    margin: 0 !important;
    border-right-color: var(--border-color, #edf0f4);

    .action-item {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 42px;
      font-size: 15px;
      cursor: pointer;
      transition:
        color 0.16s ease,
        background-color 0.16s ease;

      &.action-view,
      &.action-edit,
      &.action-chat {
        color: var(--text-color-2, #4e5969);
      }

      &.action-delete {
        color: hsl(var(--destructive));
      }

      &:hover {
        color: hsl(var(--primary));
        background: hsl(var(--primary) / 7%);
      }

      &.action-delete:hover {
        color: hsl(var(--destructive));
        background: hsl(var(--destructive) / 7%);
      }
    }
  }
}
</style>
