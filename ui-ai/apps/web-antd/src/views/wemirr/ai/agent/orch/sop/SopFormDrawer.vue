<script lang="ts" setup>
/**
 * SOP 新增 / 编辑 / 查看 抽屉：先配流程节点（名称/用途 + 节点数组）。
 *
 * 每个节点：名称 / 流程定义 / 执行者 / 检查者（可空）/ 最大驳回次数（默认 3）。
 * 执行者/检查者从单智能体克隆或自定义（复用 shared/MemberPicker，use 口径过滤）。
 * options（克隆来源 + 五类挂载候选）在抽屉里统一加载一次经 props 下发。数组序即流程顺序，
 * 支持上移/下移排序与增删。流程所有节点共用一份 state（顺序执行语义）。readonly 即查看态。
 *
 * 节点多时抽屉会被拉得很长，所以：「+ 添加节点」固定在节点列表顶部，
 * 不用滚动到底部去点；每个节点自己可收起/展开（collapsed 只存本地行对象，不进提交载荷）。
 */
import { computed, reactive, ref, watch } from 'vue';

import { DownOutlined, RightOutlined } from '@ant-design/icons-vue';
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
  /** 本地折叠态（纯 UI，不参与提交）：上移/下移随节点一起走，不会错位 */
  collapsed: boolean;
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
    collapsed: false,
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
          collapsed: false,
        }));
        Object.assign(form, {
          name: d.name || '',
          purpose: d.purpose || '',
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
    purpose: form.purpose.trim(),
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
    root-class-name="orch-drawer"
    width="min(860px, 100vw)"
    placement="right"
    @update:open="close"
  >
    <a-spin :spinning="loading">
      <div class="orch-form">
        <section class="orch-section orch-basic">
          <div class="orch-section-heading">
            <div>
              <h3>流程信息</h3>
              <p>定义这套标准流程的名称和用途</p>
            </div>
          </div>
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
                <a-form-item label="用途（可选）">
                  <a-input
                    v-model:value="form.purpose"
                    :disabled="props.readonly"
                    placeholder="一句话说明这套流程做什么"
                  />
                </a-form-item>
              </a-col>
            </a-row>
          </a-form>
        </section>

        <section class="orch-section orch-step-list">
          <div class="orch-section-heading">
            <div>
              <h3>流程节点</h3>
              <p>节点按列表顺序执行，可收起暂时不编辑的配置</p>
            </div>
            <a-button
              v-if="!props.readonly"
              class="add-step-btn"
              type="dashed"
              @click="addStep"
            >
              + 添加节点
            </a-button>
          </div>

          <div v-for="(s, i) in form.steps" :key="i" class="step-card">
            <div class="step-head">
              <span class="step-no">节点 {{ i + 1 }}</span>
              <span v-if="s.collapsed" class="step-title">{{ s.subject || '未命名节点' }}</span>
              <a-space :size="2">
                <a-button size="small" type="text" @click="s.collapsed = !s.collapsed">
                  <component :is="s.collapsed ? RightOutlined : DownOutlined" />
                  {{ s.collapsed ? '展开' : '收起' }}
                </a-button>
                <template v-if="!props.readonly">
                  <a-button :disabled="i === 0" size="small" type="text" @click="moveStep(i, -1)">
                    上移
                  </a-button>
                  <a-button
                    :disabled="i === form.steps.length - 1"
                    size="small"
                    type="text"
                    @click="moveStep(i, 1)"
                  >
                    下移
                  </a-button>
                  <a-button danger size="small" type="text" @click="removeStep(i)">移除</a-button>
                </template>
              </a-space>
            </div>

            <div v-show="!s.collapsed" class="step-body">
              <a-form layout="vertical">
                <a-row :gutter="12">
                  <a-col :span="16">
                    <a-form-item label="节点名称" required>
                      <a-input
                        v-model:value="s.subject"
                        :disabled="props.readonly"
                        placeholder="该流程节点叫什么"
                      />
                    </a-form-item>
                  </a-col>
                  <a-col :span="8">
                    <a-form-item label="最大驳回次数">
                      <a-input-number
                        v-model:value="s.maxAttempts"
                        :disabled="props.readonly"
                        :max="20"
                        :min="1"
                      />
                    </a-form-item>
                  </a-col>
                  <a-col :span="24">
                    <a-form-item label="流程定义">
                      <a-textarea
                        v-model:value="s.description"
                        :auto-size="{ minRows: 2, maxRows: 4 }"
                        :disabled="props.readonly"
                        placeholder="该节点要达成什么（供执行者遵循）"
                      />
                    </a-form-item>
                  </a-col>
                </a-row>
              </a-form>

              <div class="role-block">
                <div class="role-heading">
                  <span>执行者</span>
                  <small>完成本节点定义的工作</small>
                </div>
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
              </div>

              <a-checkbox
                v-if="!props.readonly"
                v-model:checked="s.hasVerifier"
                class="verifier-toggle"
              >
                配置检查者（留空则执行完即通过）
              </a-checkbox>
              <div v-if="s.hasVerifier" class="role-block is-verifier">
                <div class="role-heading">
                  <span>检查者</span>
                  <small>检查结果，不通过时反馈并驳回</small>
                </div>
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
              </div>
            </div>
          </div>
        </section>

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
      </div>
    </a-spin>
  </a-drawer>
</template>

<style scoped lang="less">
.step-card {
  overflow: hidden;
  border: 1px solid var(--border-color, #e8edf3);
  border-radius: 8px;

  & + .step-card {
    margin-top: 10px;
  }
}

.step-head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  min-height: 42px;
  padding: 4px 8px 4px 12px;
  background: var(--hover-bg-color, #f7f9fc);
}

.step-no {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
}

.step-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  color: var(--text-color-3, #8c8c8c);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-body {
  padding: 14px 12px 12px;
}

.role-block {
  padding: 12px;
  margin-top: 4px;
  background: var(--hover-bg-color, #f8fafc);
  border: 1px solid var(--border-color, #edf0f4);
  border-radius: 8px;

  &.is-verifier {
    margin-top: 12px;
  }
}

.role-heading {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 12px;

  span {
    font-size: 13px;
    font-weight: 600;
  }

  small {
    font-size: 12px;
    color: var(--text-color-3, #8c8c8c);
  }
}

.verifier-toggle {
  display: flex;
  margin: 14px 2px 0;
}
</style>
