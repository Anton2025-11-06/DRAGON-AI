<script lang="ts" setup>
/**
 * 编排成员编辑器（团队队长/成员、目标执行者/检查者、SOP 节点执行者/检查者复用）：克隆 or 自定义。
 *
 * - 顶部「克隆来源」下拉：选一个单智能体即经 agentLoader 预填 name/systemPrompt/mounts（客户端便利，
 *   后端 resolve_member 也会据 sourceAgentId 兜底合并）；清空则转纯自定义；
 * - name/description/systemPrompt + 五个 picker（模型/技能/工具/MCP/知识库），
 *   options 全部由父组件加载后经 props 传入（避免每个成员各拉一遍、且各模式端点解耦）；
 * - 克隆后仍可自由覆盖各字段——成员配置只落团队/目标/SOP 表 config_json，不回写单智能体。
 */
import { reactive, watch } from 'vue';

import PromptEditor from './PromptEditor.vue';

export interface Opt {
  label: string;
  value: number;
}

/** 成员挂载快照 */
export interface MemberMounts {
  kbIds: number[];
  mcpIds: number[];
  toolIds: number[];
  skillIds: number[];
  modelId?: null | number;
}

/** 成员规格：克隆(sourceAgentId) 或自定义 */
export interface MemberSpec {
  sourceAgentId?: null | number;
  name?: null | string;
  description?: null | string;
  systemPrompt?: null | string;
  mounts?: Partial<MemberMounts>;
}

const props = defineProps<{
  agentOpts: Opt[];
  kbOpts: Opt[];
  mcpOpts: Opt[];
  modelOpts: Opt[];
  /** 选克隆来源后据此拉取单智能体详情做预填（父组件提供，端点与准入由父收口） */
  agentLoader?: (id: number) => Promise<any>;
  /** 是否显示「职责描述」字段（团队成员需要，队长/目标不需要） */
  showDescription?: boolean;
  skillOpts: Opt[];
  toolOpts: Opt[];
  readonly?: boolean;
  modelValue: MemberSpec;
}>();

const emit = defineEmits<{ (e: 'update:modelValue', value: MemberSpec): void }>();

// 本地可编辑镜像（mounts 各列表单独 reactive，便于 v-model 绑多选）
const local = reactive({
  sourceAgentId: undefined as number | undefined,
  name: '',
  description: '',
  systemPrompt: '',
  skillIds: [] as number[],
  toolIds: [] as number[],
  mcpIds: [] as number[],
  kbIds: [] as number[],
  modelId: undefined as number | undefined,
});

let syncing = false;

watch(
  () => props.modelValue,
  (v) => {
    syncing = true;
    const m = v.mounts || {};
    Object.assign(local, {
      sourceAgentId: v.sourceAgentId ?? undefined,
      name: v.name || '',
      description: v.description || '',
      systemPrompt: v.systemPrompt || '',
      skillIds: m.skillIds || [],
      toolIds: m.toolIds || [],
      mcpIds: m.mcpIds || [],
      kbIds: m.kbIds || [],
      modelId: m.modelId ?? undefined,
    });
    queueMicrotask(() => {
      syncing = false;
    });
  },
  { immediate: true, deep: true },
);

watch(
  local,
  () => {
    if (syncing) return;
    emit('update:modelValue', {
      sourceAgentId: local.sourceAgentId ?? null,
      name: local.name || null,
      description: props.showDescription ? (local.description || null) : undefined,
      systemPrompt: local.systemPrompt || null,
      mounts: {
        skillIds: local.skillIds,
        toolIds: local.toolIds,
        mcpIds: local.mcpIds,
        kbIds: local.kbIds,
        modelId: local.modelId ?? null,
      },
    });
  },
  { deep: true },
);

async function onCloneChange(id: number | undefined) {
  local.sourceAgentId = id;
  if (!id || !props.agentLoader) return;
  // 预填克隆来源配置（后端仍会按 use 口径过滤）
  try {
    const d: any = await props.agentLoader(id);
    const m = d.mounts || {};
    local.name = d.name || local.name;
    local.systemPrompt = d.systemPrompt || '';
    local.skillIds = m.skillIds || [];
    local.toolIds = m.toolIds || [];
    local.mcpIds = m.mcpIds || [];
    local.kbIds = m.kbIds || [];
    local.modelId = m.modelId ?? local.modelId;
  } catch {
    // 预填失败不影响纯自定义，后端解析时仍会用 sourceAgentId 兜底
  }
}
</script>

<template>
  <div class="member-picker">
    <a-form layout="vertical">
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item label="克隆来源（单智能体）">
            <a-select
              :disabled="props.readonly"
              :options="props.agentOpts"
              allow-clear
              option-filter-prop="label"
              placeholder="留空则自定义角色"
              show-search
              :value="local.sourceAgentId"
              @update:value="onCloneChange"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item label="成员名称" required>
            <a-input
              v-model:value="local.name"
              :disabled="props.readonly"
              placeholder="输入角色名称"
            />
          </a-form-item>
        </a-col>
        <a-col v-if="props.showDescription" :span="24">
          <a-form-item label="职责描述">
            <a-textarea
              v-model:value="local.description"
              :auto-size="{ minRows: 2, maxRows: 3 }"
              :disabled="props.readonly"
              placeholder="擅长什么、何时派给它、返回什么"
            />
          </a-form-item>
        </a-col>
      </a-row>

      <div class="member-config">
        <div class="member-config-heading">
          <span>角色指令</span>
          <small>留空则沿用平台默认基础段</small>
        </div>
        <a-form-item label="系统提示词">
          <PromptEditor
            v-model="local.systemPrompt"
            :disabled="props.readonly"
            :height="128"
            placeholder="说明该角色的任务、边界和输出要求"
          />
        </a-form-item>

        <div class="member-config-heading is-mounts">
          <span>运行配置</span>
          <small>按需选择模型与可调用资源</small>
        </div>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="模型">
              <a-select
                v-model:value="local.modelId"
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
                v-model:value="local.skillIds"
                :disabled="props.readonly"
                :max-tag-count="1"
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
                v-model:value="local.toolIds"
                :disabled="props.readonly"
                :max-tag-count="1"
                :options="toolOpts"
                allow-clear
                mode="multiple"
                option-filter-prop="label"
                placeholder="选择工具"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="MCP">
              <a-select
                v-model:value="local.mcpIds"
                :disabled="props.readonly"
                :max-tag-count="1"
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
                v-model:value="local.kbIds"
                :disabled="props.readonly"
                :max-tag-count="1"
                :options="kbOpts"
                allow-clear
                mode="multiple"
                option-filter-prop="label"
                placeholder="选择知识库"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </div>
    </a-form>
  </div>
</template>

<style scoped lang="less">
.member-picker {
  min-width: 0;
}

.member-config {
  padding-top: 2px;
  clear: both;
}

.member-config-heading {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding-top: 12px;
  margin: 2px 0 10px;
  border-top: 1px solid var(--border-color, #edf0f4);

  span {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-color-1, #1f2329);
  }

  small {
    font-size: 12px;
    color: var(--text-color-3, #8c8c8c);
  }

  &.is-mounts {
    margin-top: 2px;
  }
}
</style>
