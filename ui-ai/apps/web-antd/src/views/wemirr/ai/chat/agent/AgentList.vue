<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { RobotOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { message, Spin } from 'ant-design-vue';

import type { ChatAgent } from '../api';

import { getAgentPage } from './api';

const emit = defineEmits<{
  select: [agent: ChatAgent];
}>();

const loading = ref(false);
const searchText = ref('');
const agentData = ref<ChatAgent[]>([]);
const selectedId = ref<number | null>(null);

// 过滤后的智能体列表
const filteredAgents = computed(() => {
  if (!searchText.value.trim()) {
    return agentData.value;
  }
  const keyword = searchText.value.toLowerCase().trim();
  return agentData.value.filter(
    (agent) =>
      agent.name.toLowerCase().includes(keyword) ||
      (agent.description && agent.description.toLowerCase().includes(keyword)),
  );
});

// 获取智能体列表
const getAgents = async () => {
  loading.value = true;
  try {
    const res = await getAgentPage({ current: 1, size: 1000 });
    agentData.value = res.records || [];
    return agentData.value;
  } catch {
    message.error('获取智能体列表失败');
    return [];
  } finally {
    loading.value = false;
  }
};

// 选择智能体
const handleSelectAgent = (agent: ChatAgent) => {
  selectedId.value = agent.id;
  emit('select', agent);
};

// 清空搜索
const handleClearSearch = () => {
  searchText.value = '';
};

onMounted(async () => {
  const list = await getAgents();
  if (list.length > 0) {
    handleSelectAgent(list[0]!);
  }
});

defineExpose({
  selectedId,
  getAgents,
});
</script>

<template>
  <aside class="agent-sidebar">
    <!-- Logo -->
    <div class="sidebar-logo">
      <RobotOutlined class="logo-icon" />
      <span class="logo-text">智能体</span>
    </div>

    <!-- 搜索框 -->
    <div class="sidebar-search">
      <a-input-search
        v-model:value="searchText"
        placeholder="搜索智能体..."
        allow-clear
        @clear="handleClearSearch"
      >
        <template #prefix>
          <SearchOutlined class="text-gray-400" />
        </template>
      </a-input-search>
    </div>

    <!-- 智能体列表 -->
    <div class="sidebar-content">
      <Spin :spinning="loading">
        <div
          v-for="agent in filteredAgents"
          :key="agent.id"
          class="agent-item"
          :class="{ active: selectedId === agent.id }"
          @click="handleSelectAgent(agent)"
        >
          <RobotOutlined class="item-icon" />
          <div class="item-info">
            <span class="item-name">{{ agent.name }}</span>
            <span v-if="agent.description" class="item-desc">
              {{ agent.description }}
            </span>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-if="filteredAgents.length === 0 && !loading" class="sidebar-empty">
          <RobotOutlined class="empty-icon" />
          <p v-if="searchText.trim()">未找到"{{ searchText }}"</p>
          <p v-else>暂无智能体</p>
        </div>
      </Spin>
    </div>

    <!-- 底部记录数量 -->
    <div class="sidebar-footer">共 {{ filteredAgents.length }} 个智能体</div>
  </aside>
</template>

<style lang="less" scoped>
.agent-sidebar {
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
    color: #722ed1;
  }

  .logo-text {
    font-size: 18px;
    font-weight: 600;
    color: #333;
  }
}

.sidebar-search {
  padding: 16px;
}

.sidebar-content {
  flex: 1;
  padding: 0 8px 8px;
  overflow: hidden auto;

  // 隐藏滚动条但保留滚动功能
  scrollbar-width: none;
  -ms-overflow-style: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.agent-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 14px;
  cursor: pointer;
  border-radius: 10px;
  transition: all 0.15s ease;

  &:hover {
    background: #f5f5f5;
  }

  &.active {
    background: linear-gradient(
      135deg,
      rgb(114 46 209 / 10%) 0%,
      rgb(24 144 255 / 10%) 100%
    );

    .item-icon {
      color: #722ed1;
    }

    .item-name {
      font-weight: 500;
      color: #722ed1;
    }
  }

  .item-icon {
    flex-shrink: 0;
    margin-top: 2px;
    font-size: 18px;
    color: #999;
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

.sidebar-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: #999;
  text-align: center;

  .empty-icon {
    margin-bottom: 12px;
    font-size: 48px;
    color: #d9d9d9;
  }

  p {
    margin: 0;
    font-size: 14px;
  }
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
