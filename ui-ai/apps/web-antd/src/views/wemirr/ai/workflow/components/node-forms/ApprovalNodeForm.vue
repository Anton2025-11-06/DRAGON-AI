<script setup lang="ts">
/**
 * APPROVAL 节点配置表单
 *
 * - 暂停范围（pauseScope）：DOWNSTREAM 仅本节点及下游等待 / ALL 整条工作流一起停。它只
 *   决定「等人给结论时停多大范围」，不决定拒绝后砍谁 —— 审批节点只收集结论，
 *   同意与不同意都照常往下游走，要分支就在下游接个条件节点引用 review
 * - 审批人（approvers）：数字或字符串混合的集合，命中其一即可审批；留空 = 任何持有
 *   api-key 且知道执行 id 的人皆可审。只有本节点配了审批人，才需要开始节点有一个
 *   「审批人(APPROVER)」入参作为提交时的身份来源（后端 graph.validate 同一口径 ERROR）
 * - 选择输出参数（passThroughInputs）：决定本节点把哪些上游数据放行给下游，对象可以只
 *   放行其中的某几个 key（配置形态 {nodeId,varName,path,name}）。候选项与下游变量下拉、
 *   审批面板的可编辑清单是同一份推导（前端 variable-selector/upstream-variables.ts，
 *   后端 nodes/approval_nodes.py），三边不同源就会出现「选得到却取不到、看得到却改不了」
 */
import type {
  ApprovalNodeConfig,
  ApprovalPassThroughInput,
} from '#/api/ai-workflow/types';

import { computed, reactive, ref, watch } from 'vue';

import { useAiWorkflowStore } from '#/store/ai-workflow';

import {
  approvalPassThroughOptions,
  decodePassThroughKey,
  encodePassThroughKey,
  flattenPassThroughOptions,
  normalizePassThroughInputs,
  passThroughOutputName,
  readGraph,
} from '../variable-selector/upstream-variables';

// Props
interface Props {
  config: ApprovalNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ApprovalNodeConfig): void;
}>();

const workflowStore = useAiWorkflowStore();

/** 表单里的树节点形态（a-tree 只认 key/title/children，域字段不外泄） */
interface TreeNode {
  children?: TreeNode[];
  disableCheckbox?: boolean;
  key: string;
  title: string;
}

/** 失效项单独成组：它们仍在配置里，得能看见也能被取消勾选 */
const STALE_GROUP_KEY = '__stale__';

/** 配置项 → 树节点键（与候选用同一个编码器，否则勾选状态对不上） */
function keyOf(item: ApprovalPassThroughInput): string {
  return encodePassThroughKey(item.nodeId, item.varName, item.path || '');
}

// 表单数据
const formData = reactive<ApprovalNodeConfig>({
  approvers: [],
  pauseScope: 'DOWNSTREAM',
  passThroughInputs: [],
});

/** 画布开始节点里是否已声明 APPROVER 入参（审批身份来源） */
const hasApproverInput = computed(() => {
  const canvas = workflowStore.canvasRef;
  if (!canvas) return true; // 画布未就绪时不误报
  const nodes = canvas.getNodes?.() || [];
  return nodes.some((node: any) => {
    if (node.data?.nodeType !== 'START') return false;
    const fields = node.data?.config?.fields || [];
    return fields.some((f: any) => f.type === 'APPROVER');
  });
});

/** 画布拓扑：候选按本节点的上游算，图上改一笔候选就跟着变 */
const graph = computed(() => readGraph(workflowStore.canvasRef));

const selfNode = computed(() =>
  graph.value.nodes.find((node) => node.id === props.nodeId),
);

/** 可选的透传来源：本节点全部上游节点的输出变量 + 开始节点入参（可到子字段） */
const passThroughGroups = computed(() =>
  selfNode.value ? approvalPassThroughOptions(selfNode.value, graph.value) : [],
);

/** 已配置但候选里已没有的项（源节点被删/输出变量改名）：运行期不会透传 */
const staleInputs = computed(() => {
  const known = new Set(
    flattenPassThroughOptions(passThroughGroups.value).map((item) => item.key),
  );
  return (formData.passThroughInputs || []).filter(
    (item) => !known.has(keyOf(item)),
  );
});

function toTreeNode(option: {
  children?: any[];
  key: string;
  title: string;
}): TreeNode {
  const node: TreeNode = { key: option.key, title: option.title };
  if (option.children && option.children.length > 0) {
    node.children = option.children.map((child) =>
      toTreeNode({
        children: child.children,
        key: child.key,
        title: child.title,
      }),
    );
  }
  return node;
}

