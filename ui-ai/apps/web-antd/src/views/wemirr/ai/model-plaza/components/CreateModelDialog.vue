<script setup lang="ts">
/**
 * 添加/编辑模型弹窗（对齐 common_model 设计）
 * 表单分组：
 * - base_form_data：基础信息（name 模型名称 / model_type 能力类型(12类) / model_name 模型标识 / provider 厂商）
 * - credential：凭据信息（base_url 厂商接口基础地址(必填) / gateway_url 网关展示地址 / api_key 管理端密钥(必填)）
 * - advanced：高级设置（是否支持流消息/思考模式、开启流式/思考的参数键名、常用参数列表、限流 QPS、启用状态）
 *
 * 【设计对齐 2026-09】模型登记只需 provider + category(12类) + model_name + base_url，
 * 具体接口端点由 common_model 各厂商子类按能力类型自行拼接；选择厂商时自动回填该厂商默认 base_url。
 * 提交时展平为后端 ModelSaveRequest（category 即 model_type）。
 *
 * 【高级设置改造】测试按钮移至「确定」左侧：弹出 ModelTryPanel（用当前未保存表单参数调 /models/test），
 * 面板内提供流式/思考开关、常用参数、按类型的输入区（文件型可上传）与结果展示。
 */
import { computed, nextTick, reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';

import { PROVIDER_DEFAULT_BASE_URL } from '#/api/ai-workflow/const';

import * as api from '../api';
import type { CommonParam, CommonParamType } from '../api';
import ModelTryPanel from './ModelTryPanel.vue';

// ==================== 入参/事件 ====================

const props = defineProps<{
  /** 编辑的模型详情（null 为新增） */
  model?: api.ModelDetailRep | null;
  open: boolean;
  /** 当前选中的提供商 key，空串代表未选择（需在弹窗内选择） */
  provider?: string;
}>();

const emit = defineEmits<{
  success: [];
  'update:open': [open: boolean];
}>();

// ==================== 状态 ====================

const saving = ref(false);
const activeTab = ref('base-info');

// ===== 测试弹窗（ModelTryPanel）=====
const testPanelOpen = ref(false);
const testRunning = ref(false);
const testResult = ref<api.ModelTestRep | null>(null);
const testError = ref<null | string>(null);
/** 测试弹窗挂到 body，避免被主弹窗内表单容器捕获导致定位错乱 */
const getTestContainer = () =>
  typeof document === 'undefined' ? undefined : document.body;

// 分类/提供商字典（后端 /categories 接口）
const categoryOptions = ref<{ label: string; value: string }[]>([]);
const providerOptions = ref<{ label: string; value: string }[]>([]);

// ===== 基础信息 =====
const base_form_data = reactive({
  name: '',
  model_type: '', // 模型类型 = 后端 category（12 能力类型 code）
  model_name: '', // 模型标识
  provider: '', // 提供商（表单校验直接读 base_form_data，避免独立 ref 永远 undefined）
  rate_limit_qps: 0,
  tutorial_md: '',
  status: true,
});
// ===== 凭据信息 =====
const credential = reactive({
  base_url: '',
  gateway_url: '',
  api_key: '',
});
// ===== 高级设置 =====
const advanced = reactive({
  supports_stream: false,
  supports_thinking: false,
  stream_param: '',
  thinking_param: '',
});
const commonParams = ref<CommonParam[]>([]);

const PARAM_TYPE_OPTIONS: { label: string; value: CommonParamType }[] = [
  { label: '布尔', value: 'boolean' },
  { label: '整数', value: 'integer' },
  { label: '浮点数', value: 'number' },
  { label: 'JSON', value: 'object' },
  { label: '字符串', value: 'string' },
];

/** 限流 QPS：开关「限制」=有正数 QPS（蓝），「不限制」=0（灰）；底层存 rate_limit_qps */
const rateLimited = computed({
  get: () => !!base_form_data.rate_limit_qps,
  set: (v: boolean) => {
    base_form_data.rate_limit_qps = v ? 1 : 0;
  },
});

function addParam() {
  commonParams.value.push({ name: '', default: '', desc: '', type: 'string' });
}
function removeParam(idx: number) {
  commonParams.value.splice(idx, 1);
}
/** 切换参数类型时归一默认值，避免残留不匹配类型 */
function onParamTypeChange(p: CommonParam) {
  p.default =
    p.type === 'boolean' ? false : p.type === 'object' ? '{}' : undefined;
}

// ===== 模型标识注册表（后台已验证标识，按 类型×厂家 过滤，可下拉也可手动输入） =====
const registryOptions = ref<{ value: string; label: string }[]>([]);
const loadRegistry = async () => {
  const { provider, model_type } = base_form_data;
  if (!provider || !model_type) {
    registryOptions.value = [];
    return;
  }
  try {
    const list = (await api.GetRegistry({ provider, category: model_type })) || [];
    registryOptions.value = list.map((it) => ({
      value: it.model_name,
      label: it.model_name,
    }));
  } catch {
    registryOptions.value = [];
  }
};
// 切换厂家/类型时刷新已注册标识下拉
watch(
  () => [base_form_data.provider, base_form_data.model_type],
  () => loadRegistry(),
);

// 各厂商默认 base_url 集合（编辑回填时若命中默认值则视为「未自定义」，允许切换厂商时重新自动回填）
const PROVIDER_DEFAULT_URLS = new Set(
  Object.values(PROVIDER_DEFAULT_BASE_URL).filter(Boolean),
);
// 是否允许「选厂商自动回填 base_url」：编辑回填期间临时关闭，避免覆盖已存的自定义地址
const autoFillEnabled = ref(true);

const isEdit = computed(() => !!props.model);

// 外部提供商变化时同步（打开弹窗/编辑回填依赖）
watch(
  () => props.provider,
  (p) => {
    base_form_data.provider = p || '';
  },
);

// 选择厂商时自动回填该厂商默认 base_url：
// 仅当当前 base_url 为空或仍是某厂商默认值（未被用户自定义）时才覆盖，避免误改自定义地址
watch(
  () => base_form_data.provider,
  (p) => {
    if (!autoFillEnabled.value || !p) return;
    const cur = credential.base_url.trim();
    if (cur === '' || PROVIDER_DEFAULT_URLS.has(cur)) {
      credential.base_url = PROVIDER_DEFAULT_BASE_URL[p] || '';
    }
  },
);

// ==================== 逻辑 ====================

const loadDict = async () => {
  if (categoryOptions.value.length > 0) return;
  try {
    const data: any = await api.GetCategories();
    categoryOptions.value = data?.categories || [];
    providerOptions.value = data?.providers || [];
  } catch {
    message.error('分类字典加载失败');
  }
};

const resetForm = () => {
  activeTab.value = 'base-info';
  Object.assign(base_form_data, {
    name: '',
    model_type: '',
    model_name: '',
    provider: '',
    rate_limit_qps: 0,
    tutorial_md: '',
    status: true,
  });
  Object.assign(credential, {
    base_url: '',
    gateway_url: '',
    api_key: '',
  });
  Object.assign(advanced, {
    supports_stream: false,
    supports_thinking: false,
    stream_param: '',
    thinking_param: '',
  });
  commonParams.value = [];
};

// 规范化后端返回的常用参数（缺字段补默认，保证表格控件可用）
const normalizeParams = (raw: any): CommonParam[] => {
  if (!Array.isArray(raw)) return [];
  return raw.map((p) => ({
    name: p?.name || '',
    default: p?.default,
    desc: p?.desc || '',
    type: (p?.type || 'string') as CommonParamType,
  }));
};

// 回填编辑数据（详情接口：api_key/高级设置/参数 仅管理员可见）
const fillModel = (model: api.ModelDetailRep) => {
  base_form_data.name = model.name;
  base_form_data.model_type = model.category;
  base_form_data.model_name = model.model_name;
  base_form_data.provider = model.provider;
  base_form_data.rate_limit_qps = model.rate_limit_qps ?? 0;
  base_form_data.tutorial_md = model.tutorial_md || '';
  base_form_data.status = model.status;
  credential.base_url = model.base_url || '';
  credential.gateway_url = model.gateway_url || '';
  credential.api_key = model.api_key || '';
  advanced.supports_stream = !!model.supports_stream;
  advanced.supports_thinking = !!model.supports_thinking;
  advanced.stream_param = model.stream_param || '';
  advanced.thinking_param = model.thinking_param || '';
  commonParams.value = normalizeParams(model.common_params);
};

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    await loadDict();
    resetForm();
    base_form_data.provider = props.provider || '';
    if (props.model) {
      // 回填期间禁用自动填充，避免 provider 变化把已存 base_url 覆盖成默认值
      autoFillEnabled.value = false;
      fillModel(props.model);
      // 双保险：等弹窗内容渲染完（浏览器密码自动填充完成）后再刷一次凭据回填。
      // 否则 Chrome 会把登录页记住的密码自动填入「管理端密钥」输入框
      // （autofill 直接改 DOM、不触发 v-model），导致显示/传输的都是假 key
      await nextTick();
      fillModel(props.model);
      autoFillEnabled.value = true;
    }
    loadRegistry();
  },
);

