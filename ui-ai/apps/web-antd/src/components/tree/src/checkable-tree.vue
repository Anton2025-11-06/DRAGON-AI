<script setup lang="ts">
/**
 * 可勾选树组件
 * 支持级联选择、搜索高亮、横向平铺等功能
 */
import { inject, provide, shallowRef, triggerRef, watch } from 'vue';

import { CaretDownOutlined, CaretRightOutlined } from '@ant-design/icons-vue';
import { Checkbox } from 'ant-design-vue';

interface TreeProps {
  /** 树数据 */
  treeData?: any[];
  /** 选中的节点 key 数组 */
  value?: (number | string)[];
  /** 字段名映射 */
  fieldNames?: { children: string; key: string; title: string };
  /** 是否严格模式（父子节点不关联） */
  checkStrictly?: boolean;
  /** 是否默认展开全部 */
  expandAll?: boolean;
  /** 搜索文本 */
  searchText?: string;
}

defineOptions({ name: 'CheckableTree' });

const props = withDefaults(defineProps<TreeProps>(), {
  treeData: () => [],
  value: () => [],
  fieldNames: () => ({ children: 'children', key: 'id', title: 'title' }),
  checkStrictly: false,
  expandAll: false,
  searchText: '',
});

const emit = defineEmits(['update:value', 'update:expandedKeys']);

// 展开状态管理
const expandedKeys = shallowRef<Set<number | string>>(new Set());
// 内部选中状态
const checkedKeys = shallowRef<Set<number | string>>(new Set());

// 数据扁平化映射 - 使用 provide/inject 共享
const TREE_MAP_KEY = 'checkableTreeMapContext';

interface TreeMapContext {
  nodeMap: Map<number | string, any>;
  parentMap: Map<number | string, any>;
}

const injectedContext = inject<null | TreeMapContext>(TREE_MAP_KEY, null);
const isRoot = !injectedContext;
const nodeMap = injectedContext?.nodeMap ?? new Map<number | string, any>();
const parentMap = injectedContext?.parentMap ?? new Map<number | string, any>();

if (isRoot) {
  provide(TREE_MAP_KEY, { nodeMap, parentMap });
}

// 初始化数据映射
const initMap = (nodes: any[], parent: any = null) => {
  nodes.forEach((node) => {
    const key = node[props.fieldNames.key];
    nodeMap.set(key, node);
    if (parent) {
      parentMap.set(key, parent);
    }
    const children = node[props.fieldNames.children];
    if (children && children.length > 0) {
      initMap(children, node);
    }
  });
};

// 监听 treeData 变化
watch(
  () => props.treeData,
  (newVal) => {
    if (isRoot) {
      nodeMap.clear();
      parentMap.clear();
      initMap(newVal);
    }
    if (props.expandAll) {
      expandAllNodes(newVal);
    }
  },
  { immediate: true },
);

// 监听 value 变化
watch(
  () => props.value,
  (newVal) => {
    checkedKeys.value = new Set(newVal);
  },
  { immediate: true },
);

// 监听搜索文本，自动展开匹配节点的父节点
watch(
  () => props.searchText,
  (newVal) => {
    if (newVal) {
      const expandMatchedParents = (nodes: any[]) => {
        nodes.forEach((node) => {
          const children = node[props.fieldNames.children];
          if (children && children.length > 0) {
            expandMatchedParents(children);
            if (
              children.some((child: any) =>
                isNodeOrChildrenMatch(child, newVal),
              )
            ) {
              expandedKeys.value.add(node[props.fieldNames.key]);
            }
          }
        });
      };
      if (props.treeData) {
        expandMatchedParents(props.treeData);
        triggerRef(expandedKeys);
      }
    }
  },
);

function expandAllNodes(nodes: any[], trigger = true) {
  nodes.forEach((node) => {
    const key = node[props.fieldNames.key];
    expandedKeys.value.add(key);
    const children = node[props.fieldNames.children];
    if (children) {
      expandAllNodes(children, false);
    }
  });
  if (trigger) triggerRef(expandedKeys);
}

function collapseAllNodes() {
  expandedKeys.value.clear();
  triggerRef(expandedKeys);
}

defineExpose({
  expandAll: () => {
    if (props.treeData) {
      expandAllNodes(props.treeData);
    }
  },
  collapseAll: collapseAllNodes,
});

const toggleExpand = (node: any, e?: Event) => {
  e?.stopPropagation();
  const key = node[props.fieldNames.key];
  if (expandedKeys.value.has(key)) {
    expandedKeys.value.delete(key);
  } else {
    expandedKeys.value.add(key);
  }
  triggerRef(expandedKeys);
};

// 判断节点是否匹配搜索
const isNodeMatch = (node: any, searchText: string): boolean => {
  if (!searchText) return true;
  const title = node[props.fieldNames.title];
  return title?.toLowerCase().includes(searchText.toLowerCase());
};

// 判断节点或其子孙是否匹配搜索
const isNodeOrChildrenMatch = (node: any, searchText: string): boolean => {
  if (!searchText) return true;
  if (isNodeMatch(node, searchText)) return true;
  const children = node[props.fieldNames.children];
  if (children && children.length > 0) {
    return children.some((child: any) =>
      isNodeOrChildrenMatch(child, searchText),
    );
  }
  return false;
};

