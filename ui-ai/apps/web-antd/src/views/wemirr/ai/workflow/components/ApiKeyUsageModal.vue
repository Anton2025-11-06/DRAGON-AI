<script setup lang="ts">
/**
 * API Key 用法说明弹窗。
 *
 * 创建成功后与列表「查看」共用同一份：curl 模板写两遍必然漂移，这里只留一个来源。
 *
 * 说明按该工作流的真实入参生成，不套通用模板。入参表、请求体骨架、要不要先传文件，
 * 全部取自「当前发布版本快照」里 START 节点的字段定义——引擎对 API 触发只执行已发布
 * 版本（见 workflow_execution_service._prepare_execution），拿草稿字段出文档，调用方
 * 就会照一套线上根本不存在的入参发起调用。
 *
 * 入参含文件类型时多插一步「先上传再回填」：文件变量的值就是上传接口返回的那几个字段，
 * 这一段不能省，否则调用方只能猜（猜成 base64 或把文件塞进 multipart 都跑不通）。
 *
 * 步骤按对外契约排：submit 是唯一动作（新建 / 答审批 / 重跑都发它），暂停帧只报「停下了」，
 * 该怎么答要回详情取 pendingApprovals。口径见 /docs/workflow-execution-contract.md §1.2。
 */
import type { InputField, WorkflowGraph } from '#/api/ai-workflow/types';

import { computed, ref, watch } from 'vue';

import { CheckOutlined, CopyOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { getWorkflowDetail, getWorkflowVersion } from '#/api/ai-workflow';

import {
  getInputFieldTypeLabel,
  getMaxFileCount,
  getTextMaxLength,
  normalizeInputFieldType,
} from '../domain/input-field-type';

interface Props {
  /** 要展示的 API Key（明文） */
  apiKey: string;
  open: boolean;
  workflowId: number | string;
}

interface UsageStep {
  code: string;
  desc: string;
  title: string;
}

const props = defineProps<Props>();

const emit = defineEmits<{ (e: 'update:open', open: boolean): void }>();

const apiBaseUrl = `${window.location.origin}/api/workflow`;

// ==================== 开始节点入参（取发布快照） ====================

/** 说明所依据的字段清单 */
const fields = ref<InputField[]>([]);
/** 字段来源的版本号（0 = 从未发布） */
const sourceVersion = ref(0);
/** 未发布：字段只能取自草稿，而这条 API 调用会被引擎挡下 */
const notPublished = ref(false);
const loadingFields = ref(false);
/** 拉取失败时不铺空表，直接给原因 */
const fieldsError = ref('');

/**
 * 取 START 节点字段。
 * 字段可能在 data.config.fields 或 data.fields（两种历史写法，与编辑页/对话窗口同口径）。
 */
function pickStartFields(graph?: WorkflowGraph): InputField[] {
  const startNode = (graph?.nodes || []).find(
    (node) => node?.type === 'START' || node?.data?.nodeType === 'START',
  );
  return startNode?.data?.config?.fields || startNode?.data?.fields || [];
}

async function loadStartFields() {
  if (!props.workflowId) return;
  loadingFields.value = true;
  fieldsError.value = '';
  try {
    const detail = await getWorkflowDetail(props.workflowId);
    const version = Number(detail?.currentVersion) || 0;
    if (version <= 0) {
      notPublished.value = true;
      sourceVersion.value = 0;
      fields.value = pickStartFields(detail?.graph);
      return;
    }
    const snapshot = await getWorkflowVersion(props.workflowId, version);
    notPublished.value = false;
    sourceVersion.value = version;
    fields.value = pickStartFields(snapshot?.graphSnapshot);
  } catch {
    fieldsError.value = '读取开始节点入参失败（可能没有工作流查看权限）';
    fields.value = [];
  } finally {
    loadingFields.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) loadStartFields();
  },
  { immediate: true },
);

/** 文件类字段：值必须先上传才能填 */
const fileFields = computed(() =>
  fields.value.filter((field) => {
    const type = normalizeInputFieldType(field.type);
    return type === 'FILE_LIST' || type === 'SINGLE_FILE';
  }),
);

/** 是否存在多文件字段（决定要不要给批量上传示例） */
const hasMultiFileField = computed(() =>
  fileFields.value.some(
    (field) => normalizeInputFieldType(field.type) === 'FILE_LIST',
  ),
);

