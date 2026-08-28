<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 模板引擎选择 -->
    <a-form-item label="模板引擎">
      <a-radio-group
        v-model:value="formData.engine"
        button-style="solid"
        @change="handleChange"
      >
        <a-radio-button value="JINJA2">Jinja2</a-radio-button>
        <a-radio-button value="FREEMARKER">Freemarker</a-radio-button>
        <a-radio-button value="SIMPLE">简单替换</a-radio-button>
      </a-radio-group>
      <div class="form-hint">
        {{ getEngineHint() }}
      </div>
    </a-form-item>

    <!-- 模板内容 -->
    <a-form-item label="模板内容" required>
      <a-textarea
        v-model:value="formData.template"
        :rows="10"
        :placeholder="getTemplatePlaceholder()"
        class="template-editor"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 输入变量列表 -->
    <a-form-item label="输入变量">
      <div class="variable-list">
        <div
          v-for="(variable, index) in formData.variables"
          :key="index"
          class="variable-item"
        >
          <a-input
            v-model:value="variable.name"
            placeholder="变量名"
            style="width: 100px"
            @change="handleChange"
          />
          <VariableInput
            v-model="variable.reference"
            :current-node-id="nodeId"
            placeholder="选择变量引用"
            style="flex: 1"
            @change="handleChange"
          />
          <a-input
            v-model:value="variable.defaultValue"
            placeholder="默认值"
            style="width: 100px"
            @change="handleChange"
          />
          <a-button
            type="text"
            danger
            size="small"
            @click="removeVariable(index)"
          >
            <DeleteOutlined />
          </a-button>
        </div>
        <a-button type="dashed" block @click="addVariable">
          <PlusOutlined /> 添加变量
        </a-button>
      </div>
      <div class="form-hint">
        定义模板中使用的变量，变量名将在模板中通过
        <code v-pre>{{ name }}</code> 引用
      </div>
    </a-form-item>

    <!-- 输出变量名 -->
    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="template_output"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 高级设置 -->
    <a-collapse ghost>
      <a-collapse-panel key="advanced" header="高级设置">
        <!-- 转义 HTML -->
        <a-form-item>
          <a-checkbox
            v-model:checked="formData.escapeHtml"
            @change="handleChange"
          >
            转义 HTML 字符
          </a-checkbox>
          <div class="form-hint">启用后将转义输出中的 HTML 特殊字符</div>
        </a-form-item>

        <!-- 去除空白 -->
        <a-form-item>
          <a-checkbox
            v-model:checked="formData.trimWhitespace"
            @change="handleChange"
          >
            去除首尾空白
          </a-checkbox>
          <div class="form-hint">启用后将去除输出结果的首尾空白字符</div>
        </a-form-item>

        <!-- 严格模式 -->
        <a-form-item>
          <a-checkbox
            v-model:checked="formData.strictMode"
            @change="handleChange"
          >
            严格模式
          </a-checkbox>
          <div class="form-hint">
            启用后，引用不存在的变量将报错而非输出空值
          </div>
        </a-form-item>
      </a-collapse-panel>
    </a-collapse>

    <!-- 模板语法说明 -->
    <a-alert type="info" show-icon class="syntax-help">
      <template #message>{{ getEngineName() }} 语法说明</template>
      <template #description>
        <div class="syntax-examples">
          <template v-if="formData.engine === 'JINJA2'">
            <div class="example-item">
              <code v-pre>{{ name }}</code>
              <span>输出变量值</span>
            </div>
            <div class="example-item">
              <code v-pre>{% if condition %}...{% endif %}</code>
              <span>条件判断</span>
            </div>
            <div class="example-item">
              <code v-pre>{% for item in list %}...{% endfor %}</code>
              <span>循环遍历</span>
            </div>
            <div class="example-item">
              <code v-pre>{{ name | upper }}</code>
              <span>过滤器</span>
            </div>
          </template>
          <template v-else-if="formData.engine === 'FREEMARKER'">
            <div class="example-item">
              <code>${name}</code>
              <span>输出变量值</span>
            </div>
            <div class="example-item">
              <code>&lt;#if condition&gt;...&lt;/#if&gt;</code>
              <span>条件判断</span>
            </div>
            <div class="example-item">
              <code>&lt;#list items as item&gt;...&lt;/#list&gt;</code>
              <span>循环遍历</span>
            </div>
          </template>
          <template v-else>
            <div class="example-item">
              <code v-pre>{{ name }}</code>
              <span>简单变量替换</span>
            </div>
          </template>
        </div>
      </template>
    </a-alert>
  </a-form>