/** 勾选树：节点名一层（不可勾选）→ 变量一层 → 结构化输出展开的 key */
const treeData = computed<TreeNode[]>(() => {
  const nodes: TreeNode[] = passThroughGroups.value.map((group) => ({
    children: group.options.map((option) => toTreeNode(option)),
    disableCheckbox: true,
    key: `group::${group.nodeId}`,
    title: group.nodeName,
  }));
  if (staleInputs.value.length > 0) {
    nodes.push({
      children: staleInputs.value.map((item) => ({
        key: keyOf(item),
        title: `${item.varName}${item.path ? `.${item.path}` : ''}（已失效）`,
      })),
      disableCheckbox: true,
      key: STALE_GROUP_KEY,
      title: '已失效（源节点或变量名已不在上游）',
    });
  }
  return nodes;
});

/** 树上所有「可勾选目标」的键，按树的顺序（新勾选项按它落位，保证先选先胜的顺序稳定） */
const targetKeys = computed<string[]>(() => [
  ...flattenPassThroughOptions(passThroughGroups.value).map((item) => item.key),
  ...staleInputs.value.map((item) => keyOf(item)),
]);

const checkedKeys = computed(() =>
  (formData.passThroughInputs || []).map((item) => keyOf(item)),
);

// 默认全部展开：诉求就是「把 obj 的每个 key 都显示出来」，收起来等于没显示
const expandedKeys = ref<string[]>([]);
watch(
  treeData,
  (nodes) => {
    const keys: string[] = [];
    const walk = (list: TreeNode[]) => {
      list.forEach((node) => {
        if (node.children && node.children.length > 0) {
          keys.push(node.key);
          walk(node.children);
        }
      });
    };
    walk(nodes);
    expandedKeys.value = keys;
  },
  { immediate: true },
);

function handleExpand(keys: any) {
  expandedKeys.value = (
    Array.isArray(keys) ? keys : keys?.node?.eventKey || []
  ) as string[];
}

/** 勾选变更：保留已存在项的别名，新项按树顺序追加（后端按选择顺序先选先胜） */
function handleCheck(checked: any) {
  const list: string[] = Array.isArray(checked)
    ? checked
    : checked?.checked || [];
  const picked = new Set(list);
  const existing = new Map(
    (formData.passThroughInputs || []).map((item) => [keyOf(item), item]),
  );
  const next: ApprovalPassThroughInput[] = [];
  targetKeys.value.forEach((key) => {
    if (!picked.has(key)) return;
    const old = existing.get(key);
    next.push(old ?? decodePassThroughKey(key));
  });
  formData.passThroughInputs = next;
  handleChange();
}

/** 源节点名（失效项也要能显示出来源，只拿得到 id 时就退化成 id） */
function nodeNameOf(nodeId: string): string {
  return graph.value.nodes.find((node) => node.id === nodeId)?.label || nodeId;
}

/** 已选清单：每行 = 一个透传目标，键名可改（下游就用这个名字引用） */
const selectedRows = computed(() =>
  (formData.passThroughInputs || []).map((item, index) => ({
    index,
    item,
    key: keyOf(item),
    outputName: passThroughOutputName(item),
    source: `${nodeNameOf(item.nodeId)}.${item.varName}${item.path ? `.${item.path}` : ''}`,
  })),
);

/** 已选条数提示（文案放这里，免得模板里的长文本被折行规则来回改） */
const selectedHint = computed(
  () => `已选 ${selectedRows.value.length} 项（输出键名可改，同名时先选的赢）`,
);

function handleAliasChange(index: number, value: string) {
  const items = formData.passThroughInputs || [];
  const item = items[index];
  if (!item) return;
  item.name = value.trim();
  formData.passThroughInputs = [...items];
  handleChange();
}

function removeSelected(index: number) {
  formData.passThroughInputs = (formData.passThroughInputs || []).filter(
    (_, position) => position !== index,
  );
  handleChange();
}

function resetSelection() {
  formData.passThroughInputs = [];
  handleChange();
}

/**
 * 缺审批入参：审批人留空时不限身份，不需要入参；配了审批人时提交拿不到
 * 审批身份，整条图过不了校验。
 */