const close = () => {
  testPanelOpen.value = false;
  emit('update:open', false);
};

/** 组装后端 ModelSaveRequest：常用参数过滤掉空行 */
const buildSavePayload = (): api.ModelSaveReq => {
  const cleanedParams = commonParams.value
    .filter((p) => p.name && p.name.trim())
    .map((p) => ({
      name: p.name.trim(),
      default: p.default,
      desc: p.desc || '',
      type: p.type,
    }));
  return {
    name: base_form_data.name,
    category: base_form_data.model_type as api.ModelSaveReq['category'],
    provider: base_form_data.provider as api.ModelSaveReq['provider'],
    model_name: base_form_data.model_name,
    base_url: credential.base_url,
    gateway_url: credential.gateway_url || undefined,
    api_key: credential.api_key,
    rate_limit_qps: base_form_data.rate_limit_qps || 0,
    supports_stream: advanced.supports_stream,
    supports_thinking: advanced.supports_thinking,
    stream_param: advanced.stream_param || undefined,
    thinking_param: advanced.thinking_param || undefined,
    common_params: cleanedParams,
    tutorial_md: base_form_data.tutorial_md,
    status: base_form_data.status,
  };
};

/** 基础必填校验（名称/类型/标识/提供商/真实地址/密钥）：返回首个缺失提示 */
const validateRequired = (): null | string => {
  if (!base_form_data.name) return '请输入模型名称';
  if (!base_form_data.model_type) return '请选择模型类型';
  if (!base_form_data.model_name) return '请输入模型标识';
  if (!base_form_data.provider) return '请选择提供商';
  if (!credential.base_url.trim()) return '请输入模型真实地址';
  if (!credential.api_key.trim()) return '请输入管理端密钥';
  return null;
};

