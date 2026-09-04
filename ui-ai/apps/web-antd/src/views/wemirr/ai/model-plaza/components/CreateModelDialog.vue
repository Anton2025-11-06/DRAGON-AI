<script setup lang="ts">
import type { FormRules } from 'ant-design-vue';

/**
 * 添加/编辑模型弹窗
 * 保留 maxkb「添加模型」的数据结构设计：
 * - base_form_data：基础信息分组（name 模型名称 / model_type 模型类型 / model_name 模型标识）
 * - credential：凭据信息分组（base_url 接口地址 / api_key 管理端密钥）
 * - 高级设置：限流参数 + 使用教程 + 启用状态
 * 提交时展平为后端 ModelSaveRequest（category 即 maxkb 的 model_type）
 */
import { computed, nextTick, reactive, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import * as api from '../api';

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

// ==================== 常量 ====================

// ==================== 状态 ====================

const saving = ref(false);
const testing = ref(false);
const activeTab = ref('base-info');

// 连通性测试结果弹窗：展示调用状态（HTTP/耗时）与上游返回的 data 字段
const testModalOpen = ref(false);
const testResult = ref<api.ModelTestRep | null>(null);
/**
 * 测试结果弹窗挂载容器：显式挂到 body，避免被主弹窗内表单容器捕获导致定位错乱；
 * 防御非 DOM 环境（document 不可用）时返回 undefined，交由 antd 默认容器兜底
 */
const getTestModalContainer = () =>
  typeof document === 'undefined' ? undefined : document.body;
const formatTestData = computed(() => {
  const data = testResult.value?.data;
  if (data === undefined || data === null || data === '') {
    return '';
  }
  if (typeof data === 'string') {
    return data;
  }
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
});

// 分类/提供商字典（后端 /categories 接口）
const categoryOptions = ref<{ label: string; value: string }[]>([]);
const providerOptions = ref<{ label: string; value: string }[]>([]);

// ===== 基础信息（maxkb base_form_data） =====
const base_form_data = reactive({
  name: '',
  model_type: '', // 模型类型 = 后端 category
  model_name: '', // 模型标识
  provider: '', // 提供商（表单校验直接读 base_form_data，避免独立 ref 永远 undefined）
  rate_limit_qps: 0,
  tutorial_md: '',
  status: true,
});
// ===== 凭据信息（maxkb credential） =====
const credential = reactive({
  base_url: '',
  gateway_url: '',
  // 是否直连：直连=base_url 含完整接口路径（转发 /api/model）；非直连=base_url+接口后缀（转发 /api/model/{path}）
  is_direct: true,
  // 非直连时维护的接口后缀行：{ desc, url }
  suffixes: [] as { desc: string; url: string }[],
  api_key: '',
});

const addSuffix = () => credential.suffixes.push({ url: '', desc: '' });
const removeSuffix = (index: number) => credential.suffixes.splice(index, 1);

/** 接口后缀校验：非空行必须以 / 开头（失焦时单行提示，提交/测试时全量拦截） */
const validateSuffixUrl = (row?: { url: string }): boolean => {
  const rows = row ? [row] : credential.suffixes;
  const bad = rows.find((s) => {
    const url = s.url.trim();
    return url !== '' && !url.startsWith('/');
  });
  if (bad) {
    message.warning(
      `接口后缀「${bad.url.trim()}」必须以 / 开头，如 /v1/chat/completions`,
    );
    return false;
  }
  return true;
};

// 模型参数不做后台管理：调用方在 /api/model 请求体中直接指定，网关原样透传

const isEdit = computed(() => !!props.model);

// 外部提供商变化时同步（打开弹窗/编辑回填依赖）
watch(
  () => props.provider,
  (p) => {
    base_form_data.provider = p || '';
  },
);

const formRules: FormRules = {
  name: [{ required: true, message: '请输入模型名称' }],
  model_type: [{ required: true, message: '请选择模型类型' }],
  model_name: [{ required: true, message: '请输入模型标识' }],
  provider: [{ required: true, message: '请选择提供商' }],
};

// 模型标识：自由输入文本（与模型名称一致）

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
    is_direct: true,
    api_key: '',
  });
  credential.suffixes = [];
};

// 回填编辑数据（详情接口：api_key/教程 仅管理员可见）
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
  credential.is_direct = model.is_direct !== false;
  credential.suffixes = (model.suffixes || []).map((s) => ({
    url: s.url || '',
    desc: s.desc || '',
  }));
  // 非直连但未配置任何后缀时，默认给一行方便维护
  if (!credential.is_direct && credential.suffixes.length === 0) {
    credential.suffixes.push({ url: '', desc: '' });
  }
  credential.api_key = model.api_key || '';
};

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    await loadDict();
    resetForm();
    base_form_data.provider = props.provider || '';
    if (props.model) {
      fillModel(props.model);
      // 双保险：等弹窗内容渲染完（浏览器密码自动填充完成）后再刷一次凭据回填。
      // 否则 Chrome 会把登录页记住的密码自动填入「管理端密钥」输入框
      // （autofill 直接改 DOM、不触发 v-model），导致显示/传输的都是假 key
      await nextTick();
      fillModel(props.model);
    }
  },
);