const approverMisconfigured = computed(
  () => (formData.approvers || []).length > 0 && !hasApproverInput.value,
);

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      approvers: config.approvers ? [...config.approvers] : [],
      pauseScope: config.pauseScope || 'DOWNSTREAM',
      passThroughInputs: normalizePassThroughInputs(config.passThroughInputs),
    });
  },
  { deep: true, immediate: true },
);

// 处理配置变更
function handleChange() {
  emit('update:config', {
    ...formData,
    approvers: [...(formData.approvers || [])],
    passThroughInputs: [...(formData.passThroughInputs || [])],
  });
}

/** 审批人下拉：tags 模式允许自由输入，数字字面量归一化为 number（与后端交集比对口径一致） */
const approverValue = computed(() => (formData.approvers || []).map(String));

/** 审批人变更：去掉空白项，纯数字入参转成 number */
function handleApproversChange(value: string[]) {
  formData.approvers = (value || [])
    .map((item) => item.trim())
    .filter((item) => item !== '')
    .map((item) => (/^-?\d+$/.test(item) ? Number(item) : item));
  handleChange();
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <a-form-item label="暂停范围" required>
      <a-select v-model:value="formData.pauseScope" @change="handleChange">
        <a-select-option value="DOWNSTREAM">本节点及下游</a-select-option>
        <a-select-option value="ALL">整条工作流</a-select-option>
      </a-select>
      <div class="field-help">
        整条工作流：在跑分支会被取消，恢复后重跑；本节点及下游：仅等待本节点放行。
        两种都只影响「等结论时停多大范围」，不同意也不会砍下游
      </div>
    </a-form-item>

    <a-form-item label="审批人">
      <a-select
        mode="tags"
        :value="approverValue"
        :token-separators="[',', ' ']"
        placeholder="输入工号/用户名后回车，可填多个"
        style="width: 100%"
        @change="handleApproversChange"
      />
      <div class="field-help">
        留空 = 任何持有 api-key 且知道执行 id 者皆可审
      </div>
    </a-form-item>

    <a-alert
      v-if="approverMisconfigured"
      type="warning"
      show-icon
      message="开始节点缺少「审批人」入参"
      description="本节点限定了审批人，提交时就必须能拿到审批人身份：请到开始节点添加一个类型为 APPROVER 的输入参数，否则无法保存与提交；不限定审批人可把审批人清空。"
      class="misconfig-alert"
    />

    <a-form-item label="选择输出参数">
      <div class="field-help">
        审批放行时交给下游的输入参数：一个都不勾 =
        上游全部输出都透传；勾了则只透传勾选的这些。对象与数组已按子字段展开，
        可直接勾到某个 key / 下标（下游按输出键名引用）。审批结论
        review/reviewOpinion/reviewBy
        始终输出，审批面板里的可编辑清单与本选择同源。
      </div>

      <a-tree
        block-node
        checkable
        :check-strictly="true"
        :tree-data="treeData"
        :checked-keys="checkedKeys"
        :expanded-keys="expandedKeys"
        class="pass-through-tree"
        @check="handleCheck"
        @expand="handleExpand"
      />

      <div v-if="selectedRows.length > 0" class="pass-through-selected">
        <div class="selected-head">
          <span>{{ selectedHint }}</span>
          <a-button type="link" size="small" @click="resetSelection">
            清空
          </a-button>
        </div>
        <div v-for="row in selectedRows" :key="row.key" class="selected-row">
          <span class="selected-source" :title="row.source">{{
            row.source
          }}</span>
          <a-input
            :value="row.item.name"
            size="small"
            class="name-input"
            :placeholder="row.outputName"
            @change="
              (event: any) => handleAliasChange(row.index, event.target.value)
            "
          />
          <a-button
            type="link"
            size="small"
            danger
            @click="removeSelected(row.index)"
          >
            移除
          </a-button>
        </div>
      </div>
    </a-form-item>
  </a-form>
</template>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  .field-help {
    margin-bottom: 6px;
    font-size: 12px;
    line-height: 1.5;
    color: #8c8c8c;
  }

  .misconfig-alert {
    margin-bottom: 16px;
    font-size: 12px;
  }
}

.pass-through-tree {
  max-height: 240px;
  padding: 4px;
  font-size: 12px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
}

.pass-through-selected {
  margin-top: 8px;

  .selected-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 2px;
    font-size: 12px;
    color: #8c8c8c;
  }

  .selected-row {
    display: flex;
    gap: 6px;
    align-items: center;
    margin-bottom: 4px;
    font-size: 12px;

    .selected-source {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      color: #595959;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .name-input {
      width: 120px;
      flex: none;
    }
  }
}
</style>
