<script setup lang="ts">
/**
 * AI 通用侧边栏组件
 * 支持两种模式：
 * 1. conversation - 会话列表模式（带分组、新建、删除、重命名）
 * 2. list - 普通列表模式（带搜索、选择）
 */
import { computed, ref } from 'vue';

import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
  SearchOutlined,
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

/** 侧边栏项目 */
export interface SidebarItem {
  key: number | string;
  label: string;
  description?: string;
}

/** 侧边栏分组 */
export interface SidebarGroup {
  title: string;
  items: SidebarItem[];
}

// ==================== Props ====================

const props = withDefaults(
  defineProps<{
    /** 当前选中项的 key */
    activeKey?: number | string;
    /** 是否显示新建按钮 */
    createable?: boolean;
    /** 新建按钮文字 */
    createText?: string;
    /** 是否可删除 */
    deleteable?: boolean;
    /** 底部文字 */
    footerText?: string;
    /** 分组数据（conversation 模式使用） */
    groups?: SidebarGroup[];
    /** 是否有更多数据 */
    hasMore?: boolean;
    /** 图标颜色 */
    iconColor?: string;
    /** 列表数据（list 模式使用） */
    items?: SidebarItem[];
    /** 加载状态 */
    loading?: boolean;
    /** Logo 图标类型 */
    logoIcon?: 'book' | 'robot';
    /** Logo 文字 */
    logoText?: string;
    /** 模式：conversation-会话列表，list-普通列表 */
    mode?: 'conversation' | 'list';
    /** 是否可重命名 */
    renameable?: boolean;
    /** 是否显示搜索框 */
    searchable?: boolean;
    /** 搜索框占位符 */
    searchPlaceholder?: string;
    /** 是否显示描述 */
    showDescription?: boolean;
    /** 总数 */
    total?: number;
  }>(),
  {
    mode: 'conversation',
    logoIcon: 'robot',
    logoText: 'AI 助手',
    createable: true,
    createText: '新建对话',
    searchable: false,
    searchPlaceholder: '搜索...',
    renameable: true,
    deleteable: true,
    showDescription: false,
    iconColor: '#1890ff',
    groups: () => [],
    items: () => [],
    activeKey: undefined,
    footerText: undefined,
    total: undefined,
    hasMore: false,
    loading: false,
  },
);

// ==================== Emits ====================

const emit = defineEmits<{
  change: [key: number | string];
  create: [];
  delete: [key: number | string];
  loadMore: [];
  rename: [key: number | string, name: string];
  search: [keyword: string];
}>();

// ==================== 状态 ====================

const searchText = ref('');
const editingKey = ref<null | number | string>(null);
const editingName = ref('');
const loadingMore = ref(false);

// ==================== 计算属性 ====================

/** 过滤后的列表（list 模式） */
const filteredItems = computed(() => {
  if (!searchText.value.trim()) {
    return props.items;
  }
  const keyword = searchText.value.toLowerCase().trim();
  return props.items.filter(
    (item) =>
      item.label.toLowerCase().includes(keyword) ||
      (item.description && item.description.toLowerCase().includes(keyword)),
  );
});

/** 是否为空 */
const isEmpty = computed(() => {
  if (props.mode === 'conversation') {
    return props.groups.length === 0;
  }
  return filteredItems.value.length === 0;
});

/** 显示的总数 */
const displayTotal = computed(() => {
  if (props.total !== undefined) {
    return props.total;
  }
  if (props.mode === 'conversation') {
    return props.groups.reduce((sum, g) => sum + g.items.length, 0);
  }
  return filteredItems.value.length;
});

// ==================== 编辑相关 ====================

function startEdit(item: SidebarItem) {
  editingKey.value = item.key;
  editingName.value = item.label;
}

function cancelEdit() {
  editingKey.value = null;
  editingName.value = '';
}

function confirmEdit(key: number | string) {
  if (editingName.value.trim()) {
    emit('rename', key, editingName.value.trim());
  }
  cancelEdit();
}

function handleDelete(key: number | string) {
  Modal.confirm({
    title: '确认删除',
    content: '删除后无法恢复，确定要删除吗？',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => emit('delete', key),
  });
}

// ==================== 滚动加载 ====================

