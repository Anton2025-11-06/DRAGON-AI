<script setup lang="ts">
/**
 * ApprovalPanel 审批决策面板
 *
 * 数据来源是后端外发的审批上下文（node.paused / workflow.paused 事件，或执行详情的
 * approvalContext 字段），面板只负责收集「结论 + 意见 + 上游数据编辑」，不直接落库：
 * 结论以 WorkflowSubmitReq 的形式交给调用方，由调用方决定走 WS 同步再提交
 * （submit-sync）还是 HTTP 异步再提交（/submit）。
 *
 * 编辑行按决策⑦以 (源节点id, 变量名, 新值) 三元组下发，后端同时回写
 * node_states[源节点].output 与运行时上下文并留 diff 作审计。
 */
import type {
  ApprovalContext,
  ApprovalDecisionReq,
  ApprovalEditReq,
} from '#/api/ai-workflow/types';

import { computed, reactive, watch } from 'vue';

import {
  CheckCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

// ==================== Props ====================

interface Props {
  /** 待审批节点的上下文列表（并行下可能多个） */
  contexts: ApprovalContext[];
  /** 提交中（禁用按钮，由调用方控制） */
  submitting?: boolean;
  /** 面板标题 */
  title?: string;
}

const props = withDefaults(defineProps<Props>(), {
  submitting: false,
  title: '人工审批',
});

// ==================== Emits ====================

const emit = defineEmits<{
  /** 收集完结论后交给调用方提交（mode 固定为 CONTINUE） */
  (e: 'submit', decisions: ApprovalDecisionReq[]): void;
  /** 放弃审批（父组件用于切走视图） */
  (e: 'cancel'): void;
}>();

// ==================== State ====================

/** 单个审批节点的编辑行（原始值快照用于判断是否改动） */
interface EditRow {
  nodeId: string;
  nodeName?: string;
  varName: string;
  /** 后端下发的当前值 */
  original: any;
  /** 编辑框里的文本（JSON 或原文） */
  text: string;
}

/** 单个审批节点的决策草稿 */
interface DecisionDraft {
  approved: boolean;
  opinion: string;
  rows: EditRow[];
}

/** 按审批节点 id 索引的草稿 */
const drafts = reactive<Record<string, DecisionDraft>>({});

/** 值 → 编辑框文本：字符串直接用原文，其余走 JSON（保持结构化值可编辑） */
function toEditText(value: any): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** 编辑框文本 → 回传值：字符串原样，其余按 JSON 解析（解析失败退回原文） */
function fromEditText(original: any, text: string): any {
  if (typeof original === 'string') return text;
  if (
    original !== undefined &&
    original !== null &&
    typeof original !== 'object'
  ) {
    const num = Number(text);
    return text.trim() !== '' && Number.isFinite(num) ? num : text;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function buildRows(context: ApprovalContext): EditRow[] {
  return (context.editableInputs || []).map((item) => ({
    nodeId: item.nodeId,
    nodeName: item.nodeName,
    varName: item.varName,
    original: item.value,
    text: toEditText(item.value),
  }));
}

// 上下文变化时重建草稿：只补齐新增节点，已有草稿保留用户已填内容
watch(
  () => props.contexts,
  (list) => {
    const ids = new Set((list || []).map((item) => item.nodeId));
    Object.keys(drafts).forEach((id) => {
      if (!ids.has(id)) delete drafts[id];
    });
    (list || []).forEach((context) => {
      if (!drafts[context.nodeId]) {
        drafts[context.nodeId] = {
          approved: true,
          opinion: '',
          rows: buildRows(context),
        };
      }
    });
  },
  { immediate: true, deep: true },
);

/** 该行是否相对后端下发值发生了变化 */
function isRowModified(row: EditRow): boolean {
  return row.text !== toEditText(row.original);
}

/**
 * 卡片列表：把「上下文 + 草稿」一次配好。
 *
 * 草稿存在 reactive 的字典里，模板直接写 `drafts[context.nodeId].xxx` 在
 * noUncheckedIndexedAccess 下永远可能是 undefined（v-if 拦不住下标访问），
 * 所以在这里先把没建好草稿的项滤掉，模板里只拿已经收窄的对象。
 */
const cards = computed(() =>
  (props.contexts || [])
    .map((context) => ({ context, draft: drafts[context.nodeId] }))
    .filter(
      (item): item is { context: ApprovalContext; draft: DecisionDraft } =>
        !!item.draft,
    ),
);

/** 同意时才需要下发编辑；不同意时下游整体取消，改值没有意义 */
function collectEdits(draft: DecisionDraft): ApprovalEditReq[] {
  const edits: ApprovalEditReq[] = [];
  draft.rows.forEach((row) => {
    if (!isRowModified(row)) return;
    edits.push({
      nodeId: row.nodeId,
      varName: row.varName,
      value: fromEditText(row.original, row.text),
    });
  });
  return edits;
}

function handleSubmit() {
  const list = props.contexts || [];
  if (list.length === 0) {
    message.warning('没有待审批的节点');
    return;
  }
  const decisions: ApprovalDecisionReq[] = [];
  for (const context of list) {
    const draft = drafts[context.nodeId];
    if (!draft) continue;
    if (draft.approved) {
      decisions.push({
        nodeId: context.nodeId,
        approved: true,
        opinion: draft.opinion.trim() || undefined,
        edits: collectEdits(draft),
      });
    } else {
      // 不同意：忽略编辑，只带结论与意见
      decisions.push({
        nodeId: context.nodeId,
        approved: false,
        opinion: draft.opinion.trim() || undefined,
      });
    }
  }
  if (decisions.length === 0) {
    message.warning('请为每个待审批节点给出结论');
    return;
  }
  emit('submit', decisions);
}
</script>

<template>
  <div class="approval-panel" data-testid="workflow-approval-panel">
    <div class="approval-panel-header">
      <span class="approval-panel-title">{{ title }}</span>
      <a-tag v-if="contexts.length > 1" color="purple" size="small">
        {{ contexts.length }} 个节点待决策
      </a-tag>
    </div>

    <div
      v-for="{ context, draft } in cards"
      :key="context.nodeId"
      class="approval-card"
    >
      <div class="approval-card-head">
        <span class="approval-node-name">
          {{ context.nodeName || context.nodeId }}
        </span>
        <a-tag v-if="context.pauseScope === 'ALL'" color="orange" size="small">
          整条流程等待
        </a-tag>
        <a-tag v-else-if="context.pauseScope" size="small">仅下游等待</a-tag>
      </div>

      <div
        v-if="context.approvers && context.approvers.length > 0"
        class="approval-meta"
      >
        审批人：{{ context.approvers.join('、') }}
      </div>
      <div v-if="context.timeoutHours" class="approval-meta">
        审批时限：{{ context.timeoutHours }} 小时
      </div>

      <a-radio-group v-model:value="draft.approved" button-style="solid">
        <a-radio-button :value="true">
          <CheckCircleOutlined /> 同意
        </a-radio-button>
        <a-radio-button :value="false">
          <CloseCircleOutlined /> 不同意
        </a-radio-button>
      </a-radio-group>

      <a-textarea
        v-model:value="draft.opinion"
        :rows="2"
        :maxlength="500"
        class="approval-opinion"
        placeholder="审批意见（可选，会记入审计）"
      />

      <div v-if="draft.approved" class="approval-edits">
        <div class="approval-edits-title">
          可编辑的上游数据（改动随结论一起回写）
        </div>
        <div
          v-for="row in draft.rows"
          :key="`${row.nodeId}.${row.varName}`"
          class="approval-edit-row"
        >
          <div class="approval-edit-label">
            <span class="approval-edit-source">
              {{ row.nodeName || row.nodeId }}.{{ row.varName }}
            </span>
            <a-tag v-if="isRowModified(row)" color="blue" size="small">
              已修改
            </a-tag>
          </div>
          <a-textarea v-model:value="row.text" :rows="2" />
        </div>
        <a-empty
          v-if="draft.rows.length === 0"
          :image="false"
          description="该节点没有可编辑的上游数据"
        />
      </div>
      <div v-else class="approval-reject-tip">
        不同意将取消该节点的下游分支，整条流以「部分完成」收尾；
        {{
          context.rejectReply
            ? `回复兜底文案：${context.rejectReply}`
            : '未配置拒绝回复文案'
        }}
      </div>
    </div>

    <div class="approval-panel-footer">
      <a-space>
        <a-button size="small" @click="emit('cancel')">暂不处理</a-button>
        <a-button
          type="primary"
          size="small"
          :loading="submitting"
          @click="handleSubmit"
        >
          提交审批结论
        </a-button>
      </a-space>
    </div>
  </div>
</template>

<style scoped lang="less">
.approval-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 8px;

  .approval-panel-header {
    display: flex;
    gap: 8px;
    align-items: center;

    .approval-panel-title {
      font-size: 14px;
      font-weight: 600;
      color: #262626;
    }
  }

  .approval-card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 6px;

    .approval-card-head {
      display: flex;
      gap: 8px;
      align-items: center;

      .approval-node-name {
        font-size: 13px;
        font-weight: 500;
        color: #262626;
      }
    }

    .approval-meta {
      font-size: 12px;
      color: #8c8c8c;
    }

    .approval-opinion {
      margin-top: 4px;
    }
  }

  .approval-edits {
    .approval-edits-title {
      margin-bottom: 6px;
      font-size: 12px;
      color: #595959;
    }

    .approval-edit-row {
      margin-bottom: 8px;

      .approval-edit-label {
        display: flex;
        gap: 6px;
        align-items: center;
        margin-bottom: 4px;
        font-size: 12px;
        color: #595959;

        .approval-edit-source {
          font-family: Consolas, Menlo, monospace;
        }
      }
    }
  }

  .approval-reject-tip {
    font-size: 12px;
    color: #fa8c16;
  }

  .approval-panel-footer {
    display: flex;
    justify-content: flex-end;
  }
}
</style>
