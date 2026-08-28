<script lang="ts" setup>
/**
 * 智能体卡片组件
 * 展示智能体信息，支持查看、编辑、删除操作
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

/** 获取头像文字 */
const getAvatarText = (name: string) => {
  return name ? name.charAt(0).toUpperCase() : 'A';
};

/** 获取随机渐变色 */
const getGradient = (id: number) => {
  const gradients = [
    'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
    'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
    'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)',
    'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
    'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)',
  ];
  return gradients[id % gradients.length];
};
</script>

<template>
  <Card class="agent-card" :bordered="false">
    <!-- 顶部装饰条 -->
    <div class="card-header-bar" :style="{ background: getGradient(item.id) }" />

    <div class="card-content">
      <!-- 头像区域 -->
      <div class="agent-avatar-wrapper">
        <Avatar
          v-if="item.avatar"
          :src="item.avatar"
          :size="72"
          class="agent-avatar"
        />
        <Avatar
          v-else
          :size="72"
          class="agent-avatar default-avatar"
          :style="{ background: getGradient(item.id) }"
        >
          {{ getAvatarText(item.name) }}
        </Avatar>
        <!-- 在线状态指示器 -->
        <span class="status-dot" />
      </div>

      <!-- 名称 -->
      <h3 class="agent-name" :title="item.name">
        <RobotOutlined class="name-icon" />
        {{ item.name }}
      </h3>

      <!-- 描述 -->
      <p class="agent-description" :title="item.description">
        {{ item.description || '这是一个智能助手' }}
      </p>

      <!-- 能力标签 -->
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
        <Tag v-if="!item.kbId && !item.tools?.length" color="default" class="feature-tag">
          <MessageOutlined />
          <span>基础对话</span>
        </Tag>
      </div>
    </div>

    <!-- 操作按钮 -->
    <template #actions>
      <Tooltip title="查看详情">
        <span class="action-item action-view" @click.stop="$emit('view', item)">
          <EyeOutlined />
        </span>
      </Tooltip>
      <Tooltip title="编辑配置">
        <span class="action-item action-edit" @click.stop="$emit('edit', item)">
          <EditOutlined />
        </span>
      </Tooltip>
      <Tooltip title="开始对话">
        <span class="action-item action-chat" @click.stop="$emit('chat', item)">
          <MessageOutlined />
        </span>
      </Tooltip>
      <Tooltip title="删除">
        <span class="action-item action-delete" @click.stop="$emit('remove', item)">
          <DeleteOutlined />
        </span>
      </Tooltip>
    </template>
  </Card>
</template>

<style lang="less" scoped>
.agent-card {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  cursor: pointer;
  background: #fff;
  border-radius: 16px;
  box-shadow:
    0 2px 8px rgb(0 0 0 / 6%),
    0 0 1px rgb(0 0 0 / 8%);
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    box-shadow:
      0 12px 28px rgb(0 0 0 / 12%),
      0 0 1px rgb(0 0 0 / 8%);
    transform: translateY(-6px);

    .card-header-bar {
      height: 6px;
    }

    .agent-avatar {
      box-shadow: 0 8px 20px rgb(0 0 0 / 15%);
      transform: scale(1.08);
    }

    .status-dot {
      transform: scale(1.2);
    }
  }

  :deep(.ant-card-body) {
    flex: 1;
    padding: 0;
  }
}

.card-header-bar {
  height: 4px;
  transition: height 0.3s ease;
}

.card-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  padding: 24px 20px 16px;
  text-align: center;
}

.agent-avatar-wrapper {
  position: relative;
  margin-bottom: 16px;
}

.agent-avatar {
  box-shadow: 0 4px 12px rgb(0 0 0 / 12%);
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);

  &.default-avatar {
    font-size: 28px;
    font-weight: 600;
    color: #fff;
  }
}

.status-dot {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 14px;
  height: 14px;
  background: #52c41a;
  border: 3px solid #fff;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgb(82 196 26 / 40%);
  transition: transform 0.3s ease;
}

.agent-name {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin: 0 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.4;
  color: #1a1a1a;
  white-space: nowrap;

  .name-icon {
    font-size: 16px;
    color: #722ed1;
  }
}

.agent-description {
  display: -webkit-box;
  flex: 1;
  width: 100%;
  min-height: 40px;
  margin: 0 0 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 13px;
  line-height: 1.6;
  color: #666;
  -webkit-box-orient: vertical;
}

.agent-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;

  .feature-tag {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    padding: 4px 12px;
    margin: 0;
    font-size: 12px;
    border: none;
    border-radius: 20px;

    .anticon {
      font-size: 12px;
    }
  }
}

:deep(.ant-card-actions) {
  display: flex;
  padding: 0;
  margin: 0;
  background: #fafafa;
  border-top: 1px solid #f0f0f0;
  border-radius: 0 0 16px 16px;

  > li {
    flex: 1;
    margin: 0 !important;
    border-right: 1px solid #f0f0f0;

    &:last-child {
      border-right: none;
    }

    .action-item {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 44px;
      font-size: 16px;
      cursor: pointer;
      transition: all 0.25s ease;

      &.action-view {
        color: #52c41a;

        &:hover {
          color: #fff;
          background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%);
        }
      }

      &.action-edit {
        color: #1890ff;

        &:hover {
          color: #fff;
          background: linear-gradient(135deg, #1890ff 0%, #40a9ff 100%);
        }
      }

      &.action-chat {
        color: #722ed1;

        &:hover {
          color: #fff;
          background: linear-gradient(135deg, #722ed1 0%, #9254de 100%);
        }
      }

      &.action-delete {
        color: #ff4d4f;

        &:hover {
          color: #fff;
          background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%);
        }
      }
    }
  }
}
</style>
