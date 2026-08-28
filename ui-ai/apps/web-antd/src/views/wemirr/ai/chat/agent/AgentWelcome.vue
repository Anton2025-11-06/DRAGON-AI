<script setup lang="ts">
/**
 * 智能体欢迎页组件
 * 显示智能体信息和快速开始引导
 */
import { h } from 'vue';

import {
  BulbOutlined,
  MessageOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import { Avatar, Card, Col, Row, Space, Tag } from 'ant-design-vue';
import { Welcome } from 'ant-design-x-vue';

import type { AgentDetail } from './api';

// ==================== Props ====================
defineProps<{
  agent?: AgentDetail | null;
}>();

// ==================== Emits ====================
const emit = defineEmits<{
  send: [prompt: string];
}>();

// ==================== 欢迎页图标 ====================
const welcomeIcon = h(RobotOutlined, { 
  style: { 
    fontSize: '32px', 
    color: '#fff',
  } 
});

// ==================== 推荐问题 ====================
const defaultQuestions = [
  '你好，请介绍一下你自己',
  '你能帮我做什么？',
  '请帮我完成一个任务',
  '给我一些建议',
];

// ==================== 事件处理 ====================
function handleQuestionClick(question: string) {
  emit('send', question);
}
</script>

<template>
  <div class="agent-welcome-container">
    <!-- 智能体信息卡片 -->
    <div v-if="agent" class="agent-profile">
      <Avatar :size="80" :src="agent.avatar" class="profile-avatar">
        <template #icon>
          <RobotOutlined />
        </template>
      </Avatar>
      <div class="profile-info">
        <h2 class="profile-name">{{ agent.name }}</h2>
        <p class="profile-desc">{{ agent.description || '这是一个智能助手' }}</p>
      </div>
    </div>

    <!-- 无智能体时的默认欢迎 -->
    <Welcome
      v-else
      :icon="welcomeIcon"
      title="智能体对话"
      description="选择左侧的智能体开始对话"
      class="welcome-header"
    />

    <!-- 功能区域 -->
    <div class="welcome-cards">
      <Row :gutter="16">
        <Col :span="12">
          <Card class="feature-card" :bordered="false">
            <template #title>
              <Space>
                <BulbOutlined style="color: #faad14" />
                <span>快速开始</span>
              </Space>
            </template>
            <div class="card-content">
              <div 
                v-for="item in defaultQuestions" 
                :key="item" 
                class="question-item"
                @click="handleQuestionClick(item)"
              >
                <QuestionCircleOutlined class="question-icon" />
                {{ item }}
              </div>
            </div>
          </Card>
        </Col>
        <Col :span="12">
          <Card class="feature-card" :bordered="false">
            <template #title>
              <Space>
                <SettingOutlined style="color: #1890ff" />
                <span>智能体能力</span>
              </Space>
            </template>
            <div class="card-content">
              <div class="capability-item">
                <MessageOutlined />
                <div>
                  <div class="capability-title">自然对话</div>
                  <div class="capability-desc">流畅的多轮对话，理解上下文语境</div>
                </div>
              </div>
              <div class="capability-item">
                <ThunderboltOutlined />
                <div>
                  <div class="capability-title">专业能力</div>
                  <div class="capability-desc">根据设定的角色提供专业服务</div>
                </div>
              </div>
              <div class="capability-item">
                <BulbOutlined />
                <div>
                  <div class="capability-title">智能推理</div>
                  <div class="capability-desc">深度思考，给出有价值的建议</div>
                </div>
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>

    <!-- 智能体标签 -->
    <div v-if="agent" class="agent-tags">
      <Tag color="purple">智能体对话</Tag>
      <Tag v-if="agent.kbId" color="green">关联知识库</Tag>
      <Tag v-if="agent.tools?.length" color="blue">{{ agent.tools.length }} 个工具</Tag>
    </div>
  </div>
</template>

<style lang="less" scoped>
.agent-welcome-container {
  display: flex;
  flex: 1;
  flex-direction: column;
  width: 100%;
  max-width: 900px;
  padding: 40px;
  margin: 0 auto;
  overflow-y: auto;
}

.agent-profile {
  display: flex;
  gap: 20px;
  align-items: center;
  padding: 24px;
  margin-bottom: 32px;
  background: linear-gradient(135deg, rgb(114 46 209 / 8%) 0%, rgb(24 144 255 / 8%) 100%);
  border-radius: 16px;

  .profile-avatar {
    flex-shrink: 0;
    background: linear-gradient(135deg, #722ed1 0%, #1890ff 100%);
    box-shadow: 0 4px 12px rgb(114 46 209 / 30%);
  }

  .profile-info {
    flex: 1;
  }

  .profile-name {
    margin: 0 0 8px;
    font-size: 24px;
    font-weight: 600;
    color: #333;
  }

  .profile-desc {
    margin: 0;
    font-size: 14px;
    line-height: 1.6;
    color: #666;
  }
}

.welcome-header {
  margin-bottom: 32px;
}

.welcome-cards {
  margin-bottom: 24px;

  .feature-card {
    height: 100%;
    background: var(--hover-color, #fafafa);
    border-radius: 12px;

    :deep(.ant-card-head) {
      padding: 16px 20px 0;
      border-bottom: none;
    }

    :deep(.ant-card-body) {
      padding: 12px 20px 20px;
    }
  }

  .card-content {
    .question-item {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 10px 12px;
      margin-bottom: 8px;
      color: #722ed1;
      cursor: pointer;
      border-radius: 6px;
      transition: background 0.2s;

      .question-icon {
        color: #999;
      }

      &:hover {
        background: rgb(114 46 209 / 10%);

        .question-icon {
          color: #722ed1;
        }
      }

      &:last-child {
        margin-bottom: 0;
      }
    }

    .capability-item {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      padding: 10px 0;
      border-bottom: 1px solid var(--border-color, #f0f0f0);

      &:last-child {
        border-bottom: none;
      }

      .anticon {
        margin-top: 2px;
        font-size: 18px;
        color: #722ed1;
      }

      .capability-title {
        margin-bottom: 4px;
        font-weight: 500;
        color: var(--text-color, #333);
      }

      .capability-desc {
        font-size: 12px;
        color: var(--text-color-secondary, #999);
      }
    }
  }
}

.agent-tags {
  display: flex;
  gap: 8px;
  justify-content: center;
}
</style>
