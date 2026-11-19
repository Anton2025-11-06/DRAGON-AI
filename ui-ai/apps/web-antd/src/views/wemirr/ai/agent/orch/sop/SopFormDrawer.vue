<script lang="ts" setup>
/**
 * SOP 新增 / 编辑 / 查看 抽屉：先配流程节点（名称/用途 + 节点数组）。
 *
 * 每个节点：名称 / 流程定义 / 执行者 / 检查者（可空）/ 最大驳回次数（默认 3）。
 * 执行者/检查者从单智能体克隆或自定义（复用 shared/MemberPicker，use 口径过滤）。
 * options（克隆来源 + 五类挂载候选）在抽屉里统一加载一次经 props 下发。数组序即流程顺序，
 * 支持上移/下移排序与增删。流程所有节点共用一份 state（顺序执行语义）。readonly 即查看态。
 */
import { computed, reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import MemberPicker, {
  type MemberSpec,
  type Opt,
} from '../shared/MemberPicker.vue';
import * as api from './api';

const props = defineProps<{
  open: boolean;
  readonly?: boolean;
  sopId?: null | number;
}>();

const emit = defineEmits<{
  (e: 'saved'): void;
  (e: 'update:open', value: boolean): void;
}>();

const loading = ref(false);
const saving = ref(false);

const agentOpts = ref<Opt[]>([]);
const modelOpts = ref<Opt[]>([]);
const skillOpts = ref<Opt[]>([]);
const toolOpts = ref<Opt[]>([]);
const mcpOpts = ref<Opt[]>([]);
const kbOpts = ref<Opt[]>([]);

const form = reactive({
  name: '',
  purpose: '',
  status: true,
  steps: [] as StepRow[],
});

/** 节点行：本地可编辑形态（executor/verifier 为 MemberSpec，subject/description/maxAttempts 直存） */
interface StepRow {
  subject: string;
  description: string;
  maxAttempts: number;
  executor: MemberSpec;
  verifier: MemberSpec;
  hasVerifier: boolean;
}

const title = computed(() =>
  props.readonly
    ? `查看 SOP：${form.name}`
    : props.sopId
      ? '编辑 SOP'
      : '新增 SOP',
);

function toOpts(items: api.ResourceOption[]): Opt[] {
  return (items || []).map((i) => ({
    label: i.description ? `${i.name}（${i.description}）` : i.name,
    value: i.id,
  }));
}

function emptyMember(): MemberSpec {
  return { mounts: { skillIds: [], toolIds: [], mcpIds: [], kbIds: [] } };
}

function emptyStep(): StepRow {
  return {
    subject: '',
    description: '',
    maxAttempts: 3,
    executor: emptyMember(),
    verifier: emptyMember(),
    hasVerifier: false,
  };
}

async function loadOptions() {
  const [agents, skill, tool, mcp, kb, models] = await Promise.all([
    api.ListAgentsForClone(),
    api.GetUsableResources('skill'),
    api.GetUsableResources('tool'),
    api.GetUsableResources('mcp'),
    api.GetUsableResources('kb'),
    api.ListModels(),
  ]);
  agentOpts.value = toOpts(agents.items);
  skillOpts.value = toOpts(skill.items);
  toolOpts.value = toOpts(tool.items);
  mcpOpts.value = toOpts(mcp.items);
  kbOpts.value = toOpts(kb.items);
  modelOpts.value = toOpts(models.items);
}

function resetForm() {
  Object.assign(form, {
    name: '',
    purpose: '',
    status: true,
    steps: [emptyStep()],
  });
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    loading.value = true;
    resetForm();
    try {
      await loadOptions();
      if (props.sopId) {
        const d: any = await api.GetDetail(props.sopId);
        const cfg = d.config || {};
        const steps = (cfg.steps || []).map((s: any) => ({
          subject: s.subject || '',
          description: s.description || '',
          maxAttempts: s.max_attempts ?? 3,
          executor: s.executor || emptyMember(),
          verifier: s.verifier || emptyMember(),
          hasVerifier: !!s.verifier,
        }));
        Object.assign(form, {
          name: d.name || '',
          purpose: d.purpose || '',
          status: d.status !== 0,
          steps: steps.length ? steps : [emptyStep()],
        });
      }
    } catch (error: any) {
      message.error(error?.message || '加载失败');
    } finally {
      loading.value = false;
    }
  },
);

function addStep() {
  form.steps.push(emptyStep());
}

function removeStep(index: number) {
  if (form.steps.length <= 1) {
    message.warning('SOP 至少保留一个流程节点');
    return;
  }
  form.steps.splice(index, 1);
}

