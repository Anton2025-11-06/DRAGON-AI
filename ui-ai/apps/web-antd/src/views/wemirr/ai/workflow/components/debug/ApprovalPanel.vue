<script setup lang="ts">
/**
 * ApprovalPanel 审批决策面板
 *
 * 数据来源是执行详情的 pendingApprovals（此刻真欠着人答的那几份），面板只负责收集
 * 「结论 + 意见 + 要改的数据」，不直接落库：结论以 ApprovalDecisionReq[] 交给调用方，
 * 由调用方决定走 WS 还是 HTTP 提交——两者是同一个 submit 动作。
 *
 * 一份待办一张卡片，答复只凭 approvalToken：结论真正落到哪条执行的哪个节点是后端
 * 按令牌解析的内部事实，面板不知道也不需要知道。
 *
 * 编辑行来自待办的 editableFields（就是本次会透传给下游的那批数据），一行一项：
 * 提交时按 name 回传改后的完整值，落在哪个源节点的哪条子路径由后端自己认。
 * 例外是 valueType=APPROVER 的那一行（开始节点的「审批入参」）：它的值永远是数组，
 * 所以用标签式控件而不是文本框；后端会把改过的值抬进本轮 inputs 参与审批人身份校验。
 *
 * 文案不得出现「子执行/提交目标/终态」这类开发词：用户执行的是眼前这条流，审批就在
 * 这一侧完成，不该被要求理解嵌套执行的转发链路。
 */
import type {
  ApprovalAction,
  ApprovalDecisionReq,
  EditableApprovalField,
  PendingApproval,
} from '#/api/ai-workflow/types';

import { computed, reactive, watch } from 'vue';

import {
  CheckCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

// ==================== Props ====================

interface Props {
  /** 欠人答的审批清单（并行下可能多份） */
  approvals: PendingApproval[];
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
  /** 收集完结论交给调用方提交（一份待办一条） */
  (e: 'submit', decisions: ApprovalDecisionReq[]): void;
}>();

// ==================== State ====================

/** 一份待办里一行的编辑状态（原始值就是下发的那份，用来判断是否改过） */
interface EditRow {
  /** 后端下发的这一行（name 是回传的键） */
  field: EditableApprovalField;
  /** 编辑框里的文本（JSON 或原文；审批人行不用它） */
  text: string;
  /** 审批人行编辑值（标签式输入，提交为字符串数组） */
  list: string[];
}

/** 一份待办的决策草稿 */
interface DecisionDraft {
  action: ApprovalAction;
  opinion: string;
  rows: EditRow[];
}

/** 按节点 id 索引的草稿（同一节点刷新待办时保留用户已填内容） */
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

/** 开始节点「审批入参」的值形状标记（与后端 APPROVER_FIELD_TYPE 同字） */
const APPROVER_FIELD_TYPE = 'APPROVER';

function isApproverRow(row: EditRow): boolean {
  return row.field.valueType === APPROVER_FIELD_TYPE;
}

/**
 * 审批人值 → 标签数组：单值/数组/空值统一成字符串数组（丢空项、去重）。
 *
 * 下发值与提交值都走这一份归一，否则「未改动」会被数字与字符串的差异误判成已修改；
 * 后端比对时两边本来就都 str 化，所以这里不把工号还原成数字。
 */
function toApproverList(value: any): string[] {
  let raw: any[];
  if (value === undefined || value === null || value === '') {
    raw = [];
  } else if (Array.isArray(value)) {
    raw = value;
  } else {
    raw = [value];
  }
  const cleaned = raw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item !== '');
  return [...new Set(cleaned)];
}

function buildRows(approval: PendingApproval): EditRow[] {
  return (approval.editableFields || []).map((field) => ({
    field,
    text: toEditText(field.value),
    list: toApproverList(field.value),
  }));
}

/**
 * 这张卡片是不是在等子流程里的审批：后端的 title 只在这时与节点名不同
 *（它已把「哪条子流的哪一道」写全，面板不再自己拼）
 */
function isChildAwaiting(approval: PendingApproval): boolean {
  return approval.title !== approval.nodeLabel;
}

/** 一行的展示名：源节点 · 字段名（回传只用 name，坐标不在对外清单里） */
function rowLabel(row: EditRow): string {
  const { label, sourceNodeLabel } = row.field;
  return sourceNodeLabel
    ? `${sourceNodeLabel} · ${label || row.field.name}`
    : label || row.field.name;
}

// 待办变化时重建草稿：同一节点保留已填内容，消失的节点收掉卡片
watch(
  () => props.approvals,
  (list) => {
    const ids = new Set((list || []).map((item) => item.nodeId));
    Object.keys(drafts).forEach((id) => {
      if (!ids.has(id)) delete drafts[id];
    });
    (list || []).forEach((approval) => {
      drafts[approval.nodeId] ??= {
        action: 'APPROVE',
        opinion: '',
        rows: buildRows(approval),
      };
    });
  },
  { immediate: true, deep: true },
);