function handleScroll(e: Event) {
  const target = e.target as HTMLElement;
  const { scrollTop, scrollHeight, clientHeight } = target;
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

// ==================== 搜索 ====================

function handleSearch() {
  emit('search', searchText.value);
}

function handleClearSearch() {
  searchText.value = '';
  emit('search', '');
}
</script>

<template>
  <aside class="ai-sidebar">
    <!-- Logo -->
    <div class="sidebar-logo">
      <RobotOutlined class="logo-icon" :style="{ color: iconColor }" />
      <span class="logo-text">{{ logoText }}</span>
    </div>

    <!-- 新建按钮（conversation 模式） -->
    <div v-if="createable && mode === 'conversation'" class="sidebar-action">
      <Button type="primary" block @click="emit('create')">
        <PlusOutlined /> {{ createText }}
      </Button>
    </div>

    <!-- 搜索框（list 模式） -->
    <div v-if="searchable" class="sidebar-search">
      <a-input-search
        v-model:value="searchText"
        :placeholder="searchPlaceholder"
        allow-clear
        @search="handleSearch"
        @clear="handleClearSearch"
      >
        <template #prefix>
          <SearchOutlined class="text-gray-400" />
        </template>
      </a-input-search>
    </div>

    <!-- 内容区域 -->
    <div class="sidebar-content" @scroll="handleScroll">
      <Spin :spinning="loading">
        <!-- conversation 模式：分组列表 -->
        <template v-if="mode === 'conversation'">
          <template v-for="group in groups" :key="group.title">
            <div class="conversation-group">
              <div class="group-title">{{ group.title }}</div>
              <div class="conversation-items">
                <div
                  v-for="item in group.items"
                  :key="item.key"
                  class="sidebar-item"
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

                    <Dropdown
                      v-if="renameable || deleteable"
                      :trigger="['click']"
                      @click.stop
                    >
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
                          <Menu.Item
                            v-if="renameable"
                            key="rename"
                            @click="startEdit(item)"
                          >
                            <EditOutlined /> 重命名
                          </Menu.Item>
                          <Menu.Item
                            v-if="deleteable"
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
        </template>

        <!-- list 模式：普通列表 -->
        <template v-else>
          <div
            v-for="item in filteredItems"
            :key="item.key"
            class="sidebar-item list-item"
            :class="{ active: item.key === activeKey }"
            @click="emit('change', item.key)"
          >
            <RobotOutlined class="item-icon" />
            <div class="item-info">
              <span class="item-name">{{ item.label }}</span>
              <span
                v-if="showDescription && item.description"
                class="item-desc"
              >
                {{ item.description }}
              </span>
            </div>
          </div>
        </template>

        <!-- 空状态 -->
        <div v-if="isEmpty && !loading" class="sidebar-empty">
          <RobotOutlined class="empty-icon" />
          <Typography.Text type="secondary">
            <template v-if="searchText.trim()">
              未找到"{{ searchText }}"
            </template>
            <template v-else-if="mode === 'conversation'">
              暂无会话，点击上方按钮创建
            </template>
            <template v-else>暂无数据</template>
          </Typography.Text>
        </div>

        <!-- 加载更多 -->
        <div v-if="loadingMore" class="loading-more">
          <Spin size="small" />
        </div>
      </Spin>
    </div>

    <!-- 底部 -->
    <div class="sidebar-footer">
      {{ footerText || `共 ${displayTotal} 条记录` }}
    </div>
  </aside>
</template>

<style lang="less" scoped>
.ai-sidebar {
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
  }

  .logo-text {
    font-size: 18px;
    font-weight: 600;
    color: #333;
  }
}

.sidebar-action,
.sidebar-search {
  padding: 16px;
}

.sidebar-content {
  flex: 1;
  padding: 8px;
  overflow: hidden auto;
  scrollbar-width: none;
  -ms-overflow-style: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

// ==================== 分组样式 ====================
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

// ==================== 列表项样式 ====================
.sidebar-item {
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

    .item-title,
    .item-name {
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

// ==================== list 模式样式 ====================
.list-item {
  align-items: flex-start;

  .item-icon {
    margin-top: 2px;
    font-size: 18px;
  }

  .item-info {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .item-name {
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 14px;
    color: #333;
    white-space: nowrap;
  }

  .item-desc {
    display: -webkit-box;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: 2;
    font-size: 12px;
    line-height: 1.4;
    color: #999;
    -webkit-box-orient: vertical;
  }
}

// ==================== 空状态 ====================
.sidebar-empty {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;

  .empty-icon {
    font-size: 48px;
    color: #d9d9d9;
  }
}

// ==================== 加载更多 ====================
.loading-more {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
}

// ==================== 底部 ====================
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