const submit = async () => {
  const miss = validateRequired();
  if (miss) {
    message.warning(miss);
    return;
  }
  const payload = buildSavePayload();
  saving.value = true;
  try {
    if (props.model) {
      await api.UpdateObj(props.model.id, payload);
      message.success('保存成功');
    } else {
      await api.AddObj(payload);
      message.success('新增成功');
    }
    close();
    emit('success');
  } finally {
    saving.value = false;
  }
};

// ==================== 连通性测试（弹出 ModelTryPanel） ====================

/** 点击「测试」：校验必填后打开测试台（用当前未保存的表单参数） */
const openTest = () => {
  const miss = validateRequired();
  if (miss) {
    message.warning(miss);
    return;
  }
  testResult.value = null;
  testError.value = null;
  testPanelOpen.value = true;
};

/** ModelTryPanel 提交：携带 inputs/stream/thinking/params 走 SSE 调 /models/test，逐块实时显示 */
const onTryRun = async (payload: {
  inputs: Record<string, any>;
  params: Record<string, any>;
  stream: boolean;
  thinking: boolean;
}) => {
  testRunning.value = true;
  testError.value = null;
  testResult.value = null;
  let accContent = '';
  let accReasoning = '';
  try {
    const res = await api.TestModelStream(
      {
        category: base_form_data.model_type as api.ModelTestReq['category'],
        provider: base_form_data.provider as api.ModelTestReq['provider'],
        model_name: base_form_data.model_name,
        base_url: credential.base_url,
        api_key: credential.api_key,
        inputs: payload.inputs,
        stream: payload.stream,
        thinking: payload.thinking,
        params: payload.params,
      },
      (chunk) => {
        // 流式增量：逐块累积并回填结果区（模型面板读 result.data.content/reasoning 渲染）
        if (chunk.content) accContent += chunk.content;
        if (chunk.reasoningContent) accReasoning += chunk.reasoningContent;
        if (accContent || accReasoning) {
          testResult.value = {
            success: true,
            message: '输出中…',
            data: { content: accContent, reasoning: accReasoning || undefined },
          };
        }
      },
    );
    // 汇总帧覆盖增量态（含 urls/vectors/scores/latency 等完整产出）
    testResult.value = res || null;
  } catch (e: any) {
    testResult.value = null;
    testError.value = e?.message || '测试请求失败，请稍后重试';
  } finally {
    testRunning.value = false;
  }
};
</script>