/** 入参表上方的来源说明：让读者知道这份文档是按哪版图生成的 */
const sourceText = computed(() => {
  if (loadingFields.value) return '正在读取开始节点入参…';
  if (fieldsError.value) return fieldsError.value;
  if (notPublished.value) return '来源：草稿（尚未发布）';
  if (sourceVersion.value > 0) {
    return `来源：已发布版本 v${sourceVersion.value} 的图快照`;
  }
  return fields.value.length === 0 ? '开始节点未配置入参' : '';
});

/** 顶栏告警：未发布 / 入参没读到——两种情况下方的请求体都不能直接照抄 */
const warnText = computed(() => {
  if (fieldsError.value) {
    return `${fieldsError.value}；下面步骤里的 values 是空壳，请刷新页面或到画布确认开始节点配置后再复制。`;
  }
  if (notPublished.value) {
    return '该工作流尚未发布，API 调用会被引擎挡下并提示「工作流尚未发布，请先发布后再执行」。下方入参取自草稿，仅供对齐配置；发布后请重新打开本说明核对一次。';
  }
  return '';
});

// ==================== 按字段生成取值示例 ====================

/** 字节数转可读大小（口径与动态输入表单的上传校验一致） */
function formatFileSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)}MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${size}B`;
}

/**
 * 文件入参的值骨架。
 * 键与上传返回体、DynamicInputForm 的 toFileVar 三方同一份，谁抄谁都不会漂移。
 */
function fileValueSample(): Record<string, unknown> {
  return {
    expiresAt: '<上传返回的 expiresAt>',
    fileName: '<上传返回的 fileName>',
    name: '<原始文件名>',
    size: 0,
    url: '<上传返回的 url>',
  };
}

/** 该字段的示例值（用于拼请求体骨架） */
function sampleValue(field: InputField): unknown {
  const type = normalizeInputFieldType(field.type);
  if (type === 'SINGLE_FILE') return fileValueSample();
  if (type === 'FILE_LIST') return [fileValueSample()];
  if (type === 'CHECKBOX') {
    return field.defaultValue === undefined
      ? true
      : Boolean(field.defaultValue);
  }
  if (type === 'NUMBER') return field.defaultValue ?? field.minValue ?? 0;
  if (type === 'SELECT') {
    return field.defaultValue || field.options?.[0] || '<从选项里单选一项>';
  }
  if (type === 'APPROVER') {
    return Array.isArray(field.defaultValue) && field.defaultValue.length > 0
      ? field.defaultValue
      : ['<审批人标识，数字或字符串>'];
  }
  return field.defaultValue ?? `<${field.label || field.name}>`;
}

/**
 * 取值说明：类型形态 + 该字段实际配的限制。
 * 限制项只在真的配了时才拼进来，避免给调用方一堆「≤不限」的噪声。
 */
function valueHint(field: InputField): string {
  const type = normalizeInputFieldType(field.type);
  const parts: string[] = [];

  switch (type) {
    case 'APPROVER': {
      parts.push('审批人标识数组，元素为数字或字符串，如 [1001]');

      break;
    }
    case 'CHECKBOX': {
      parts.push('布尔：true / false');

      break;
    }
    case 'FILE_LIST':
    case 'SINGLE_FILE': {
      parts.push(
        type === 'FILE_LIST'
          ? `文件对象数组，最多 ${getMaxFileCount(field)} 个`
          : '文件对象（单个）',
      );
      const types = (field.allowedFileTypes || []).filter(Boolean);
      if (types.length > 0) parts.push(`仅 ${types.join(' / ')}`);
      const maxSize = Number(field.maxFileSize);
      if (Number.isFinite(maxSize) && maxSize > 0) {
        parts.push(`单个≤${formatFileSize(maxSize)}`);
      }
      parts.push('先上传再回填，见下方「文件入参怎么填」');

      break;
    }
    case 'NUMBER': {
      parts.push('数字');
      const min = Number(field.minValue);
      const max = Number(field.maxValue);
      if (Number.isFinite(min) || Number.isFinite(max)) {
        parts.push(
          `范围 ${Number.isFinite(min) ? min : '不限'} ~ ${Number.isFinite(max) ? max : '不限'}`,
        );
      }

      break;
    }
    case 'SELECT': {
      parts.push(
        field.options && field.options.length > 0
          ? `单选一项：${field.options.join(' / ')}`
          : '单选一项的文本',
      );

      break;
    }
    default: {
      parts.push(`文本，≤${getTextMaxLength(field)} 字符`);
      if (field.pattern) parts.push(`需匹配正则 ${field.pattern}`);
    }
  }

  if (
    type !== 'SINGLE_FILE' &&
    type !== 'FILE_LIST' &&
    field.defaultValue !== null &&
    field.defaultValue !== undefined &&
    field.defaultValue !== ''
  ) {
    parts.push(`默认值 ${JSON.stringify(field.defaultValue)}`);
  }
  return parts.join('；');
}

/** 字段说明（与变量名/标题同名的历史回填值不重复展示，口径同动态输入表单） */
function fieldDescription(field: InputField): string {
  const desc = (field.description || '').trim();
  if (!desc) return '';
  if (
    desc === (field.label || '').trim() ||
    desc === (field.name || '').trim()
  ) {
    return '';
  }
  return desc;
}

/** 业务输入骨架（开始节点配的那些字段） */
const bodyValues = computed<Record<string, unknown>>(() => {
  const acc: Record<string, unknown> = {};
  fields.value.forEach((field) => {
    if (field?.name) acc[field.name] = sampleValue(field);
  });
  return acc;
});

/** 新建执行的请求体：顶层只有 workflowId 与 values 两个业务键 */
const newBody = computed(() => ({
  values: bodyValues.value,
  workflowId: props.workflowId,
}));

/** 请求体（展开版，供改值） */
const bodyText = computed(() => JSON.stringify(newBody.value, null, 2));

/** 请求体（单行版，供 curl 的 -d） */
const bodyInline = computed(() => JSON.stringify(newBody.value));

/** 只取业务输入那一层（同一会话换输入重跑时只要它） */
const valuesInline = computed(() => JSON.stringify(bodyValues.value));

// ==================== 调用步骤 ====================

/** 上传示例文件名：跟着字段配的类型白名单走，没配就给个中性占位 */
const uploadSampleName = computed(() => {
  const raw = fileFields.value[0]?.allowedFileTypes?.[0] || '';
  const ext = raw.startsWith('.') ? raw.slice(1) : raw;
  return ext ? `sample.${ext}` : 'sample.bin';
});

/** 批量示例的第二个文件：同类型换个名字，只为说明这是两个文件 */
const uploadSampleName2 = computed(() => `another-${uploadSampleName.value}`);

function uploadStep(): UsageStep {
  const code: string[] = [
    '# 单文件：multipart 表单字段名固定为 file',
    `curl -X POST ${apiBaseUrl}/workflow-files/upload \\`,
    `  -F "file=@${uploadSampleName.value}"`,
  ];
  if (hasMultiFileField.value) {
    code.push(
      '',
      '# 多文件字段走批量接口：字段名为 files，可重复多次',
      `curl -X POST ${apiBaseUrl}/workflow-files/upload-batch \\`,
      `  -F "files=@${uploadSampleName.value}" \\`,
      `  -F "files=@${uploadSampleName2.value}"`,
    );
  }
  return {
    code: code.join('\n'),
    desc: '入参里有文件类型字段时才需要。此接口在网关匿名白名单内，不要带 X-Workflow-Token（带该头时网关只放行提交/查看现状/订阅/取消，上传会吃 403）。',
    title: '上传文件，换取文件入参的值',
  };
}

/** 各步调用：编号 + 说明 + 可直接粘贴的命令。入参含文件字段时，上传就是第 1 步。 */
const steps = computed<UsageStep[]>(() => [
  ...(fileFields.value.length > 0 ? [uploadStep()] : []),
  {
    code: [
      `curl -X POST ${apiBaseUrl}/workflow-executions/submit \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '${bodyInline.value}'`,
    ].join('\n'),
    desc: '对外只有这一个动作：新建会话、答审批、重跑、重开全部 POST 到这里，请求体里给了哪几个键就是哪个意图。workflowId 可以省略（网关按这个 key 绑定的工作流补上），传了就必与之一致。返回 data 里的 executionId 是此后唯一的会话标识；只执行已发布版本快照。',
    title: '提交执行（新建会话）',
  },
  {
    code: `curl -N ${apiBaseUrl}/workflow-executions/<executionId>/subscribe`,
    desc: 'SSE 长连接，网关白名单放行，无需携带请求头；结束帧 event: workflow.completed 的 data.outputs 就是返回值。暂停帧 node.paused / workflow.paused 只报「本轮跑完了、停在第几代」（pauseGeneration），不带你该怎么答。',
    title: '订阅执行事件流，取返回值',
  },
  {
    code: [
      `curl -X GET ${apiBaseUrl}/workflow-executions/<executionId> \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}"`,
    ].join('\n'),
    desc: '收到暂停帧后调这一步：data.pendingApprovals 每份待办给一个 approvalToken（答复凭据）和一份 editableFields（这份待办允许改哪几个字段）。没在等人时是空数组；重连、晚订阅、事后补答都只能从这里取凭据。',
    title: '查看现状，取待办与 approvalToken',
  },
  {
    code: [
      `curl -X POST ${apiBaseUrl}/workflow-executions/submit \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"executionId": "<executionId>", "decisions": [{"approvalToken": "<待办的 approvalToken>", "action": "APPROVE", "fieldValues": {"<editableFields 里的字段名>": "改后的值"}, "opinion": "同意"}]}'`,
    ].join('\n'),
    desc: 'decisions 每份待办一条，靠 approvalToken 定位（不需要知道是哪个节点）。action 只有 APPROVE / REJECT 两个词，两者都会继续跑下游，区别只是记下的结论；fieldValues 只回传改过的字段，键取 editableFields[].name。同一份 approvalToken 再提交会回 200 且 duplicated 为 true、后台不做第二次提交。',
    title: '答复审批并恢复执行',
  },
  {
    code: [
      `curl -X POST ${apiBaseUrl}/workflow-executions/submit \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"executionId": "<executionId>", "values": ${valuesInline.value}}'`,
    ].join('\n'),
    desc: '沿用同一个 executionId（会话与记忆连续），带新 values 从 START 全量重跑；把 values 整个省掉就是原样重跑；改带 "restart": true 则作废未答的审批后重开（带未答审批时直接发 values 会被挡下）。',
    title: '同一会话换输入重跑',
  },
  {
    code: [
      `curl -X POST ${apiBaseUrl}/workflow-executions/<executionId>/cancel \\`,
      `  -H "X-Workflow-Token: ${props.apiKey}"`,
    ].join('\n'),
    desc: '上一条还在执行中（提交会吃 409）时的解套手段；取消后未答复的 approvalToken 全部失效。',
    title: '取消执行',
  },
]);