function moveStep(index: number, delta: number) {
  const target = index + delta;
  if (target < 0 || target >= form.steps.length) return;
  const arr = form.steps;
  [arr[index], arr[target]] = [arr[target]!, arr[index]!];
}

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('请输入 SOP 名称');
    return;
  }
  const steps: api.OrchSopStepSpec[] = [];
  for (const [i, s] of form.steps.entries()) {
    if (!s.subject.trim()) {
      message.warning(`第 ${i + 1} 个节点缺少名称`);
      return;
    }
    if (!s.executor?.name?.trim() && !s.executor?.sourceAgentId) {
      message.warning(`第 ${i + 1} 个节点的执行者需要名称或克隆来源`);
      return;
    }
    const useVerifier = s.hasVerifier && (s.verifier?.name?.trim() || s.verifier?.sourceAgentId);
    steps.push({
      subject: s.subject.trim(),
      description: s.description || undefined,
      maxAttempts: s.maxAttempts,
      executor: s.executor,
      verifier: useVerifier ? s.verifier : undefined,
    });
  }
  if (!steps.length) {
    message.warning('请至少配置一个流程节点');
    return;
  }
  const payload: api.OrchSopSaveReq = {
    name: form.name.trim(),
    purpose: form.purpose || undefined,
    status: form.status,
    steps,
  };
  saving.value = true;
  try {
    await (props.sopId ? api.UpdateObj(props.sopId, payload) : api.AddObj(payload));
    message.success(props.sopId ? '保存成功' : '创建成功');
    emit('update:open', false);
    emit('saved');
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  } finally {
    saving.value = false;
  }
}

function close() {
  emit('update:open', false);
}
</script>

<template>
  <a-drawer
    :open="props.open"
    :title="title"
    :width="820"
    placement="right"
    @update:open="close"
  >
    <a-spin :spinning="loading">
      <a-form layout="vertical">
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="SOP 名称" required>
              <a-input
                v-model:value="form.name"
                :disabled="props.readonly"
                placeholder="给这套 SOP 起个名字"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="用途">
              <a-input
                v-model:value="form.purpose"
                :disabled="props.readonly"
                placeholder="一句话说明这套流程做什么"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="启用状态">
              <a-switch
                v-model:checked="form.status"
                :disabled="props.readonly"
                checked-children="启用"
                un-checked-children="停用"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form>

      <a-divider orientation="left">
        流程节点
        <a-button
          v-if="!props.readonly"
          class="add-step-btn"
          size="small"
          type="link"
          @click="addStep"
        >
          + 添加节点
        </a-button>
      </a-divider>

      <div v-for="(s, i) in form.steps" :key="i" class="step-card">
        <div class="step-head">
          <span class="step-no">节点 {{ i + 1 }}</span>
          <a-space v-if="!props.readonly">
            <a-button :disabled="i === 0" size="small" type="link" @click="moveStep(i, -1)">
              上移
            </a-button>
            <a-button
              :disabled="i === form.steps.length - 1"
              size="small"
              type="link"
              @click="moveStep(i, 1)"
            >
              下移
            </a-button>
            <a-button danger size="small" type="link" @click="removeStep(i)">移除</a-button>
          </a-space>
        </div>

        <a-form layout="vertical">
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="节点名称" required>
                <a-input
                  v-model:value="s.subject"
                  :disabled="props.readonly"
                  placeholder="该流程节点叫什么"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="最大驳回次数">
                <a-input-number
                  v-model:value="s.maxAttempts"
                  :disabled="props.readonly"
                  :max="20"
                  :min="1"
                  style="width: 100%"
                />
              </a-form-item>
            </a-col>
            <a-col :span="24">
              <a-form-item label="流程定义">
                <a-textarea
                  v-model:value="s.description"
                  :autosize="{ minRows: 2, maxRows: 5 }"
                  :disabled="props.readonly"
                  placeholder="该节点要达成什么（供执行者遵循）"
                />
              </a-form-item>
            </a-col>
          </a-row>
        </a-form>

        <div class="field-label">执行者</div>
        <MemberPicker
          v-model="s.executor"
          :agent-opts="agentOpts"
          :agent-loader="api.GetAgentDetail"
          :kb-opts="kbOpts"
          :mcp-opts="mcpOpts"
          :model-opts="modelOpts"
          :readonly="props.readonly"
          :skill-opts="skillOpts"
          :tool-opts="toolOpts"
        />

        <a-checkbox
          v-if="!props.readonly"
          v-model:checked="s.hasVerifier"
          class="verifier-toggle"
        >
          配置检查者（留空则执行完即通过）
        </a-checkbox>
        <template v-if="s.hasVerifier">
          <div class="field-label">检查者</div>
          <MemberPicker
            v-model="s.verifier"
            :agent-opts="agentOpts"
            :agent-loader="api.GetAgentDetail"
            :kb-opts="kbOpts"
            :mcp-opts="mcpOpts"
            :model-opts="modelOpts"
            :readonly="props.readonly"
            :skill-opts="skillOpts"
            :tool-opts="toolOpts"
          />
        </template>
      </div>

      <div class="drawer-foot">
        <a-space>
          <a-button @click="close">{{ props.readonly ? '关闭' : '取消' }}</a-button>
          <a-button
            v-if="!props.readonly"
            :loading="saving"
            type="primary"
            @click="handleSave"
          >
            保存
          </a-button>
        </a-space>
      </div>
    </a-spin>
  </a-drawer>
</template>

<style scoped lang="less">
.step-card {
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;

  .step-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;

    .step-no {
      font-weight: 600;
    }
  }

  .field-label {
    margin: 4px 0;
    font-size: 12px;
    color: #8c8c8c;
  }

  .verifier-toggle {
    margin: 8px 0;
  }
}

.add-step-btn {
  margin-left: 12px;
}

.drawer-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
