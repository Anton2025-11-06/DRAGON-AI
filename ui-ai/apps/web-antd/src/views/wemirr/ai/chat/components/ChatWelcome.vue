<script setup lang="ts">
import { h } from 'vue';

import {
  CommentOutlined,
  FireOutlined,
  GlobalOutlined,
  HeartOutlined,
  ReadOutlined,
  RobotOutlined,
  SmileOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import { Card, Col, Row, Space } from 'ant-design-vue';
import { Prompts, Welcome } from 'ant-design-x-vue';
import type { PromptsProps } from 'ant-design-x-vue';

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

// ==================== 热门话题 ====================
const hotTopics = [
  '帮我写一篇关于人工智能的文章',
  '如何学习编程？',
  '解释一下量子计算的原理',
  '推荐一些提高工作效率的方法',
  '帮我分析这段代码的问题',
];

// ==================== 快捷提示 ====================
const promptItems: PromptsProps['items'] = [
  { key: 'code', icon: h(ThunderboltOutlined), label: '代码助手' },
  { key: 'write', icon: h(ReadOutlined), label: '写作帮手' },
  { key: 'translate', icon: h(GlobalOutlined), label: '翻译助手' },
  { key: 'chat', icon: h(CommentOutlined), label: '闲聊模式' },
];

// ==================== 事件处理 ====================
function handleTopicClick(topic: string) {
  emit('send', topic);
}

function handlePromptClick(info: { data: { key: string } }) {
  const prompts: Record<string, string> = {
    code: '请帮我解决一个编程问题',
    write: '请帮我写一篇文章',
    translate: '请帮我翻译以下内容',
    chat: '你好，今天想聊点什么？',
  };
  const prompt = prompts[info.data.key] || '';
  if (prompt) {
    emit('send', prompt);
  }
}
</script>

<template>
  <div class="welcome-container">
    <!-- 欢迎头部 -->
    <Welcome
      :icon="welcomeIcon"
      title="你好，我是 AI 助手"
      description="基于大语言模型，为你提供智能对话服务"
      class="welcome-header"
    />

    <!-- 功能卡片 -->
    <div class="welcome-cards">
      <Row :gutter="16">
        <Col :span="12">
          <Card class="feature-card" :bordered="false">
            <template #title>
              <Space>
                <FireOutlined style="color: #ff4d4f" />
                <span>热门话题</span>
              </Space>
            </template>
            <div class="card-content">
              <div 
                v-for="item in hotTopics" 
                :key="item" 
                class="topic-item"
                @click="handleTopicClick(item)"
              >
                {{ item }}
              </div>
            </div>
          </Card>
        </Col>
        <Col :span="12">
          <Card class="feature-card" :bordered="false">
            <template #title>
              <Space>
                <ReadOutlined style="color: #1890ff" />
                <span>使用指南</span>
              </Space>
            </template>
            <div class="card-content">
              <div class="guide-item">
                <HeartOutlined />
                <div>
                  <div class="guide-title">意图理解</div>
                  <div class="guide-desc">AI 理解你的需求并提供解决方案</div>
                </div>
              </div>
              <div class="guide-item">
                <SmileOutlined />
                <div>
                  <div class="guide-title">角色扮演</div>
                  <div class="guide-desc">AI 可以扮演不同角色与你对话</div>
                </div>
              </div>
              <div class="guide-item">
                <CommentOutlined />
                <div>
                  <div class="guide-title">智能对话</div>
                  <div class="guide-desc">自然流畅的多轮对话体验</div>
                </div>
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>

    <!-- 快捷提示 -->
    <div class="welcome-prompts">
      <Prompts :items="promptItems" @item-click="handlePromptClick" />
    </div>
  </div>
</template>

<style lang="less" scoped>
.welcome-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 40px;
  margin: 0 auto;
  width: 100%;

  .welcome-header {
    margin-bottom: 32px;
  }

  .welcome-cards {
    margin-bottom: 32px;

    .feature-card {
      height: 100%;
      background: var(--hover-color, #fafafa);
      border-radius: 12px;

      :deep(.ant-card-head) {
        border-bottom: none;
        padding: 16px 20px 0;
      }

      :deep(.ant-card-body) {
        padding: 12px 20px 20px;
      }
    }

    .card-content {
      .topic-item {
        padding: 8px 12px;
        margin-bottom: 8px;
        color: #1890ff;
        cursor: pointer;
        border-radius: 6px;
        transition: background 0.2s;

        &:hover {
          background: rgba(24, 144, 255, 0.1);
        }

        &:last-child {
          margin-bottom: 0;
        }
      }

      .guide-item {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 10px 0;
        border-bottom: 1px solid var(--border-color, #f0f0f0);

        &:last-child {
          border-bottom: none;
        }

        .anticon {
          font-size: 18px;
          color: #1890ff;
          margin-top: 2px;
        }

        .guide-title {
          font-weight: 500;
          color: var(--text-color, #333);
          margin-bottom: 4px;
        }

        .guide-desc {
          font-size: 12px;
          color: var(--text-color-secondary, #999);
        }
      }
    }
  }

  .welcome-prompts {
    margin-top: auto;
  }
}
</style>