/** 上传返回体示例（外层是平台统一响应体，取 data 里的字段） */
const uploadResponseSample = computed(() =>
  [
    `{`,
    `  "code": 200,`,
    `  "message": "上传成功",`,
    `  "data": {`,
    `    "ok": true,`,
    `    "fileName": "tmp/<32位hex>_${uploadSampleName.value}",`,
    `    "name": "${uploadSampleName.value}",`,
    `    "size": 20480,`,
    `    "url": "${apiBaseUrl}/workflow-files/download/tmp/<32位hex>_${uploadSampleName.value}",`,
    `    "expiresIn": <url 有效期秒数，取 storage.url_expires>,`,
    `    "expiresAt": "<url 的失效时间>"`,
    `  }`,
    `}`,
  ].join('\n'),
);

/**
 * 「文件入参怎么填」的条目。
 *
 * 文案放在脚本里而不是模板里：prettier 会在中文句子里换行，而换行经 HTML 渲染就是
 * 一个空格，读者会看到「开始节点 配的」这种断不开的句子；字符串字面量它不会拆。
 */
const fileGuideItems: string[] = [
  `先调上传接口拿文件：POST ${apiBaseUrl}/workflow-files/upload，multipart 表单字段名固定为 file（批量接口 upload-batch 用 files，可重复多次）。`,
  '平台侧只卡两条硬限制：单文件不超过 100MB、空文件会被拒。开始节点配的「文件类型白名单」与「单个文件大小上限」只在页面表单里校验，走 API 直传后端不会替你拦，得自己按上表的限制准备文件。',
  '上传成功后，把返回 data 里的 url / fileName / name / size / expiresAt 这五个键原样组成一个对象，作为该入参的值——引擎与下游节点（如文档提取器）按 url 读文件内容，fileName 供下载与删除。',
  '多文件类型的字段传这种对象组成的数组，个数受该字段配的「最多文件数量」限制。',
  'expiresAt 是 url 的失效时间（OSS 后端给的是预签名地址，到期即不可访问）；发现过期就重新上传一次并把新值回填进 values。',
];

