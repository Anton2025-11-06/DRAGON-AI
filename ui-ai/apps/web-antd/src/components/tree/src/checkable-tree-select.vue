<script setup lang="ts">
/**
 * 可勾选树选择器组件
 * 带搜索、全选、展开/折叠等功能
 */
import { computed, ref, watch } from 'vue';

import { treeToList } from '@vben/utils';

import { Checkbox, Input } from 'ant-design-vue';

import CheckableTree from './checkable-tree.vue';

interface Props {
  /** 选中的节点 key 数组 */
  value?: (number | string)[];
  /** 树数据 */
  treeData?: any[];
  /** 字段名映射 */
  fieldNames?: { children: string; key: string; title: string };
  /** 是否严格模式（父子节点不关联） */
  checkStrictly?: boolean;
  /** 是否默认展开全部 */
  expandAllOnInit?: boolean;
  /** 最大高度 */
  maxHeight?: number | string;
  /** 是否显示操作栏 */
  showToolbar?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  value: () => [],
  treeData: () => [],
  fieldNames: () => ({ children: 'children', key: 'id', title: 'title' }),
  checkStrictly: true,
  expandAllOnInit: false,
  maxHeight: 400,
  showToolbar: true,
});

const emit = defineEmits(['update:value']);

const currentCheckStrictly = ref(props.checkStrictly);

watch(
  () => props.checkStrictly,
  (val) => {
    currentCheckStrictly.value = val;
  },
);

const innerCheckedStrictly = computed(() => !currentCheckStrictly.value);

const associationText = computed(() =>
  currentCheckStrictly.value ? '父子节点关联' : '父子节点独立',
);

const innerCheckedKeys = ref<(number | string)[]>([]);

const allKeys = computed(() => {
  const idField = props.fieldNames.key;
  if (!props.treeData) return [];
  return treeToList(props.treeData).map((item: any) => item[idField]);
});

const checkedRealKeys = ref<(number | string)[]>([]);
const selectAllStatus = ref(false);
const searchText = ref('');
const treeRef = ref<any>(null);

// 防抢操作锁
let isProcessing = false;

watch(
  () => props.value,
  (val) => {
    const currentVal = val || [];
    if (JSON.stringify(currentVal) === JSON.stringify(checkedRealKeys.value)) {
      return;
    }
    checkedRealKeys.value = currentVal;
    innerCheckedKeys.value = currentVal;
    selectAllStatus.value =
      currentVal.length > 0 && currentVal.length === allKeys.value.length;
  },
  { deep: true, immediate: true },
);

// 处理树数据，标记倒数第二层节点
const innerTreeData = computed(() => {
  if (!props.treeData) return props.treeData;

  const processNode = (nodes: any[]) => {
    return nodes.map((node) => {
      const newNode = { ...node };
      if (newNode.children && newNode.children.length > 0) {
        const isAllLeaves = newNode.children.every(
          (child: any) => !child.children || child.children.length === 0,
        );
        if (isAllLeaves) {
          newNode.isPenultimate = true;
        } else {
          newNode.children = processNode(newNode.children);
        }
      }
      return newNode;
    });
  };

  return processNode(props.treeData);
});

const expandStatus = ref(false);

function handleExpandOrCollapseAll(e: any) {
  if (isProcessing) return;
  isProcessing = true;

  const checked = e.target.checked;
  expandStatus.value = checked;
  if (treeRef.value) {
    if (checked) {
      treeRef.value.expandAll();
    } else {
      treeRef.value.collapseAll();
    }
  }

  setTimeout(() => {
    isProcessing = false;
  }, 100);
}

function handleCheckStrictlyChange(e: any) {
  const checked = e.target.checked;
  currentCheckStrictly.value = checked;
  innerCheckedKeys.value = [];
  checkedRealKeys.value = [];
  emit('update:value', []);
}

function handleSelectAllChange(e: any) {
  if (isProcessing) return;
  isProcessing = true;

  const checked = e.target.checked;
  selectAllStatus.value = checked;
  if (checked) {
    const keys = [...allKeys.value];
    innerCheckedKeys.value = keys;
    checkedRealKeys.value = keys;
    emit('update:value', keys);
  } else {
    innerCheckedKeys.value = [];
    checkedRealKeys.value = [];
    emit('update:value', []);
  }

  setTimeout(() => {
    isProcessing = false;
  }, 100);
}

function handleTreeValueChange(val: any[]) {
  innerCheckedKeys.value = val;
  checkedRealKeys.value = val;
  selectAllStatus.value = val.length > 0 && val.length === allKeys.value.length;
  emit('update:value', val);
}

defineExpose({
  getCheckedKeys: () => checkedRealKeys.value,
  expandAll: () => treeRef.value?.expandAll(),
  collapseAll: () => treeRef.value?.collapseAll(),
});
</script>

<template>
  <div class="checkable-tree-select rounded-lg border bg-background p-3">
    <!-- 状态栏 -->
    <div class="flex items-center justify-between gap-2 border-b pb-2">
      <div>
        <span>节点状态: </span>
        <span :class="[currentCheckStrictly ? 'text-primary' : 'text-red-500']">
          {{ associationText }}
        </span>
      </div>
      <div>
        已选中
        <span class="mx-1 font-semibold text-primary">
          {{ checkedRealKeys.length }}
        </span>
        个节点
      </div>
    </div>

    <!-- 操作栏 -->
    <div v-if="showToolbar" class="border-b py-2">
      <div class="mb-2">
        <Input
          v-model:value="searchText"
          allow-clear
          placeholder="搜索节点..."
          style="width: 100%"
        />
      </div>
      <div class="flex flex-wrap items-center justify-between">
        <Checkbox :checked="expandStatus" @change="handleExpandOrCollapseAll">
          展开/折叠全部
        </Checkbox>
        <Checkbox :checked="selectAllStatus" @change="handleSelectAllChange">
          全选/取消全选
        </Checkbox>
        <Checkbox
          :checked="currentCheckStrictly"
          @change="handleCheckStrictlyChange"
        >
          父子节点关联
        </Checkbox>
      </div>
    </div>

    <!-- 树区域 -->
    <div
      class="tree-content flex-1 overflow-auto py-2"
      :style="{
        maxHeight: typeof maxHeight === 'number' ? `${maxHeight}px` : maxHeight,
      }"
    >
      <CheckableTree
        ref="treeRef"
        :check-strictly="innerCheckedStrictly"
        :expand-all="expandAllOnInit"
        :field-names="fieldNames"
        :search-text="searchText"
        :tree-data="innerTreeData"
        :value="innerCheckedKeys"
        @update:value="handleTreeValueChange"
      />
    </div>
  </div>
</template>

<style scoped>
.checkable-tree-select {
  width: 100%;
}
</style>
