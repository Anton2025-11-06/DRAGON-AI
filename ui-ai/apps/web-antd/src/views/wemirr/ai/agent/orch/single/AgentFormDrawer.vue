<script lang="ts" setup>
/**
 * 单智能体 新增 / 编辑 / 查看 抽屉。
 *
 * 五个 picker（技能/工具/MCP/知识库/模型）数据源全部来自后端 use 口径可见集合
 * （usable-resources / models），只展示 id+名称+用途，不泄露密钥；
 * readonly 模式即「查看」：所有控件禁用、隐藏保存，只呈现配置。
 */
import { computed, reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import PromptEditor from '../shared/PromptEditor.vue';
import * as api from './api';

const props = defineProps<{
  agentId?: null | number;
  /** 查看态：只读呈现，不给保存 */
  readonly?: boolean;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'saved'): void;
}>();

interface Opt {
  label: string;
  value: number;
}

const loading = ref(false);
const saving = ref(false);

const skillOpts = ref<Opt[]>([]);
const toolOpts = ref<Opt[]>([]);
const mcpOpts = ref<Opt[]>([]);
const kbOpts = ref<Opt[]>([]);
const modelOpts = ref<Opt[]>([]);

const form = reactive({
  name: '',
  purpose: '',
  systemPrompt: '',
  skillIds: [] as number[],
  toolIds: [] as number[],
  mcpIds: [] as number[],
  kbIds: [] as number[],
  modelId: undefined as number | undefined,
});

const title = computed(() =>
  props.readonly
    ? `查看智能体：${form.name}`
    : props.agentId
      ? '编辑智能体'
      : '新增智能体',
);

function toOpts(items: api.ResourceOption[]): Opt[] {
  return (items || []).map((i) => ({
    label: i.description ? `${i.name}（${i.description}）` : i.name,
    value: i.id,
  }));
}

async function loadOptions() {
  const [skill, tool, mcp, kb, models] = await Promise.all([
    api.GetUsableResources('skill'),
    api.GetUsableResources('tool'),
    api.GetUsableResources('mcp'),
    api.GetUsableResources('kb'),
    api.ListModels(),
  ]);
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
    systemPrompt: '',
    skillIds: [],
    toolIds: [],
    mcpIds: [],
    kbIds: [],
    modelId: undefined,
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
      if (props.agentId) {
        const d: any = await api.GetDetail(props.agentId);
        const m = d.mounts || {};
        Object.assign(form, {
          name: d.name || '',
          purpose: d.purpose || '',
          systemPrompt: d.systemPrompt || '',
          skillIds: m.skillIds || [],
          toolIds: m.toolIds || [],
          mcpIds: m.mcpIds || [],
          kbIds: m.kbIds || [],
          modelId: m.modelId ?? undefined,
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
    message.warning('请输入智能体名称');
    return;
  }
  const payload: api.OrchAgentSaveReq = {
    name: form.name.trim(),
    purpose: form.purpose.trim(),
    systemPrompt: form.systemPrompt || undefined,
    mounts: {
      skillIds: form.skillIds,
      toolIds: form.toolIds,
      mcpIds: form.mcpIds,
      kbIds: form.kbIds,
      modelId: form.modelId ?? null,
    },
  };
  saving.value = true;
  try {
    await (props.agentId
      ? api.UpdateObj(props.agentId, payload)
      : api.AddObj(payload));
    message.success(props.agentId ? '保存成功' : '创建成功');
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
    width="min(680px, 100vw)"
    placement="right"
    @update:open="close"
  >
    <a-spin :spinning="loading">
      <div class="orch-form">
        <section class="orch-section orch-basic">
          <div class="orch-section-heading">
            <div>
              <h3>基本信息</h3>
              <p>名称与用途用于列表展示和检索</p>
            </div>
          </div>
          <a-form layout="vertical">
            <a-row :gutter="12">
              <a-col :span="12">
                <a-form-item label="智能体名称" required>
                  <a-input
                    v-model:value="form.name"
                    :disabled="props.readonly"
                    placeholder="给这个智能体起个名字"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="用途（可选）">
                  <a-input
                    v-model:value="form.purpose"
                    :disabled="props.readonly"
                    placeholder="一句话说明它擅长做什么"
                  />
                </a-form-item>
              </a-col>
            </a-row>
          </a-form>
        </section>

        <section class="orch-section">
          <div class="orch-section-heading">
            <div>
              <h3>角色指令</h3>
              <p>留空则沿用平台默认基础段</p>
            </div>
          </div>
          <a-form layout="vertical">
            <a-form-item label="系统提示词">
              <PromptEditor
                v-model="form.systemPrompt"
                :disabled="props.readonly"
                :height="144"
                placeholder="说明角色目标、行为边界和输出要求"
              />
            </a-form-item>
          </a-form>
        </section>

        <section class="orch-section">
          <div class="orch-section-heading">
            <div>
              <h3>能力挂载</h3>
              <p>配置运行模型，以及该智能体可调用的资源</p>
            </div>
          </div>
          <a-form layout="vertical">
            <a-row :gutter="12">
              <a-col :span="12">
                <a-form-item label="模型">
                  <a-select
                    v-model:value="form.modelId"
                    :disabled="props.readonly"
                    :options="modelOpts"
                    allow-clear
                    option-filter-prop="label"
                    placeholder="选择运行模型"
                    show-search
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="技能">
                  <a-select
                    v-model:value="form.skillIds"
                    :disabled="props.readonly"
                    :max-tag-count="2"
                    :options="skillOpts"
                    allow-clear
                    mode="multiple"
                    option-filter-prop="label"
                    placeholder="选择技能"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="工具">
                  <a-select
                    v-model:value="form.toolIds"
                    :disabled="props.readonly"
                    :max-tag-count="2"
                    :options="toolOpts"
                    allow-clear
                    mode="multiple"
                    option-filter-prop="label"
                    placeholder="选择平台工具"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="MCP">
                  <a-select
                    v-model:value="form.mcpIds"
                    :disabled="props.readonly"
                    :max-tag-count="2"
                    :options="mcpOpts"
                    allow-clear
                    mode="multiple"
                    option-filter-prop="label"
                    placeholder="选择 MCP 连接"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item label="知识库">
                  <a-select
                    v-model:value="form.kbIds"
                    :disabled="props.readonly"
                    :max-tag-count="2"
                    :options="kbOpts"
                    allow-clear
                    mode="multiple"
                    option-filter-prop="label"
                    placeholder="选择知识库"
                  />
                </a-form-item>
              </a-col>
            </a-row>
          </a-form>
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
