<script setup lang="ts">
/**
 * 列表操作节点配置表单
 * 对数组进行过滤、排序、切片、提取等操作
 */
import type {
  ListCompareOperator,
  ListConcatConfig,
  ListExtractConfig,
  ListFilterCondition,
  ListFilterConfig,
  ListLimitConfig,
  ListLogicalOperator,
  ListOperationType,
  ListOperatorConfig,
  ListSliceConfig,
  ListSortConfig,
  ListUniqueConfig,
  SortDirection,
} from '#/api/ai-workflow/types';

import { reactive, watch } from 'vue';

import {
  ColumnWidthOutlined,
  DeleteOutlined,
  ExportOutlined,
  FilterOutlined,
  MenuUnfoldOutlined,
  MergeCellsOutlined,
  NumberOutlined,
  PlusOutlined,
  ScissorOutlined,
  SortAscendingOutlined,
  SortDescendingOutlined,
  SwapOutlined,
  TagOutlined,
  VerticalAlignBottomOutlined,
  VerticalAlignTopOutlined,
} from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: ListOperatorConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: ListOperatorConfig): void;
}>();

// 默认配置
const defaultFilterConfig: ListFilterConfig = {
  conditions: [],
  operator: 'AND' as ListLogicalOperator,
};

const defaultSortConfig: ListSortConfig = {
  field: '',
  direction: 'ASC' as SortDirection,
  ignoreCase: false,
};

const defaultSliceConfig: ListSliceConfig = {
  start: 0,
  end: undefined,
  step: 1,
};

const defaultExtractConfig: ListExtractConfig = {
  fields: [],
  flatten: false,
};

const defaultUniqueConfig: ListUniqueConfig = {
  field: '',
  keepStrategy: 'FIRST',
};

const defaultLimitConfig: ListLimitConfig = {
  count: 10,
  offset: 0,
};

const defaultConcatConfig: ListConcatConfig = {
  otherArrays: [],
  removeDuplicates: false,
};

type ListOperatorFormData = Omit<
  ListOperatorConfig,
  | 'concatConfig'
  | 'extractConfig'
  | 'filterConfig'
  | 'inputVariable'
  | 'limitConfig'
  | 'operationType'
  | 'outputVariable'
  | 'sliceConfig'
  | 'sortConfig'
  | 'uniqueConfig'
> & {
  concatConfig: ListConcatConfig;
  extractConfig: ListExtractConfig;
  filterConfig: ListFilterConfig;
  inputVariable: string;
  limitConfig: ListLimitConfig;
  operationType: ListOperationType;
  outputVariable: string;
  sliceConfig: ListSliceConfig;
  sortConfig: ListSortConfig;
  uniqueConfig: ListUniqueConfig;
};

// 表单数据
const formData = reactive<ListOperatorFormData>({
  inputVariable: '',
  operationType: 'FILTER' as ListOperationType,
  outputVariable: 'list_result',
  filterConfig: { ...defaultFilterConfig },
  sortConfig: { ...defaultSortConfig },
  sliceConfig: { ...defaultSliceConfig },
  extractConfig: { ...defaultExtractConfig },
  uniqueConfig: { ...defaultUniqueConfig },
  limitConfig: { ...defaultLimitConfig },
  concatConfig: { ...defaultConcatConfig },
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    Object.assign(formData, {
      inputVariable: config.inputVariable || '',
      operationType: config.operationType || 'FILTER',
      outputVariable: config.outputVariable || 'list_result',
      filterConfig: { ...defaultFilterConfig, ...config.filterConfig },
      sortConfig: { ...defaultSortConfig, ...config.sortConfig },
      sliceConfig: { ...defaultSliceConfig, ...config.sliceConfig },
      extractConfig: { ...defaultExtractConfig, ...config.extractConfig },
      uniqueConfig: { ...defaultUniqueConfig, ...config.uniqueConfig },
      limitConfig: { ...defaultLimitConfig, ...config.limitConfig },
      concatConfig: { ...defaultConcatConfig, ...config.concatConfig },
    });
  },
  { immediate: true, deep: true },
);

