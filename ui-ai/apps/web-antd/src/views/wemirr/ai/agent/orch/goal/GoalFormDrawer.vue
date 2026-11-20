<script lang="ts" setup>
/**
 * 目标 新增 / 编辑 / 查看 抽屉：执行者 + 检查者（最多两个智能体，各可克隆或自定义）。
 *
 * options（克隆来源 + 五类挂载候选）在抽屉里统一加载一次，经 props 下发给两个 MemberPicker；
 * 克隆预填走 agentLoader（目标侧 clone-source，ACL view）。executor 与 verifier 共用一份 state
 * （顺序执行语义）。readonly 即查看态。
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
  goalId?: null | number;
  readonly?: boolean;
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
  executor: { mounts: { skillIds: [], toolIds: [], mcpIds: [], kbIds: [] } } as MemberSpec,
  verifier: { mounts: { skillIds: [], toolIds: [], mcpIds: [], kbIds: [] } } as MemberSpec,
});

const title = computed(() =>
  props.readonly
    ? `查看目标：${form.name}`
    : props.goalId
      ? '编辑目标'
      : '新增目标',
);

function toOpts(items: api.ResourceOption[]): Opt[] {
  return (items || []).map((i) => ({
    label: i.description ? `${i.name}（${i.description}）` : i.name,
    value: i.id,
  }));
}

function emptyMounts(): MemberSpec {
  return { mounts: { skillIds: [], toolIds: [], mcpIds: [], kbIds: [] } };
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
    executor: emptyMounts(),
    verifier: emptyMounts(),
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
      if (props.goalId) {
        const d: any = await api.GetDetail(props.goalId);
        const cfg = d.config || {};
        Object.assign(form, {
          name: d.name || '',
          purpose: d.purpose || '',
          executor: cfg.executor || emptyMounts(),
          verifier: cfg.verifier || emptyMounts(),
        });
      }
    } catch (error: any) {
      message.error(error?.message || '加载失败');
    } finally {
      loading.value = false;
    }
  },
);

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('请输入目标名称');
    return;
  }
  if (!form.executor?.name?.trim() && !form.executor?.sourceAgentId) {
    message.warning('执行者需要一个名称或克隆来源');
    return;
  }
  if (!form.verifier?.name?.trim() && !form.verifier?.sourceAgentId) {
    message.warning('检查者需要一个名称或克隆来源');
    return;
  }
  const payload: api.OrchGoalSaveReq = {
    name: form.name.trim(),
    purpose: form.purpose.trim(),
    executor: form.executor,
    verifier: form.verifier,
  };
  saving.value = true;
  try {
    await (props.goalId ? api.UpdateObj(props.goalId, payload) : api.AddObj(payload));
    message.success(props.goalId ? '保存成功' : '创建成功');
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
    width="min(800px, 100vw)"
    placement="right"
    @update:open="close"
  >
    <a-spin :spinning="loading">
      <div class="orch-form">
        <section class="orch-section orch-basic">
          <div class="orch-section-heading">
            <div>
              <h3>目标定义</h3>
              <p>说明要达成的目标，便于识别和检索</p>
            </div>
          </div>
          <a-form layout="vertical">
            <a-row :gutter="12">
              <a-col :span="12">
                <a-form-item label="目标名称" required>
                  <a-input
                    v-model:value="form.name"
                    :disabled="props.readonly"
                    placeholder="给这个目标起个名字"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="用途（可选）">
                  <a-input
                    v-model:value="form.purpose"
                    :disabled="props.readonly"
                    placeholder="一句话说明要达成什么"
                  />
                </a-form-item>
              </a-col>
            </a-row>
          </a-form>
        </section>

        <section class="orch-section">
          <div class="orch-section-heading">
            <div>
              <h3>执行者</h3>
              <p>负责拆解目标并推进执行</p>
            </div>
            <span class="orch-role-badge">执行</span>
          </div>
          <MemberPicker
            v-model="form.executor"
            :agent-opts="agentOpts"
            :agent-loader="api.GetAgentDetail"
            :kb-opts="kbOpts"
            :mcp-opts="mcpOpts"
            :model-opts="modelOpts"
            :readonly="props.readonly"
            :skill-opts="skillOpts"
            :tool-opts="toolOpts"
          />
        </section>

        <section class="orch-section">
          <div class="orch-section-heading">
            <div>
              <h3>检查者</h3>
              <p>核对执行结果，不通过时反馈并驳回</p>
            </div>
            <span class="orch-role-badge is-verifier">验证</span>
          </div>
          <MemberPicker
            v-model="form.verifier"
            :agent-opts="agentOpts"
            :agent-loader="api.GetAgentDetail"
            :kb-opts="kbOpts"
            :mcp-opts="mcpOpts"
            :model-opts="modelOpts"
            :readonly="props.readonly"
            :skill-opts="skillOpts"
            :tool-opts="toolOpts"
          />
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
