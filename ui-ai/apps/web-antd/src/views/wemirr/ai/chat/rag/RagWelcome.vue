<script setup lang="ts">
import { h } from 'vue';

import {
  BookOutlined,
  BulbOutlined,
  FileSearchOutlined,
  QuestionCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { Card, Col, Row, Space } from 'ant-design-vue';
import { Welcome } from 'ant-design-x-vue';

import type { KnowledgeBase } from './api';

// ==================== Props ====================
defineProps<{
  knowledgeBase?: KnowledgeBase | null;
}>();

// ==================== Emits ====================
const emit = defineEmits<{
  send: [prompt: string];
}>();

// ==================== 欢迎页图标 ====================
const welcomeIcon = h(BookOutlined, {
  style: {
    fontSize: '32px',
    color: '#fff',
  }
});

// ==================== 推荐问题 ====================
const suggestedQuestions = [
  '帮我总结一下这个知识库的主要内容',
  '这个知识库中有哪些关键概念？',
  '根据知识库内容，解答我的问题',
  '帮我查找知识库中关于某个主题的信息',
];

// ==================== 事件处理 ====================
function handleQuestionClick(question: string) {
  emit('send', question);
}
</script>

<template>
  <div class="rag-welcome-container">
    <!-- 欢迎头部 -->
    <Welcome
      :icon="welcomeIcon"
      :title="knowledgeBase ? `知识库：${knowledgeBase.name}` : '知识库问答'"
      :description="knowledgeBase?.description || '基于知识库的智能问答，为你提供精准的信息检索服务'"
      class="welcome-header"
    />

    <!-- 功能说明 -->
    <div class="welcome-cards">
      <Row :gutter="16">
        <Col :span="12">
          <Card class="feature-card" :bordered="false">
            <template #title>
              <Space>
                <BulbOutlined style="color: #faad14" />
                <span>推荐问题</span>
              </Space>
            </template>
            <div class="card-content">
              <div
                v-for="item in suggestedQuestions"
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
                <FileSearchOutlined style="color: #1890ff" />
                <span>使用指南</span>
              </Space>
            </template>
            <div class="card-content">
              <div class="guide-item">
                <SearchOutlined />
                <div>
                  <div class="guide-title">智能检索</div>
                  <div class="guide-desc">基于语义理解，从知识库中精准检索相关内容</div>
                </div>
              </div>
              <div class="guide-item">
                <BookOutlined />
                <div>
                  <div class="guide-title">知识问答</div>
                  <div class="guide-desc">结合检索结果，生成准确、有依据的回答</div>
                </div>
              </div>
              <div class="guide-item">
                <BulbOutlined />
                <div>
                  <div class="guide-title">上下文关联</div>
                  <div class="guide-desc">支持多轮对话，理解上下文进行深入交流</div>
                </div>
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>

    <!-- 知识库信息 -->
    <div v-if="knowledgeBase" class="kb-info">
      <span class="kb-info-item">
        <strong>TopK:</strong> {{ knowledgeBase.topK || 5 }}
      </span>
      <span class="kb-info-item">
        <strong>最小相似度:</strong> {{ knowledgeBase.scoreThreshold ?? 0.7 }}
      </span>
    </div>
  </div>
</template>

<style lang="less" scoped>
.rag-welcome-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 40px;
  margin: 0 auto;
  width: 100%;
  overflow-y: auto;

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
        border-bottom: none;
        padding: 16px 20px 0;
      }

      :deep(.ant-card-body) {
        padding: 12px 20px 20px;
      }
    }

    .card-content {
      .question-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        margin-bottom: 8px;
        color: #1890ff;
        cursor: pointer;
        border-radius: 6px;
        transition: background 0.2s;

        .question-icon {
          color: #999;
        }

        &:hover {
          background: rgba(24, 144, 255, 0.1);

          .question-icon {
            color: #1890ff;
          }
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

  .kb-info {
    display: flex;
    justify-content: center;
    gap: 24px;
    padding: 12px;
    background: #f5f5f5;
    border-radius: 8px;
    font-size: 13px;
    color: #666;

    .kb-info-item {
      strong {
        color: #333;
        margin-right: 4px;
      }
    }
  }
}
</style>