// 获取操作提示
function getOperationHint(): string {
  const hints: Record<string, string> = {
    FILTER: '根据条件筛选数组元素',
    SORT: '按指定字段对数组排序',
    SLICE: '截取数组的一部分',
    EXTRACT: '从对象数组中提取指定字段',
    UNIQUE: '移除数组中的重复元素',
    REVERSE: '反转数组顺序',
    FLATTEN: '将嵌套数组扁平化',
    CONCAT: '合并多个数组',
    FIRST: '获取数组的第一个元素',
    LAST: '获取数组的最后一个元素',
    COUNT: '计算数组元素数量',
    LIMIT: '限制数组元素数量',
  };
  return hints[formData.operationType || 'FILTER'] || '';
}

// 获取操作标题
function getOperationTitle(): string {
  const titles: Record<string, string> = {
    FILTER: '过滤操作',
    SORT: '排序操作',
    SLICE: '切片操作',
    EXTRACT: '提取操作',
    UNIQUE: '去重操作',
    REVERSE: '反转操作',
    FLATTEN: '扁平化操作',
    CONCAT: '合并操作',
    FIRST: '获取首元素',
    LAST: '获取尾元素',
    COUNT: '计数操作',
    LIMIT: '限制数量',
  };
  return titles[formData.operationType || 'FILTER'] || '列表操作';
}

// 获取操作描述
function getOperationDescription(): string {
  const descriptions: Record<string, string> = {
    FILTER: '根据指定条件筛选数组中的元素，只保留满足条件的元素。',
    SORT: '按指定字段对数组进行升序或降序排序。',
    SLICE: '截取数组的一部分，类似于 Python 的切片操作。',
    EXTRACT: '从对象数组中提取指定的字段，生成新的数组。',
    UNIQUE: '移除数组中的重复元素，可指定去重依据的字段。',
    REVERSE: '将数组元素的顺序反转。',
    FLATTEN: '将嵌套的数组结构扁平化为一维数组。',
    CONCAT: '将多个数组合并为一个数组。',
    FIRST: '获取数组的第一个元素，如果数组为空则返回 null。',
    LAST: '获取数组的最后一个元素，如果数组为空则返回 null。',
    COUNT: '返回数组中元素的数量。',
    LIMIT: '限制数组的元素数量，可指定偏移量。',
  };
  return descriptions[formData.operationType || 'FILTER'] || '';
}

// 处理操作类型变更
function handleOperationTypeChange() {
  handleChange();
}

// 添加过滤条件
function addFilterCondition() {
  if (!formData.filterConfig) {
    formData.filterConfig = { ...defaultFilterConfig };
  }
  if (!formData.filterConfig.conditions) {
    formData.filterConfig.conditions = [];
  }
  formData.filterConfig.conditions.push({
    field: '',
    operator: 'EQUALS' as ListCompareOperator,
    value: '',
  } as ListFilterCondition);
  handleChange();
}