const eventTypes = [
  'node.started',
  'node.completed',
  'node.delta',
  // 大模型节点插入工具后的调用过程：tool_call 是模型要求调哪个工具/传了什么参数，
  // tool_result 是这一次调用的结果摘要（完整结果在节点输出的 toolCalls 里）
  'node.tool_call',
  'node.tool_result',
  'node.failed',
  'node.paused',
  'workflow.paused',
  'workflow.resumed',
  'workflow.completed',
  'workflow.failed',
  'workflow.cancelled',
];

/** 刚复制过的块（步骤用 step-{i}，其余块用各自标识），用于把图标换成对勾 */
const copiedKey = ref<string>('');

function copyText(text: string, key: string = '') {
  navigator.clipboard
    .writeText(text)
    .then(() => {
      message.success('已复制到剪贴板');
      if (key) {
        copiedKey.value = key;
        setTimeout(() => {
          copiedKey.value = '';
        }, 1500);
      }
    })
    .catch(() => {
      message.error('复制失败，请手动复制');
    });
}

function handleClose(open: boolean) {
  emit('update:open', open);
}
</script>

<template>
  <a-modal
    :footer="null"
    :open="props.open"
    :width="760"
    title="API Key 用法说明"
    @update:open="handleClose"
  >
    <!-- Key 本体：整行放得下就不换行，复制按钮固定在右侧 -->
    <div class="usage-key">
      <span class="usage-key-label">API Key</span>
      <code class="usage-key-value">{{ props.apiKey }}</code>
      <a-button size="small" @click="copyText(props.apiKey)">
        <CopyOutlined /> 复制
      </a-button>
    </div>

    <!-- 未发布 / 入参读不到：下面的请求体不能直接照抄，顶在前面说清楚 -->
    <div v-if="warnText" class="usage-warn">{{ warnText }}</div>

    <!-- ============ 输入参数：来自该工作流开始节点的真实定义 ============ -->
    <div class="usage-section">
      <div class="section-head">
        <span class="section-title">输入参数</span>
        <span class="section-sub">{{ sourceText }}</span>
      </div>

      <div
        v-if="!loadingFields && fields.length === 0 && !fieldsError"
        class="section-empty"
      >
        开始节点没有配置入参，请求体里的 values 传
        <code>{}</code>
        即可。
      </div>

      <table v-if="fields.length > 0" class="param-table">
        <thead>
          <tr>
            <th>变量名</th>
            <th>类型</th>
            <th>必填</th>
            <th>取值说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="field in fields" :key="field.name">
            <td>
              <code>{{ field.name }}</code>
            </td>
            <td>{{ getInputFieldTypeLabel(field.type) }}</td>
            <td>
              <span
                :class="field.required ? 'param-required' : 'param-optional'"
              >
                {{ field.required ? '必填' : '可选' }}
              </span>
            </td>
            <td>
              <div class="param-hint">{{ valueHint(field) }}</div>
              <div v-if="fieldDescription(field)" class="param-desc">
                {{ fieldDescription(field) }}
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="fields.length > 0" class="usage-block">
        <div class="block-head">
          <span class="block-title">请求体（展开版，改值用）</span>
          <a class="block-copy" @click="copyText(bodyText, 'body')">
            <CheckOutlined v-if="copiedKey === 'body'" />
            <CopyOutlined v-else />
            复制
          </a>
        </div>
        <pre class="block-code"><code>{{ bodyText }}</code></pre>
      </div>
    </div>

    <!-- ============ 文件入参：先上传，再把返回字段当成值 ============ -->
    <div v-if="fileFields.length > 0" class="usage-section">
      <div class="section-head">
        <span class="section-title">文件入参怎么填</span>
        <span class="section-sub">
          涉及字段：{{ fileFields.map((field) => field.name).join('、') }}
        </span>
      </div>
      <ol class="file-guide">
        <li v-for="(item, index) in fileGuideItems" :key="index">{{ item }}</li>
      </ol>
      <div class="usage-block">
        <div class="block-head">
          <span class="block-title">上传接口返回体</span>
          <a class="block-copy" @click="copyText(uploadResponseSample, 'resp')">
            <CheckOutlined v-if="copiedKey === 'resp'" />
            <CopyOutlined v-else />
            复制
          </a>
        </div>
        <pre class="block-code"><code>{{ uploadResponseSample }}</code></pre>
      </div>
    </div>

    <div class="usage-steps">
      <div v-for="(step, index) in steps" :key="index" class="usage-step">
        <div class="step-head">
          <span class="step-no">{{ index + 1 }}</span>
          <div class="step-names">
            <div class="step-title">{{ step.title }}</div>
            <div class="step-desc">{{ step.desc }}</div>
          </div>
          <a class="step-copy" @click="copyText(step.code, `step-${index}`)">
            <CheckOutlined v-if="copiedKey === `step-${index}`" />
            <CopyOutlined v-else />
            复制
          </a>
        </div>
        <pre class="step-code"><code>{{ step.code }}</code></pre>
      </div>
    </div>

    <div class="usage-events">
      <div class="events-title">事件类型</div>
      <div class="events-tags">
        <a-tag v-for="item in eventTypes" :key="item">{{ item }}</a-tag>
      </div>
    </div>
  </a-modal>
