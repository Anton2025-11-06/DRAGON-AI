<script setup lang="ts">
import { computed, ref } from 'vue';

import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Dropdown,
  Input,
  Menu,
  Modal,
  Spin,
  Typography,
} from 'ant-design-vue';

// ==================== 类型定义 ====================
interface ConversationItem {
  key: string;
  label: string;
}

interface ConversationGroup {
  title: string;
  items: ConversationItem[];
}

// ==================== Props ====================
const props = defineProps<{
  activeKey: string;
  groupedConversations: ConversationGroup[];
  hasMore?: boolean;
  isEmpty?: boolean;
  loading?: boolean;
  total?: number;
}>();

// ==================== Emits ====================
const emit = defineEmits<{
  change: [key: string];
  create: [];
  delete: [key: string];
  loadMore: [];
  rename: [key: string, name: string];
}>();

// ==================== 编辑状态 ====================
const editingKey = ref<null | string>(null);
const editingName = ref('');
const loadingMore = ref(false);

/** 计算会话总数 */
const conversationCount = computed(() => {
  return props.groupedConversations.reduce(
    (sum, group) => sum + (group.items?.length || 0),
    0,
  );
});

function startEdit(item: ConversationItem) {
  editingKey.value = item.key;
  editingName.value = item.label;
}

function cancelEdit() {
  editingKey.value = null;
  editingName.value = '';
}

function confirmEdit(key: string) {
  if (editingName.value.trim()) {
    emit('rename', key, editingName.value.trim());
  }
  cancelEdit();
}

function handleDelete(key: string) {
  Modal.confirm({
    title: '确认删除',
    content: '删除后无法恢复，确定要删除这个会话吗？',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => emit('delete', key),
  });
}

/** 滚动加载更多 */
function handleScroll(e: Event) {
  const target = e.target as HTMLElement;
  const { scrollTop, scrollHeight, clientHeight } = target;
  // 距离底部 50px 时触发加载
  if (
    scrollHeight - scrollTop - clientHeight < 50 &&
    props.hasMore &&
    !loadingMore.value
  ) {
    loadingMore.value = true;
    emit('loadMore');
    setTimeout(() => {
      loadingMore.value = false;
    }, 500);
  }
}
</script>

<template>
  <aside class="chat-sidebar">
    <!-- Logo -->
    <div class="sidebar-logo">
      <RobotOutlined class="logo-icon" />
      <span class="logo-text">AI 助手</span>
    </div>

    <!-- 新建对话按钮 -->
    <div class="sidebar-new-btn">
      <Button type="primary" block @click="emit('create')">
        <PlusOutlined /> 新建对话
      </Button>
    </div>

    <!-- 分组会话列表 -->
    <div class="sidebar-content" @scroll="handleScroll">
      <Spin :spinning="loading">
        <template v-for="group in groupedConversations" :key="group.title">
          <div class="conversation-group">
            <div class="group-title">{{ group.title }}</div>

            <div class="conversation-items">
              <div
                v-for="item in group.items"
                :key="item.key"
                class="conversation-item"
                :class="{ active: item.key === activeKey }"
                @click="emit('change', item.key)"
              >
                <!-- 编辑模式 -->
                <template v-if="editingKey === item.key">
                  <Input
                    v-model:value="editingName"
                    size="small"
                    class="edit-input"
                    @click.stop
                    @press-enter="confirmEdit(item.key)"
                  />
                  <div class="edit-actions" @click.stop>
                    <Button
                      type="text"
                      size="small"
                      @click="confirmEdit(item.key)"
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
                  <span class="item-title">{{ item.label }}</span>

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
                        <Menu.Item key="rename" @click="startEdit(item)">
                          <EditOutlined /> 重命名
                        </Menu.Item>
                        <Menu.Item
                          key="delete"
                          danger
                          @click="handleDelete(item.key)"
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
        <div v-if="isEmpty && !loading" class="sidebar-empty">
          <Typography.Text type="secondary">
            暂无会话，点击上方按钮创建
          </Typography.Text>
        </div>

        <!-- 加载更多提示 -->
        <div v-if="loadingMore" class="loading-more">
          <Spin size="small" />
        </div>
      </Spin>
    </div>

    <!-- 底部记录数量 -->
    <div class="sidebar-footer">共 {{ total ?? conversationCount }} 条记录</div>
  </aside>
</template>

<style lang="less" scoped>
.chat-sidebar {
  display: flex;
  flex-direction: column;
  width: 280px;
  min-width: 280px;
  height: 100%;
  overflow: hidden;
  background: #fff;
  border-right: 1px solid var(--border-color, #e8e8e8);
  border-radius: 8px 0 0 8px;
}

.sidebar-logo {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 20px 16px;
  border-bottom: 1px solid #f0f0f0;

  .logo-icon {
    font-size: 28px;
    color: #1890ff;
  }

  .logo-text {
    font-size: 18px;
    font-weight: 600;
    color: #333;
  }
}

.sidebar-new-btn {
  padding: 16px;
}

.sidebar-content {
  flex: 1;
  padding: 8px;
  overflow: hidden auto;

  // 隐藏滚动条但保留滚动功能
  scrollbar-width: none; // Firefox
  -ms-overflow-style: none; // IE/Edge

  &::-webkit-scrollbar {
    display: none; // Chrome/Safari
  }
}

.conversation-group {
  margin-bottom: 16px;

  .group-title {
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #999;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
}

.conversation-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conversation-item {
  position: relative;
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 12px 14px;
  cursor: pointer;
  border-radius: 10px;
  transition: all 0.15s ease;

  &:hover {
    background: #f5f5f5;

    .item-more {
      opacity: 1;
    }
  }

  &.active {
    background: linear-gradient(
      135deg,
      rgb(24 144 255 / 10%) 0%,
      rgb(102 126 234 / 10%) 100%
    );

    .item-icon {
      color: #1890ff;
    }

    .item-title {
      font-weight: 500;
      color: #1890ff;
    }
  }

  .item-icon {
    flex-shrink: 0;
    font-size: 16px;
    color: #999;
  }

  .item-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 14px;
    color: #333;
    white-space: nowrap;
  }

  .item-more {
    opacity: 0;
    transition: opacity 0.15s;

    .more-dots {
      font-size: 16px;
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
    gap: 4px;
  }
}

.sidebar-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
}

.loading-more {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
}

.sidebar-footer {
  padding: 12px 16px;
  font-size: 12px;
  color: #666;
  text-align: center;
  background: #fff;
  border-top: 1px solid #f0f0f0;
  box-shadow: 0 -2px 8px rgb(0 0 0 / 4%);
}
</style>