const close = () => {
  // 关闭编辑弹窗时一并收起测试结果弹窗，避免残留到下一个弹窗周期
  testModalOpen.value = false;
  testResult.value = null;
  emit('update:open', false);
};

const submit = async () => {
  // 页面校验：名称/类型/标识/提供商
  if (!base_form_data.name) {
    message.warning('请输入模型名称');
    return;
  }
  if (!base_form_data.model_type) {
    message.warning('请选择模型类型');
    return;
  }
  if (!base_form_data.model_name) {
    message.warning('请输入模型标识');
    return;
  }
  if (!base_form_data.provider) {
    message.warning('请选择提供商');
    return;
  }
  // 非直连校验：至少一行有效后缀（url 非空）且每行必须以 / 开头
  if (!credential.is_direct) {
    const validSuffixes = credential.suffixes.filter((s) => s.url.trim());
    if (validSuffixes.length === 0) {
      message.warning('非直连模型请至少维护一个接口后缀');
      return;
    }
    if (!validateSuffixUrl()) {
      return;
    }
  }

  // 展平为后端 ModelSaveRequest：category = model_type
  // 直连时传空后缀列表，让后端清空历史维护的后缀（切回直连后不再校验）
  const payload: api.ModelSaveReq = {
    name: base_form_data.name,
    category: base_form_data.model_type,
    provider: base_form_data.provider,
    model_name: base_form_data.model_name,
    base_url: credential.base_url || undefined,
    gateway_url: credential.gateway_url || undefined,
    is_direct: credential.is_direct,
    suffixes: credential.is_direct
      ? []
      : credential.suffixes
          .filter((s) => s.url.trim())
          .map((s) => ({ url: s.url.trim(), desc: s.desc.trim() })),
    api_key: credential.api_key || undefined,
    rate_limit_qps: base_form_data.rate_limit_qps || 0,
    tutorial_md: base_form_data.tutorial_md || undefined,
    status: base_form_data.status,
  };
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

// ==================== 连通性测试 ====================

/**
 * 连通性测试：
 * - 直连：测试「模型真实地址」（suffixUrl 不传）
 * - 非直连：测试「基础地址 + 该行接口后缀」（suffixUrl 传该行 url）
 */
const runTest = async (suffixUrl?: string) => {
  // 测试仅依赖：分类 / 模型标识 / 接口地址 / 密钥
  if (!base_form_data.model_type) {
    message.warning('请先选择模型类型');
    return;
  }
  if (!base_form_data.model_name) {
    message.warning('请先填写模型标识');
    return;
  }
  if (!credential.base_url) {
    message.warning('请先填写模型真实地址');
    return;
  }
  // 非直连行测试：该行后缀非空且必须以 / 开头
  const suffix = (suffixUrl || '').trim();
  if (suffixUrl !== undefined) {
    if (!suffix) {
      message.warning('请先填写该行的接口后缀');
      return;
    }
    if (!suffix.startsWith('/')) {
      message.warning(
        `接口后缀「${suffix}」必须以 / 开头，如 /v1/chat/completions`,
      );
      return;
    }
  }
  const payload: api.ModelTestReq = {
    category: base_form_data.model_type,
    model_name: base_form_data.model_name,
    base_url: credential.base_url,
    api_key: credential.api_key || undefined,
    suffix_url: suffixUrl === undefined ? undefined : suffix,
  };
  testing.value = true;
  try {
    const res = await api.TestModel(payload);
    // 无论成功失败都弹出结果弹窗：除 message 外一并展示上游返回的 data 字段
    testResult.value = res;
    testModalOpen.value = true;
    if (res?.success) {
      message.success(res.message);
    } else {
      message.error(res?.message || '测试失败');
    }
  } catch {
    testResult.value = null;
    testModalOpen.value = false;
    message.error('测试请求失败，请稍后重试');
  } finally {
    testing.value = false;
  }
};
</script>

<template>
  <a-modal
    :open="open"
    :title="isEdit ? `编辑模型：${model?.name}` : '添加模型'"
    width="640px"
    :confirm-loading="saving"
    :destroy-on-close="true"
    @ok="submit"
    @cancel="close"
  >
    <a-tabs v-model:active-key="activeTab">
      <!-- 基础信息（maxkb base_form_data：name / model_type / model_name） -->
      <a-tab-pane key="base-info" tab="基础信息">
        <a-form layout="vertical" :model="base_form_data" :rules="formRules">
          <a-form-item label="模型名称" name="name" required>
            <a-input
              v-model:value="base_form_data.name"
              :maxlength="64"
              show-count
              placeholder="请输入模型名称"
            />
          </a-form-item>
          <a-form-item label="模型类型" name="model_type" required>
            <a-select
              v-model:value="base_form_data.model_type"
              :options="categoryOptions"
              placeholder="请选择模型类型"
            />
          </a-form-item>
          <a-form-item label="模型标识" name="model_name" required>
            <a-input
              v-model:value="base_form_data.model_name"
              :maxlength="128"
              show-count
              placeholder="模型标识（API 调用时使用），如 deepseek-chat"
            />
          </a-form-item>
          <a-form-item label="提供商" name="provider" required>
            <a-select
              v-model:value="base_form_data.provider"
              :options="providerOptions"
              placeholder="请选择提供商"
            />
          </a-form-item>
        </a-form>
      </a-tab-pane>

      <!-- 凭据信息（maxkb credential） -->
      <a-tab-pane key="credential" tab="凭据信息">
        <a-form layout="vertical" :model="credential">
          <a-form-item label="模型网关地址">
            <a-input
              v-model:value="credential.gateway_url"
              placeholder="展示给调用方的网关地址，如 http://10.87.106.143:18000/api/model"
            />
          </a-form-item>
          <a-form-item label="模型真实地址">
            <div style="display: flex; gap: 8px">
              <a-input
                v-model:value="credential.base_url"
                style="flex: 1"
                placeholder="直连：完整接口路径；非直连：接口基础地址，如 http://10.87.106.143:8000"
              />
              <!-- 直连：测试按钮跟随模型真实地址 -->
              <a-button
                v-if="credential.is_direct"
                :loading="testing"
                @click="runTest()"
              >
                测试
              </a-button>
            </div>
          </a-form-item>
          <a-form-item label="是否直连">
            <a-radio-group v-model:value="credential.is_direct">
              <a-radio :value="true">是（真实地址已是完整接口路径）</a-radio>
              <a-radio :value="false">否（基础地址 + 接口后缀转发）</a-radio>
            </a-radio-group>
          </a-form-item>
          <!-- 非直连：维护支持的接口后缀行（后缀 URI + 能力说明） -->
          <template v-if="!credential.is_direct">
            <a-form-item
              v-for="(row, index) in credential.suffixes"
              :key="index"
              :label="index === 0 ? '接口后缀' : ''"
              style="margin-bottom: 8px"
            >
              <a-row :gutter="8" align="middle">
                <a-col :span="8">
                  <a-input
                    v-model:value="row.url"
                    placeholder="后缀 URI，如 /v1/chat/completions"
                    @blur="validateSuffixUrl(row)"
                  />
                </a-col>
                <a-col :span="4">
                  <!-- 非直连：每个后缀行一个测试按钮（测该行接口） -->
                  <a-button :loading="testing" @click="runTest(row.url)">
                    测试
                  </a-button>
                </a-col>
                <a-col :span="10">
                  <a-input
                    v-model:value="row.desc"
                    placeholder="接口能力说明，如 对话接口"
                  />
                </a-col>
                <a-col :span="2" style="text-align: right">
                  <a-button
                    type="text"
                    danger
                    :disabled="credential.suffixes.length <= 1"
                    @click="removeSuffix(index)"
                  >
                    删除
                  </a-button>
                </a-col>
              </a-row>
            </a-form-item>
            <a-form-item>
              <a-button type="dashed" block @click="addSuffix">
                + 添加接口后缀
              </a-button>
            </a-form-item>
          </template>
          <a-form-item label="管理端密钥">
            <a-input-password
              v-model:value="credential.api_key"
              autocomplete="new-password"
              placeholder="模型管理端 API Key，仅管理员可见"
            />
          </a-form-item>
        </a-form>
      </a-tab-pane>

      <!-- 高级设置：限流 + 教程 + 状态 -->
      <a-tab-pane key="advanced" tab="高级设置">
        <a-form layout="vertical" :model="base_form_data">
          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="限流 QPS">
                <a-input-number
                  v-model:value="base_form_data.rate_limit_qps"
                  :min="0"
                  style="width: 100%"
                  placeholder="每秒并发限制，0 表示不限"
                />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="使用教程（Markdown）">
            <a-textarea
              v-model:value="base_form_data.tutorial_md"
              :rows="4"
              placeholder="填写模型使用教程，支持 Markdown 语法"
            />
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
        <a-button type="primary" :loading="saving" @click="submit">
          确定
        </a-button>
      </a-space>
    </template>
  </a-modal>

  <!-- 连通性测试结果弹窗：调用状态 + 接口返回的 data 字段（JSON）
       显式挂载到 body，避免被编辑弹窗内的 form 容器捕获导致定位错乱 -->
  <a-modal
    :open="testModalOpen"
    title="测试结果"
    width="640px"
    :footer="null"
    :destroy-on-close="true"
    :get-container="getTestModalContainer"
    @cancel="testModalOpen = false"
  >
    <template v-if="testResult">
      <a-alert
        :type="testResult.success ? 'success' : 'error'"
        show-icon
        :message="testResult.message"
        style="margin-bottom: 12px"
      />
      <template v-if="formatTestData">
        <div style="margin-bottom: 6px; font-weight: 500">
          接口返回内容（data）：
        </div>
        <pre class="test-result-data" v-text="formatTestData"></pre>
      </template>
    </template>
  </a-modal>
</template>

<style scoped>
.test-result-data {
  max-height: 280px;
  overflow: auto;
  margin: 0;
  padding: 8px 12px;
  border-radius: 4px;
  background: #f5f5f5;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