/** 该行是否相对下发值发生了变化 */
function isRowModified(row: EditRow): boolean {
  if (isApproverRow(row)) {
    return (
      JSON.stringify(row.list) !==
      JSON.stringify(toApproverList(row.field.value))
    );
  }
  return row.text !== toEditText(row.field.value);
}

/**
 * 卡片列表：把「待办 + 草稿」一次配好。
 *
 * 草稿存在 reactive 的字典里，模板直接写 `drafts[approval.nodeId].xxx` 在
 * noUncheckedIndexedAccess 下永远可能是 undefined（v-if 拦不住下标访问），
 * 所以在这里先把没建好草稿的项滤掉，模板里只拿已经收窄的对象。
 */
const cards = computed(() =>
  (props.approvals || [])
    .map((approval) => ({ approval, draft: drafts[approval.nodeId] }))
    .filter(
      (item): item is { approval: PendingApproval; draft: DecisionDraft } =>
        !!item.draft,
    ),
);

/** 改过的行 → fieldValues：键是可编辑清单里的 name，值是改后的完整值 */
function collectFieldValues(draft: DecisionDraft): Record<string, any> {
  const values: Record<string, any> = {};
  draft.rows.forEach((row) => {
    if (!isRowModified(row)) return;
    // 审批人行只能回传数组：后端拿它与审批人名单求交集，字符串会被当成一个工号
    values[row.field.name] = isApproverRow(row)
      ? [...row.list]
      : fromEditText(row.field.value, row.text);
  });
  return values;
}

function handleSubmit() {
  const list = props.approvals || [];
  if (list.length === 0) {
    message.warning('没有待审批的节点');
    return;
  }
  const decisions: ApprovalDecisionReq[] = [];
  for (const approval of list) {
    const draft = drafts[approval.nodeId];
    if (!draft) continue;
    decisions.push({
      approvalToken: approval.approvalToken,
      action: draft.action,
      opinion: draft.opinion.trim() || undefined,
      // 不同意时后端不回写编辑，下发过去也不会落库
      fieldValues:
        draft.action === 'APPROVE' ? collectFieldValues(draft) : undefined,
    });
  }
  if (decisions.length === 0) {
    message.warning('请为每份待审批给出结论');
    return;
  }
  emit('submit', decisions);
}
</script>

<template>
  <div class="approval-panel" data-testid="workflow-approval-panel">
    <div class="approval-panel-header">
      <span class="approval-panel-title">{{ title }}</span>
      <a-tag v-if="approvals.length > 1" color="purple" size="small">
        {{ approvals.length }} 项待决策
      </a-tag>
    </div>

    <div
      v-for="{ approval, draft } in cards"
      :key="approval.approvalToken"
      class="approval-card"
    >
      <div class="approval-card-head">
        <!-- title 就是后端给的「这句话」：等子流程时它已是「子工作流 X 需要你审批」 -->
        <span class="approval-node-name">{{ approval.title }}</span>
        <a-tag v-if="isChildAwaiting(approval)" color="blue" size="small">
          子工作流审批
        </a-tag>
      </div>

      <a-radio-group v-model:value="draft.action" button-style="solid">
        <a-radio-button value="APPROVE">
          <CheckCircleOutlined /> 同意
        </a-radio-button>
        <a-radio-button value="REJECT">
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

      <div v-if="draft.action === 'APPROVE'" class="approval-edits">
        <div class="approval-edits-title">
          要审的数据（可以改，改动随结论一起生效）
        </div>
        <div
          v-for="row in draft.rows"
          :key="row.field.name"
          class="approval-edit-row"
        >
          <div class="approval-edit-label">
            <span class="approval-edit-source">{{ rowLabel(row) }}</span>
            <a-tag v-if="isApproverRow(row)" color="cyan" size="small">
              审批人
            </a-tag>
            <a-tag v-if="isRowModified(row)" color="blue" size="small">
              已修改
            </a-tag>
          </div>
          <!-- 审批入参：值契约是数组，给文本框就会把一个工号提交成字符串 -->
          <a-select
            v-if="isApproverRow(row)"
            v-model:value="row.list"
            mode="tags"
            :token-separators="[',', ' ', ';']"
            style="width: 100%"
            placeholder="审批人 ID / 账号，回车确认，可多个"
          />
          <a-textarea v-else v-model:value="row.text" :rows="2" />
        </div>
        <a-empty
          v-if="draft.rows.length === 0"
          :image="false"
          description="这份待办没有要审的数据"
        />
      </div>
      <div v-else class="approval-reject-tip">
        不同意只记录审批结论，下游仍会照常执行；要按结论分流，请在下游接一个条件节点
        引用结论里的
        <span class="approval-reject-ref">review</span>
      </div>
    </div>

    <div class="approval-panel-footer">
      <a-button
        type="primary"
        size="small"
        :loading="submitting"
        @click="handleSubmit"
      >
        提交审批结论
      </a-button>
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

    .approval-reject-ref {
      font-family: Consolas, Menlo, monospace;
    }
  }

  .approval-panel-footer {
    display: flex;
    justify-content: flex-end;
  }
}
</style>
