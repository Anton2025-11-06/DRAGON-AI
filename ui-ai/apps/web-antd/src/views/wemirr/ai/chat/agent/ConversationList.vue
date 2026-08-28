<script setup lang="ts">
import type { Conversation } from './api';

/**
 * 会话列表组件
 * 显示智能体的历史会话，支持新建、删除、重命名
 */
import { computed, ref } from 'vue';

import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Dropdown,
  Input,
  Menu,
  Spin,
  Typography,
} from 'ant-design-vue';

// ==================== Props ====================

const props = withDefaults(
  defineProps<{
    /** 当前选中的会话ID */
    activeId?: null | string;
    /** 会话列表 */
    conversations: Conversation[];
    /** 加载状态 */
    loading?: boolean;
  }>(),
  {
    loading: false,
    activeId: null,
  },
);

// ==================== Emits ====================

const emit = defineEmits<{
  create: [];
  delete: [id: string];
  rename: [id: string, title: string];
  select: [id: string];
}>();

// ==================== 状态 ====================

const editingId = ref<null | string>(null);
const editingTitle = ref('');

// ==================== 计算属性 ====================

/** 按时间分组的会话 */
const groupedConversations = computed(() => {
  const groups: { items: Conversation[]; title: string }[] = [];
  const today: Conversation[] = [];
  const yesterday: Conversation[] = [];
  const thisWeek: Conversation[] = [];
  const earlier: Conversation[] = [];

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart.getTime() - 24 * 60 * 60 * 1000);
  const weekStart = new Date(todayStart.getTime() - 7 * 24 * 60 * 60 * 1000);

  props.conversations.forEach((conv) => {
    const createTime = new Date(conv.createTime);
    if (createTime >= todayStart) {
      today.push(conv);
    } else if (createTime >= yesterdayStart) {
      yesterday.push(conv);
    } else if (createTime >= weekStart) {
      thisWeek.push(conv);
    } else {
      earlier.push(conv);
    }
  });

  if (today.length > 0) groups.push({ title: '今天', items: today });
  if (yesterday.length > 0) groups.push({ title: '昨天', items: yesterday });
  if (thisWeek.length > 0) groups.push({ title: '最近7天', items: thisWeek });
  if (earlier.length > 0) groups.push({ title: '更早', items: earlier });

  return groups;
});

// ==================== 编辑相关 ====================

function startEdit(conv: Conversation) {
  editingId.value = conv.id;
  editingTitle.value = conv.title;
}

function cancelEdit() {
  editingId.value = null;
  editingTitle.value = '';
}

function confirmEdit(id: string) {
  if (editingTitle.value.trim()) {
    emit('rename', id, editingTitle.value.trim());
  }
  cancelEdit();
}
</script>

<template>
  <aside class="conversation-sidebar">
    <!-- 头部 -->
    <div class="sidebar-header">
      <span class="header-title">历史会话</span>
      <Button type="primary" size="small" @click="emit('create')">
        <PlusOutlined /> 新建
      </Button>
    </div>

    <!-- 会话列表 -->
    <div class="sidebar-content">
      <Spin :spinning="loading">
        <template v-if="groupedConversations.length > 0">
          <div
            v-for="group in groupedConversations"
            :key="group.title"
            class="conversation-group"
          >
            <div class="group-title">{{ group.title }}</div>
            <div class="group-items">
              <div
                v-for="conv in group.items"
                :key="conv.id"
                class="conversation-item"
                :class="{ active: conv.id === activeId }"
                @click="emit('select', conv.id)"
              >
                <!-- 编辑模式 -->
                <template v-if="editingId === conv.id">
                  <Input
                    v-model:value="editingTitle"
                    size="small"
                    class="edit-input"
                    @click.stop
                    @press-enter="confirmEdit(conv.id)"
                  />
                  <div class="edit-actions" @click.stop>
                    <Button
                      type="text"
                      size="small"
                      @click="confirmEdit(conv.id)"
                    >
                      <CheckOutlined />
                    </Button>
                    <Button type="text" size="small" @click="cancelEdit">
                      <CloseOutlined />
                    </Button>
                  </div>
                </template>

                <!-- 正常模式 -->
                <template v-else>
                  <MessageOutlined class="item-icon" />
                  <span class="item-title" :title="conv.title">
                    {{ conv.title }}
                  </span>

                  <Dropdown :trigger="['click']" @click.stop>
                    <Button
                      type="text"
                      size="small"
                      class="item-more"
                      @click.stop
                    >
                      <span class="more-dots">···</span>
                    </Button>
                    <template #overlay>
                      <Menu>
                        <Menu.Item key="rename" @click="startEdit(conv)">
                          <EditOutlined /> 重命名
                        </Menu.Item>
                        <Menu.Item
                          key="delete"
                          danger
                          @click="emit('delete', conv.id)"
                        >
                          <DeleteOutlined /> 删除
                        </Menu.Item>
                      </Menu>
                    </template>
                  </Dropdown>
                </template>
              </div>
            </div>
          </div>
        </template>

        <!-- 空状态 -->
        <div v-else-if="!loading" class="sidebar-empty">
          <MessageOutlined class="empty-icon" />
          <Typography.Text type="secondary"> 暂无会话记录 </Typography.Text>
          <Button type="link" size="small" @click="emit('create')">
            开始新对话
          </Button>
        </div>
      </Spin>
    </div>

    <!-- 底部 -->
    <div class="sidebar-footer">共 {{ conversations.length }} 个会话</div>
  </aside>
</template>

<style lang="less" scoped>
.conversation-sidebar {
  display: flex;
  flex-direction: column;
  width: 240px;
  min-width: 240px;
  height: 100%;
  overflow: hidden;
  background: #fafafa;
  border-right: 1px solid #f0f0f0;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;

  .header-title {
    font-size: 14px;
    font-weight: 600;
    color: #333;
  }
}

.sidebar-content {
  flex: 1;
  padding: 8px;
  overflow: hidden auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.conversation-group {
  margin-bottom: 16px;

  .group-title {
    padding: 8px 12px 4px;
    font-size: 12px;
    font-weight: 500;
    color: #999;
  }
}

.group-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conversation-item {
  position: relative;
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  cursor: pointer;
  border-radius: 8px;
  transition: all 0.15s ease;

  &:hover {
    background: #fff;

    .item-more {
      opacity: 1;
    }
  }

  &.active {
    background: #fff;
    box-shadow: 0 1px 3px rgb(0 0 0 / 8%);

    .item-icon {
      color: #722ed1;
    }

    .item-title {
      font-weight: 500;
      color: #722ed1;
    }
  }

  .item-icon {
    flex-shrink: 0;
    font-size: 14px;
    color: #999;
  }

  .item-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 13px;
    color: #333;
    white-space: nowrap;
  }

  .item-more {
    opacity: 0;
    transition: opacity 0.15s;

    .more-dots {
      font-size: 14px;
      font-weight: bold;
      color: #999;
      letter-spacing: 1px;
    }
  }

  .edit-input {
    flex: 1;
  }

  .edit-actions {
    display: flex;
    gap: 2px;
  }
}

.sidebar-empty {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;

  .empty-icon {
    font-size: 36px;
    color: #d9d9d9;
  }
}

.sidebar-footer {
  padding: 10px 16px;
  font-size: 12px;
  color: #999;
  text-align: center;
  background: #fafafa;
  border-top: 1px solid #f0f0f0;
}
</style>