</template>

<script setup lang="ts">
/**
 * 模板转换节点配置表单
 * 使用 Jinja2/Freemarker 模板转换数据
 */
import type {
  TemplateEngine,
  TemplateNodeConfig,
  TemplateVariable,
} from '#/api/ai-workflow/types';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { reactive, watch } from 'vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: TemplateNodeConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: TemplateNodeConfig): void;
}>();

// 表单数据
const formData = reactive<TemplateNodeConfig>({
  engine: 'JINJA2' as TemplateEngine,
  template: '',
  variables: [],
  outputVariable: 'template_output',
  escapeHtml: false,
  trimWhitespace: true,
  strictMode: false,
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      engine: config.engine || 'JINJA2',
      template: config.template || '',
      variables: config.variables || [],
      outputVariable: config.outputVariable || 'template_output',
      escapeHtml: config.escapeHtml ?? false,
      trimWhitespace: config.trimWhitespace ?? true,
      strictMode: config.strictMode ?? false,
    });
  },
  { immediate: true, deep: true },
);

// 获取引擎提示
function getEngineHint(): string {
  switch (formData.engine) {
    case 'JINJA2':
      return 'Jinja2 是 Python 风格的模板引擎，支持丰富的语法和过滤器';
    case 'FREEMARKER':
      return 'Freemarker 是 Java 风格的模板引擎，适合复杂的数据处理';
    case 'SIMPLE':
      return '简单替换模式，仅支持 {{变量名}} 格式的变量替换';
    default:
      return '';
  }
}

// 获取引擎名称
function getEngineName(): string {
  switch (formData.engine) {
    case 'JINJA2':
      return 'Jinja2';
    case 'FREEMARKER':
      return 'Freemarker';
    case 'SIMPLE':
      return '简单替换';
    default:
      return '模板';
  }
}

// 获取模板占位符
function getTemplatePlaceholder(): string {
  switch (formData.engine) {
    case 'JINJA2':
      return `# Jinja2 模板示例
你好，{{ name }}！

{% if items %}
你的订单包含以下商品：
{% for item in items %}
- {{ item.name }}: ¥{{ item.price }}
{% endfor %}
{% endif %}

总计: ¥{{ total | default(0) }}`;
    case 'FREEMARKER':
      return `<#-- Freemarker 模板示例 -->
你好，\${name}！

<#if items?has_content>
你的订单包含以下商品：
<#list items as item>
- \${item.name}: ¥\${item.price}
</#list>
</#if>

总计: ¥\${total!0}`;
    case 'SIMPLE':
      return `简单替换模板示例
你好，{{name}}！
你的订单号是：{{orderId}}`;
    default:
      return '';
  }
}

// 添加变量
function addVariable() {
  if (!formData.variables) {
    formData.variables = [];
  }
  formData.variables.push({
    name: '',
    reference: '',
    defaultValue: undefined,
    type: 'string',
  } as TemplateVariable);
  handleChange();
}

// 移除变量
function removeVariable(index: number) {
  formData.variables?.splice(index, 1);
  handleChange();
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

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

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .template-editor {
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 12px;
    line-height: 1.5;
  }

  .variable-list {
    display: flex;
    flex-direction: column;
    gap: 8px;

    .variable-item {
      display: flex;
      gap: 8px;
      align-items: center;
    }
  }

  :deep(.ant-collapse-header) {
    padding: 8px 0 !important;
    font-size: 12px;
    color: #595959;
  }

  :deep(.ant-collapse-content-box) {
    padding: 0 !important;
  }

  .syntax-help {
    margin-top: 16px;

    .syntax-examples {
      display: flex;
      flex-direction: column;
      gap: 8px;

      .example-item {
        display: flex;
        gap: 12px;
        align-items: center;

        code {
          padding: 2px 6px;
          font-family: monospace;
          font-size: 11px;
          background-color: #f5f5f5;
          border-radius: 3px;
        }

        span {
          font-size: 12px;
          color: #595959;
        }
      }
    }
  }
}
</style>
