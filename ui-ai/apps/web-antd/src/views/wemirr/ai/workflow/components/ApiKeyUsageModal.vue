<script setup lang="ts">
/**
 * API Key 用法说明弹窗。
 *
 * 创建成功后与列表「查看」共用同一份：curl 模板写两遍必然漂移，这里只留一个来源。
 * 两步调用各自成块、各自可复制，事件类型收成标签，不再挤在一段注释里。
 */
import { computed, ref } from 'vue';

import { CheckOutlined, CopyOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

interface Props {
  /** 要展示的 API Key（明文） */
  apiKey: string;
  open: boolean;
  workflowId: number | string;
}

const props = defineProps<Props>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

const apiBaseUrl = `${window.location.origin}/api/workflow`;

/** 两步调用：编号 + 说明 + 可直接粘贴的命令 */
const steps = computed(() => [
  {
    code: [
      `curl -X POST ${apiBaseUrl}/workflow-executions/workflows/${props.workflowId}/execute-async \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"inputs": {"query": "你好"}}'`,
    ].join('\n'),
    desc: '返回 executionId，请求头携带 API Key',
    title: '异步执行工作流',
  },
  {
    code: `curl -N ${apiBaseUrl}/workflow-executions/<executionId>/subscribe`,
    desc: 'SSE 长连接，网关白名单放行，无需携带请求头',
    title: '订阅执行事件流',
  },
]);

const eventTypes = [
  'node.started',
  'node.completed',
  'node.delta',
  'node.failed',
  'workflow.paused',
  'workflow.completed',
  'workflow.failed',
  'workflow.cancelled',
];

/** 刚复制过的块，用于把图标换成对勾给一眼反馈 */
const copiedIndex = ref<null | number>(null);

function copyText(text: string, index: null | number = null) {
  navigator.clipboard
    .writeText(text)
    .then(() => {
      message.success('已复制到剪贴板');
      if (index !== null) {
        copiedIndex.value = index;
        setTimeout(() => {
          copiedIndex.value = null;
        }, 1500);
      }
    })
    .catch(() => {
      message.error('复制失败，请手动复制');
    });
}

function handleClose(open: boolean) {
  emit('update:open', open);
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="720"
    title="API Key 用法说明"
    @update:open="handleClose"
  >
    <!-- Key 本体：整行放得下就不换行，复制按钮固定在右侧 -->
    <div class="usage-key">
      <span class="usage-key-label">API Key</span>
      <code class="usage-key-value">{{ props.apiKey }}</code>
      <a-button size="small" @click="copyText(props.apiKey)">
        <CopyOutlined /> 复制
      </a-button>
    </div>

    <div class="usage-steps">
      <div v-for="(step, index) in steps" :key="index" class="usage-step">
        <div class="step-head">
          <span class="step-no">{{ index + 1 }}</span>
          <div class="step-names">
            <div class="step-title">{{ step.title }}</div>
            <div class="step-desc">{{ step.desc }}</div>
          </div>
          <a class="step-copy" @click="copyText(step.code, index)">
            <CheckOutlined v-if="copiedIndex === index" />
            <CopyOutlined v-else />
            复制
          </a>
        </div>
        <pre class="step-code"><code>{{ step.code }}</code></pre>
      </div>
    </div>

    <div class="usage-events">
      <div class="events-title">事件类型</div>
      <div class="events-tags">
        <a-tag v-for="item in eventTypes" :key="item">{{ item }}</a-tag>
      </div>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
.usage-key {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 16px;
  background: #f5f7fa;
  border: 1px solid #e8e8e8;
  border-radius: 8px;

  .usage-key-label {
    flex-shrink: 0;
    font-size: 12px;
    color: #8c8c8c;
  }

  .usage-key-value {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 13px;
    color: #17415e;
  }
}

.usage-steps {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-step {
  .step-head {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    margin-bottom: 6px;

    .step-no {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      margin-top: 1px;
      font-size: 12px;
      color: #fff;
      background: #1890ff;
      border-radius: 50%;
    }

    .step-names {
      flex: 1;
      min-width: 0;
    }

    .step-title {
      font-size: 13px;
      font-weight: 600;
      line-height: 20px;
      color: #262626;
    }

    .step-desc {
      font-size: 12px;
      color: #8c8c8c;
    }

    .step-copy {
      flex-shrink: 0;
      font-size: 12px;
      color: #8c8c8c;

      &:hover {
        color: #1890ff;
      }
    }
  }

  .step-code {
    padding: 10px 12px;
    margin: 0;
    overflow-x: auto;
    // 这块定的是深底白字：一旦被全局 pre 样式盖成浅底，白字就直接隐形了，故显式钉住底色
    background: #1e1e1e !important;
    border-radius: 8px;

    code {
      padding: 0;
      font-size: 12px;
      line-height: 20px;
      color: #fff;
      // 内联 code 只要带底色，就会在文字背后垫出一条浅色高亮（不是整块），
      // 叠上白字就是看到的“白底”，必须压成透明
      background: none !important;
      white-space: pre;
    }
  }
}

.usage-events {
  padding-top: 14px;
  margin-top: 16px;
  border-top: 1px dashed #e8e8e8;

  .events-title {
    margin-bottom: 8px;
    font-size: 12px;
    color: #8c8c8c;
  }

  .events-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 0;
  }
}
</style>