</template>

<style scoped lang="less">
.usage-key {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 16px;
  background: #f5f7fa;
  border: 1px solid #e8e8e8;
  border-radius: 8px;

  .usage-key-label {
    flex-shrink: 0;
    font-size: 12px;
    color: #8c8c8c;
  }

  .usage-key-value {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 13px;
    color: #17415e;
  }
}

.usage-warn {
  padding: 10px 12px;
  margin-bottom: 16px;
  font-size: 12px;
  line-height: 20px;
  color: #ad4e00;
  background: #fff7e6;
  border: 1px solid #ffd591;
  border-radius: 8px;
}

.usage-section {
  padding-top: 14px;
  margin-bottom: 16px;
  border-top: 1px dashed #e8e8e8;

  .section-head {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
    align-items: baseline;
    margin-bottom: 8px;
  }

  .section-title {
    font-size: 13px;
    font-weight: 600;
    color: #262626;
  }

  .section-sub {
    font-size: 12px;
    color: #8c8c8c;
  }

  .section-empty {
    font-size: 12px;
    line-height: 20px;
    color: #8c8c8c;
  }
}

.param-table {
  width: 100%;
  margin-bottom: 12px;
  font-size: 12px;
  border-collapse: collapse;

  th,
  td {
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
    border-bottom: 1px solid #f0f0f0;
  }

  th {
    font-weight: 500;
    color: #8c8c8c;
    white-space: nowrap;
    background: #fafafa;
  }

  code {
    padding: 0;
    color: #17415e;
    background: none;
  }

  .param-required {
    color: #cf1322;
  }

  .param-optional {
    color: #8c8c8c;
  }

  .param-hint {
    line-height: 20px;
    color: #595959;
  }

  .param-desc {
    margin-top: 2px;
    line-height: 18px;
    color: #8c8c8c;
  }
}