// 移除过滤条件
function removeFilterCondition(index: number) {
  formData.filterConfig?.conditions?.splice(index, 1);
  handleChange();
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 输入数组变量 -->
    <a-form-item label="输入数组" required>
      <VariableInput
        v-model="formData.inputVariable"
        :current-node-id="nodeId"
        :placeholder="'{{nodeName.arrayVariable}}'"
        :filter-types="['array']"
        @change="handleChange"
      />
      <div class="form-hint">
        输入要操作的数组变量引用，如 <code v-pre>{{ start.items }}</code>
      </div>
    </a-form-item>

    <!-- 操作类型 -->
    <a-form-item label="操作类型" required>
      <a-select
        v-model:value="formData.operationType"
        @change="handleOperationTypeChange"
      >
        <a-select-opt-group label="基础操作">
          <a-select-option value="FIRST">
            <VerticalAlignTopOutlined /> 获取第一个元素
          </a-select-option>
          <a-select-option value="LAST">
            <VerticalAlignBottomOutlined /> 获取最后一个元素
          </a-select-option>
          <a-select-option value="COUNT">
            <NumberOutlined /> 计算数量
          </a-select-option>
          <a-select-option value="REVERSE">
            <SwapOutlined /> 反转数组
          </a-select-option>
        </a-select-opt-group>
        <a-select-opt-group label="筛选操作">
          <a-select-option value="FILTER">
            <FilterOutlined /> 过滤
          </a-select-option>
          <a-select-option value="UNIQUE">
            <TagOutlined /> 去重
          </a-select-option>
          <a-select-option value="LIMIT">
            <ColumnWidthOutlined /> 限制数量
          </a-select-option>
        </a-select-opt-group>
        <a-select-opt-group label="排序操作">
          <a-select-option value="SORT">
            <SortAscendingOutlined /> 排序
          </a-select-option>
        </a-select-opt-group>
        <a-select-opt-group label="提取操作">
          <a-select-option value="SLICE">
            <ScissorOutlined /> 切片
          </a-select-option>
          <a-select-option value="EXTRACT">
            <ExportOutlined /> 提取字段
          </a-select-option>
          <a-select-option value="FLATTEN">
            <MenuUnfoldOutlined /> 扁平化
          </a-select-option>
        </a-select-opt-group>
        <a-select-opt-group label="合并操作">
          <a-select-option value="CONCAT">
            <MergeCellsOutlined /> 合并数组
          </a-select-option>
        </a-select-opt-group>
      </a-select>
      <div class="form-hint">{{ getOperationHint() }}</div>
    </a-form-item>

    <!-- 过滤配置 (FILTER) -->
    <template v-if="formData.operationType === 'FILTER'">
      <a-form-item label="过滤条件">
        <div class="filter-conditions">
          <div
            v-for="(condition, index) in formData.filterConfig?.conditions"
            :key="index"
            class="condition-item"
          >
            <a-input
              v-model:value="condition.field"
              placeholder="字段路径"
              style="width: 100px"
              @change="handleChange"
            />
            <a-select
              v-model:value="condition.operator"
              style="width: 120px"
              @change="handleChange"
            >
              <a-select-option value="EQUALS">等于</a-select-option>
              <a-select-option value="NOT_EQUALS">不等于</a-select-option>
              <a-select-option value="GREATER_THAN">大于</a-select-option>
              <a-select-option value="LESS_THAN">小于</a-select-option>
              <a-select-option value="CONTAINS">包含</a-select-option>
              <a-select-option value="NOT_CONTAINS">不包含</a-select-option>
              <a-select-option value="STARTS_WITH">开头是</a-select-option>
              <a-select-option value="ENDS_WITH">结尾是</a-select-option>
              <a-select-option value="IS_NULL">为空</a-select-option>
              <a-select-option value="IS_NOT_NULL">不为空</a-select-option>
              <a-select-option value="MATCHES">正则匹配</a-select-option>
            </a-select>
            <a-input
              v-if="
                !['IS_NULL', 'IS_NOT_NULL'].includes(condition.operator || '')
              "
              v-model:value="condition.value"
              placeholder="比较值"
              style="flex: 1"
              @change="handleChange"
            />
            <a-button
              type="text"
              danger
              size="small"
              @click="removeFilterCondition(index)"
            >
              <DeleteOutlined />
            </a-button>
          </div>
          <a-button type="dashed" block @click="addFilterCondition">
            <PlusOutlined /> 添加条件
          </a-button>
        </div>
      </a-form-item>
      <a-form-item label="条件组合">
        <a-radio-group
          v-model:value="formData.filterConfig.operator"
          @change="handleChange"
        >
          <a-radio value="AND">全部满足 (AND)</a-radio>
          <a-radio value="OR">任一满足 (OR)</a-radio>
        </a-radio-group>
      </a-form-item>
    </template>

    <!-- 排序配置 (SORT) -->
    <template v-if="formData.operationType === 'SORT'">
      <a-form-item label="排序字段">
        <a-input
          v-model:value="formData.sortConfig.field"
          placeholder="字段路径，如 name 或 price"
          @change="handleChange"
        />
        <div class="form-hint">留空则按元素本身排序（适用于简单数组）</div>
      </a-form-item>
      <a-form-item label="排序方向">
        <a-radio-group
          v-model:value="formData.sortConfig.direction"
          @change="handleChange"
        >
          <a-radio value="ASC"> <SortAscendingOutlined /> 升序 </a-radio>
          <a-radio value="DESC"> <SortDescendingOutlined /> 降序 </a-radio>
        </a-radio-group>
      </a-form-item>
      <a-form-item>
        <a-checkbox
          v-model:checked="formData.sortConfig.ignoreCase"
          @change="handleChange"
        >
          忽略大小写（字符串排序）
        </a-checkbox>
      </a-form-item>
    </template>

    <!-- 切片配置 (SLICE) -->
    <template v-if="formData.operationType === 'SLICE'">
      <a-form-item label="切片范围">
        <a-space>
          <a-input-number
            v-model:value="formData.sliceConfig.start"
            :min="0"
            placeholder="起始索引"
            style="width: 100px"
            @change="handleChange"
          />
          <span>至</span>
          <a-input-number
            v-model:value="formData.sliceConfig.end"
            :min="0"
            placeholder="结束索引"
            style="width: 100px"
            @change="handleChange"
          />
        </a-space>
        <div class="form-hint">索引从 0 开始，结束索引不包含在结果中</div>
      </a-form-item>
      <a-form-item label="步长">
        <a-input-number
          v-model:value="formData.sliceConfig.step"
          :min="1"
          placeholder="1"
          style="width: 100%"
          @change="handleChange"
        />
        <div class="form-hint">每隔多少个元素取一个，默认为 1</div>
      </a-form-item>
    </template>

    <!-- 提取配置 (EXTRACT) -->
    <template v-if="formData.operationType === 'EXTRACT'">
      <a-form-item label="提取字段">
        <a-select
          v-model:value="formData.extractConfig.fields"
          mode="tags"
          placeholder="输入字段名后按回车"
          @change="handleChange"
        />
        <div class="form-hint">输入要提取的字段路径，如 name、address.city</div>
      </a-form-item>
      <a-form-item>
        <a-checkbox
          v-model:checked="formData.extractConfig.flatten"
          @change="handleChange"
        >
          扁平化输出（单字段时）
        </a-checkbox>
        <div class="form-hint">
          只提取一个字段时，直接输出值数组而非对象数组
        </div>
      </a-form-item>
    </template>

    <!-- 去重配置 (UNIQUE) -->
    <template v-if="formData.operationType === 'UNIQUE'">
      <a-form-item label="去重依据字段">
        <a-input
          v-model:value="formData.uniqueConfig.field"
          placeholder="留空则按整个元素去重"
          @change="handleChange"
        />
        <div class="form-hint">指定用于判断重复的字段，留空则比较整个元素</div>
      </a-form-item>
      <a-form-item label="保留策略">
        <a-radio-group
          v-model:value="formData.uniqueConfig.keepStrategy"
          @change="handleChange"
        >
          <a-radio value="FIRST">保留第一个</a-radio>
          <a-radio value="LAST">保留最后一个</a-radio>
        </a-radio-group>
      </a-form-item>
    </template>

    <!-- 限制数量配置 (LIMIT) -->
    <template v-if="formData.operationType === 'LIMIT'">
      <a-form-item label="限制数量">
        <a-input-number
          v-model:value="formData.limitConfig.count"
          :min="1"
          placeholder="10"
          style="width: 100%"
          @change="handleChange"
        />
      </a-form-item>
      <a-form-item label="偏移量">
        <a-input-number
          v-model:value="formData.limitConfig.offset"
          :min="0"
          placeholder="0"
          style="width: 100%"
          @change="handleChange"
        />
        <div class="form-hint">跳过前 N 个元素后再取</div>
      </a-form-item>
    </template>

    <!-- 合并配置 (CONCAT) -->
    <template v-if="formData.operationType === 'CONCAT'">
      <a-form-item label="要合并的数组">
        <a-select
          v-model:value="formData.concatConfig.otherArrays"
          mode="tags"
          :placeholder="'输入数组变量引用，如 {{node.array}}'"
          @change="handleChange"
        />
        <div class="form-hint">输入其他要合并的数组变量引用</div>
      </a-form-item>
      <a-form-item>
        <a-checkbox
          v-model:checked="formData.concatConfig.removeDuplicates"
          @change="handleChange"
        >
          合并后去重
        </a-checkbox>
      </a-form-item>
    </template>

    <!-- 输出变量名 -->
    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="list_result"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 操作说明 -->
    <a-alert type="info" show-icon class="operation-info">
      <template #message>{{ getOperationTitle() }}</template>
      <template #description>
        <div class="operation-desc">{{ getOperationDescription() }}</div>
      </template>
    </a-alert>
  </a-form>
</template>

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

  .filter-conditions {
    display: flex;
    flex-direction: column;
    gap: 8px;

    .condition-item {
      display: flex;
      gap: 8px;
      align-items: center;
    }
  }

  .operation-info {
    margin-top: 16px;

    .operation-desc {
      font-size: 12px;
      color: #595959;
    }
  }
}
</style>