// 检查节点选中状态
const getNodeStatus = (node: any) => {
  const key = node[props.fieldNames.key];
  const children = node[props.fieldNames.children];

  if (props.checkStrictly || !children || children.length === 0) {
    return {
      checked: checkedKeys.value.has(key),
      indeterminate: false,
    };
  }

  const isChecked = checkedKeys.value.has(key);
  if (isChecked) return { checked: true, indeterminate: false };

  const hasCheckedChild = checkChildrenStatus(node);
  return {
    checked: false,
    indeterminate: hasCheckedChild,
  };
};

const checkChildrenStatus = (node: any): boolean => {
  const children = node[props.fieldNames.children];
  if (!children) return false;
  return children.some((child: any) => {
    if (checkedKeys.value.has(child[props.fieldNames.key])) return true;
    return checkChildrenStatus(child);
  });
};

// 处理勾选
const handleCheck = (node: any, checked: boolean) => {
  const key = node[props.fieldNames.key];
  const newKeys = new Set(checkedKeys.value);

  if (props.checkStrictly) {
    if (checked) newKeys.add(key);
    else newKeys.delete(key);
  } else {
    // 级联处理 - 向下
    const updateChildren = (n: any, c: boolean) => {
      const k = n[props.fieldNames.key];
      if (c) newKeys.add(k);
      else newKeys.delete(k);
      const children = n[props.fieldNames.children];
      if (children) {
        children.forEach((child: any) => updateChildren(child, c));
      }
    };
    updateChildren(node, checked);

    // 级联处理 - 向上
    let current = node;
    while (true) {
      const parent = parentMap.get(current[props.fieldNames.key]);
      if (!parent) break;

      const parentKey = parent[props.fieldNames.key];
      const siblings = parent[props.fieldNames.children];
      const hasAnyChecked = siblings.some((sib: any) =>
        newKeys.has(sib[props.fieldNames.key]),
      );

      if (hasAnyChecked) {
        newKeys.add(parentKey);
      } else {
        newKeys.delete(parentKey);
      }
      current = parent;
    }
  }

  emit('update:value', [...newKeys]);
};
</script>

<template>
  <ul class="checkable-tree">
    <li
      v-for="node in treeData"
      v-show="isNodeOrChildrenMatch(node, searchText)"
      :key="node[fieldNames.key]"
      class="tree-node"
    >
      <div class="node-content">
        <!-- 展开图标 -->
        <span
          class="switcher"
          :class="{ 'is-noop': !node[fieldNames.children]?.length }"
          @click="(e) => toggleExpand(node, e)"
        >
          <template v-if="node[fieldNames.children]?.length">
            <CaretDownOutlined v-if="expandedKeys.has(node[fieldNames.key])" />
            <CaretRightOutlined v-else />
          </template>
        </span>

        <!-- 复选框 -->
        <Checkbox
          :checked="getNodeStatus(node).checked"
          :indeterminate="getNodeStatus(node).indeterminate"
          @update:checked="(val: any) => handleCheck(node, val)"
        >
          <span
            :class="{
              'search-highlight': searchText && isNodeMatch(node, searchText),
            }"
          >
            {{ node[fieldNames.title] }}
          </span>
        </Checkbox>
      </div>

      <!-- 子节点 -->
      <div
        v-if="
          node[fieldNames.children]?.length &&
          expandedKeys.has(node[fieldNames.key])
        "
        class="node-children"
        :class="{ 'is-horizontal': node.isPenultimate }"
      >
        <!-- 递归渲染 -->
        <template v-if="!node.isPenultimate">
          <CheckableTree
            :check-strictly="checkStrictly"
            :expand-all="expandAll"
            :field-names="fieldNames"
            :search-text="searchText"
            :tree-data="node[fieldNames.children]"
            :value="props.value"
            @update:value="(val: any) => emit('update:value', val)"
          />
        </template>

        <!-- 横向平铺渲染 (倒数第二层) -->
        <template v-else>
          <div
            v-for="child in node[fieldNames.children]"
            v-show="isNodeOrChildrenMatch(child, searchText)"
            :key="child[fieldNames.key]"
            class="horizontal-item"
          >
            <Checkbox
              :checked="checkedKeys.has(child[fieldNames.key])"
              @update:checked="(val: any) => handleCheck(child, val)"
            >
              <span
                :class="{
                  'search-highlight':
                    searchText && isNodeMatch(child, searchText),
                }"
              >
                {{ child[fieldNames.title] }}
              </span>
            </Checkbox>
          </div>
        </template>
      </div>
    </li>
  </ul>
</template>

<style scoped lang="less">
.checkable-tree {
  padding: 0;
  margin: 0;
  list-style: none;
}

.tree-node {
  line-height: 28px;
}

.node-content {
  display: flex;
  align-items: center;

  &:hover {
    background-color: var(--accent, #f5f5f5);
  }
}

.switcher {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  color: rgb(0 0 0 / 45%);
  cursor: pointer;
  user-select: none;
  transition: all 0.2s;

  &:hover:not(.is-noop) {
    color: var(--primary, #1890ff);
  }

  &.is-noop {
    cursor: default;
    opacity: 0;
  }

  :deep(.anticon) {
    font-size: 12px;
  }
}

.node-children {
  padding-left: 24px;

  &.is-horizontal {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    padding: 8px 0 8px 24px;

    .horizontal-item {
      display: inline-flex;
      align-items: center;
    }
  }
}

.search-highlight {
  padding: 0 4px;
  font-weight: 600;
  color: var(--primary, #1890ff);
  background-color: var(--primary-100, #e6f7ff);
  border-radius: 2px;
}
</style>
