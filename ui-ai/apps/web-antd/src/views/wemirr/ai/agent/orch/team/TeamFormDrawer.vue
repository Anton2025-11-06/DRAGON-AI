<script lang="ts" setup>
/**
 * 团队 新增 / 编辑 / 查看 抽屉：队长 + 成员列表（各自可克隆或自定义）。
 *
 * options（克隆来源 + 五类挂载候选）在抽屉里统一加载一次，经 props 下发给每个 MemberPicker；
 * 克隆预填走 agentLoader（团队侧 clone-source，ACL view）。readonly 即查看态。
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
  teamId?: null | number;
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
  leader: { mounts: { skillIds: [], toolIds: [], mcpIds: [], kbIds: [] } } as MemberSpec,
  members: [] as MemberSpec[],
});

const title = computed(() =>
  props.readonly
    ? `查看团队：${form.name}`
    : props.teamId
      ? '编辑团队'
      : '新增团队',
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
    status: true,
    leader: emptyMounts(),
    members: [emptyMounts()],
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
      if (props.teamId) {
        const d: any = await api.GetDetail(props.teamId);
        const cfg = d.config || {};
        Object.assign(form, {
          name: d.name || '',
          purpose: d.purpose || '',
          status: d.status !== 0,
          leader: cfg.leader || emptyMounts(),
          members: (cfg.members || []).length ? cfg.members : [emptyMounts()],
        });
      }
    } catch (error: any) {
      message.error(error?.message || '加载失败');
    } finally {
      loading.value = false;
    }
  },
);

function addMember() {
  form.members.push(emptyMounts());
}

function removeMember(index: number) {
  if (form.members.length <= 1) {
    message.warning('团队至少保留一个成员');
    return;
  }
  form.members.splice(index, 1);
}

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('请输入团队名称');
    return;
  }
  if (!form.leader?.name?.trim() && !form.leader?.sourceAgentId) {
    message.warning('队长需要一个名称或克隆来源');
    return;
  }
  const members = form.members.filter(
    (m) => (m.name && m.name.trim()) || m.sourceAgentId,
  );
  if (!members.length) {
    message.warning('请至少配置一个成员');
    return;
  }
  const payload: api.OrchTeamSaveReq = {
    name: form.name.trim(),
    purpose: form.purpose || undefined,
    status: form.status,
    leader: form.leader,
    members,
  };
  saving.value = true;
  try {
    await (props.teamId ? api.UpdateObj(props.teamId, payload) : api.AddObj(payload));
    message.success(props.teamId ? '保存成功' : '创建成功');
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
    :width="760"
    placement="right"
    @update:open="close"
  >
    <a-spin :spinning="loading">
      <a-form layout="vertical">
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="团队名称" required>
              <a-input
                v-model:value="form.name"
                :disabled="props.readonly"
                placeholder="给这个团队起个名字"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="用途">
              <a-input
                v-model:value="form.purpose"
                :disabled="props.readonly"
                placeholder="一句话说明团队擅长做什么"
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

      <a-divider orientation="left">主智能体（队长）</a-divider>
      <MemberPicker
        v-model="form.leader"
        :agent-opts="agentOpts"
        :agent-loader="api.GetAgentDetail"
        :kb-opts="kbOpts"
        :mcp-opts="mcpOpts"
        :model-opts="modelOpts"
        :readonly="props.readonly"
        :skill-opts="skillOpts"
        :tool-opts="toolOpts"
      />

      <a-divider orientation="left">
        成员列表
        <a-button
          v-if="!props.readonly"
          class="add-member-btn"
          size="small"
          type="link"
          @click="addMember"
        >
          + 添加成员
        </a-button>
      </a-divider>
      <div v-for="(m, i) in form.members" :key="i" class="member-row">
        <div v-if="!props.readonly" class="member-actions">
          <a-button danger size="small" type="link" @click="removeMember(i)">移除</a-button>
        </div>
        <MemberPicker
          :model-value="m"
          :agent-opts="agentOpts"
          :agent-loader="api.GetAgentDetail"
          :kb-opts="kbOpts"
          :mcp-opts="mcpOpts"
          :model-opts="modelOpts"
          :readonly="props.readonly"
          :show-description="true"
          :skill-opts="skillOpts"
          :tool-opts="toolOpts"
          @update:model-value="form.members[i] = $event"
        />
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
.member-row {
  position: relative;

  .member-actions {
    display: flex;
    justify-content: flex-end;
    margin-bottom: -4px;
  }
}

.add-member-btn {
  margin-left: 12px;
}

.drawer-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