<template>
  <a-modal
    :open="open"
    :title="isEdit ? `编辑模型：${model?.name}` : '添加模型'"
    width="680px"
    :confirm-loading="saving"
    :destroy-on-close="true"
    @cancel="close"
  >
    <a-tabs v-model:active-key="activeTab">
      <!-- 基础信息：name / model_type / model_name / provider -->
      <a-tab-pane key="base-info" tab="基础信息">
        <a-form layout="vertical" :model="base_form_data">
          <a-form-item label="模型名称" required>
            <a-input
              v-model:value="base_form_data.name"
              :maxlength="64"
              show-count
              placeholder="请输入模型名称"
            />
          </a-form-item>
          <a-form-item label="模型类型" required>
            <a-select
              v-model:value="base_form_data.model_type"
              :options="categoryOptions"
              placeholder="请选择模型类型（12 能力类型）"
            />
          </a-form-item>
          <a-form-item label="模型标识" required>
            <a-auto-complete
              v-model:value="base_form_data.model_name"
              :options="registryOptions"
              :maxlength="128"
              placeholder="下拉选择已验证的模型标识，或手动输入（API 调用时使用），如 glm-4-flash"
            />
          </a-form-item>
          <a-form-item label="提供商" required>
            <a-select
              v-model:value="base_form_data.provider"
              :options="providerOptions"
              placeholder="请选择提供商"
            />
          </a-form-item>
        </a-form>
      </a-tab-pane>

      <!-- 凭据信息（真实地址 / 密钥 必填） -->
      <a-tab-pane key="credential" tab="凭据信息">
        <a-form layout="vertical" :model="credential">
          <a-form-item label="模型网关地址">
            <a-input
              v-model:value="credential.gateway_url"
              placeholder="展示给调用方的网关地址，如 http://10.87.106.143:18000/api/model"
            />
          </a-form-item>
          <a-form-item label="模型真实地址（base_url）" required>
            <a-input
              v-model:value="credential.base_url"
              placeholder="厂商接口基础地址，如 https://open.bigmodel.cn/api/paas/v4/（选厂商自动填）"
            />
          </a-form-item>
          <a-form-item label="管理端密钥" required>
            <a-input-password
              v-model:value="credential.api_key"
              autocomplete="new-password"
              placeholder="模型管理端 API Key，仅管理员可见"
            />
          </a-form-item>
        </a-form>
      </a-tab-pane>

      <!-- 高级设置：流式/思考 + 参数键名 + 常用参数 + 限流 + 状态 -->
      <a-tab-pane key="advanced" tab="高级设置">
        <a-form layout="vertical" :model="advanced">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="开启流式输出">
                <a-switch
                  v-model:checked="advanced.supports_stream"
                  checked-children="开启"
                  un-checked-children="关闭"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="开启深度思考">
                <a-switch
                  v-model:checked="advanced.supports_thinking"
                  checked-children="开启"
                  un-checked-children="关闭"
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="开启流式的参数名">
                <a-input
                  v-model:value="advanced.stream_param"
                  :maxlength="64"
                  placeholder="默认 stream"
                  :disabled="!advanced.supports_stream"
                />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="开启思考的参数名">
                <a-input
                  v-model:value="advanced.thinking_param"
                  :maxlength="64"
                  placeholder="如 enable_thinking / thinking"
                  :disabled="!advanced.supports_thinking"
                />
              </a-form-item>
            </a-col>
          </a-row>

          <!-- 常用参数可编辑表 -->
          <a-form-item label="常用参数">
            <div class="param-edit">
              <div class="param-edit__head">
                <span class="pc-name">参数名</span>
                <span class="pc-type">类型</span>
                <span class="pc-val">默认值</span>
                <span class="pc-desc">说明</span>
                <span class="pc-op"></span>
              </div>
              <div v-for="(p, i) in commonParams" :key="i" class="param-edit__row">
                <a-input
                  v-model:value="p.name"
                  class="pc-name"
                  size="small"
                  :maxlength="64"
                  placeholder="如 temperature"
                />
                <a-select
                  v-model:value="p.type"
                  class="pc-type"
                  size="small"
                  :options="PARAM_TYPE_OPTIONS"
                  @change="onParamTypeChange(p)"
                />
                <span class="pc-val">
                  <a-switch
                    v-if="p.type === 'boolean'"
                    v-model:checked="p.default"
                    size="small"
                  />
                  <a-input-number
                    v-else-if="p.type === 'integer' || p.type === 'number'"
                    v-model:value="p.default"
                    size="small"
                    :precision="p.type === 'integer' ? 0 : undefined"
                    style="width: 100%"
                  />
                  <a-input
                    v-else
                    v-model:value="p.default"
                    size="small"
                    :placeholder="p.type === 'object' ? 'JSON' : '默认值'"
                  />
                </span>
                <a-input
                  v-model:value="p.desc"
                  class="pc-desc"
                  size="small"
                  :maxlength="255"
                  placeholder="参数说明"
                />
                <span class="pc-op">
                  <a-button
                    type="text"
                    danger
                    size="small"
                    @click="removeParam(i)"
                  >
                    <template #icon>
                      <DeleteOutlined />
                    </template>
                  </a-button>
                </span>
              </div>
              <a-button
                type="dashed"
                size="small"
                block
                style="margin-top: 8px"
                @click="addParam"
              >
                <template #icon>
                  <PlusOutlined />
                </template>
                添加参数
              </a-button>
            </div>
          </a-form-item>

          <!-- 限流 QPS：开关切换限制/不限制（不限制=灰，限制=蓝），复用启用状态开关样式 -->
          <a-form-item label="限流 QPS">
            <a-space>
              <a-switch
                v-model:checked="rateLimited"
                checked-children="限制"
                un-checked-children="不限制"
              />
              <a-input-number
                v-model:value="base_form_data.rate_limit_qps"
                :min="1"
                :disabled="!rateLimited"
                style="width: 160px"
                placeholder="每秒并发限制"
              />
            </a-space>
          </a-form-item>

          <a-form-item label="启用状态">
            <a-switch
              v-model:checked="base_form_data.status"
              checked-children="启用"
              un-checked-children="停用"
            />
          </a-form-item>
        </a-form>
      </a-tab-pane>
    </a-tabs>
    <template #footer>
      <a-space>
        <a-button @click="close">取消</a-button>
        <a-button @click="openTest">测试</a-button>
        <a-button type="primary" :loading="saving" @click="submit">
          确定
        </a-button>
      </a-space>
    </template>
  </a-modal>

  <!-- 测试台弹窗：内嵌 ModelTryPanel（携带当前未保存参数调 /models/test） -->
  <a-modal
    :open="testPanelOpen"
    title="模型测试"
    width="680px"
    :footer="null"
    :destroy-on-close="true"
    :get-container="getTestContainer"
    @cancel="testPanelOpen = false"
  >
    <ModelTryPanel
      :category="base_form_data.model_type"
      :supports-stream="advanced.supports_stream"
      :supports-thinking="advanced.supports_thinking"
      :common-params="commonParams"
      :running="testRunning"
      :show-result="true"
      :result="testResult"
      :result-error="testError"
      submit-text="运行测试"
      @run="onTryRun"
    />
  </a-modal>
</template>

<style scoped>
.param-edit {
  width: 100%;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 8px;
}

.param-edit__head,
.param-edit__row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 1.4fr 1.6fr 40px;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.param-edit__head {
  font-size: 12px;
  color: #8c8c8c;
}

.pc-op {
  text-align: center;
}
</style>