.file-guide {
  padding-left: 20px;
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 22px;
  color: #595959;
}

.usage-block {
  .block-head {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-bottom: 6px;

    .block-title {
      flex: 1;
      min-width: 0;
      font-size: 12px;
      color: #8c8c8c;
    }

    .block-copy {
      flex-shrink: 0;
      font-size: 12px;
      color: #8c8c8c;

      &:hover {
        color: #1890ff;
      }
    }
  }
}

// 深色代码块：请求体/返回体与下方 curl 步骤同一套底色
.block-code,
.usage-step .step-code {
  padding: 10px 12px;
  margin: 0;
  overflow-x: auto;
  // 这块定的是深底白字：一旦被全局 pre 样式盖成浅底，白字就直接隐形了，故显式钉住底色
  background: #1e1e1e !important;
  border-radius: 8px;

  code {
    padding: 0;
    font-size: 12px;
    line-height: 20px;
    color: #fff;
    // 内联 code 只要带底色，就会在文字背后垫出一条浅色高亮（不是整块），
    // 叠上白字就是看到的“白底”，必须压成透明
    background: none !important;
    white-space: pre;
  }
}

.usage-steps {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.usage-step {
  .step-head {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    margin-bottom: 6px;

    .step-no {
      display: flex;
      flex-shrink: 0;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      margin-top: 1px;
      font-size: 12px;
      color: #fff;
      background: #1890ff;
      border-radius: 50%;
    }

    .step-names {
      flex: 1;
      min-width: 0;
    }

    .step-title {
      font-size: 13px;
      font-weight: 600;
      line-height: 20px;
      color: #262626;
    }

    .step-desc {
      font-size: 12px;
      color: #8c8c8c;
    }

    .step-copy {
      flex-shrink: 0;
      font-size: 12px;
      color: #8c8c8c;

      &:hover {
        color: #1890ff;
      }
    }
  }
}

.usage-events {
  padding-top: 14px;
  margin-top: 16px;
  border-top: 1px dashed #e8e8e8;

  .events-title {
    margin-bottom: 8px;
    font-size: 12px;
    color: #8c8c8c;
  }

  .events-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 0;
  }
}
</style>
