<script setup lang="ts">
import type { DeptTree } from '#/api/system/user/model';

import { computed, onMounted, ref, watch } from 'vue';

import { filterTree } from '@vben/utils';

import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import {
  Empty,
  InputSearch,
  message,
  Popconfirm,
  Skeleton,
  Tree,
} from 'ant-design-vue';

import { getOrgTree } from '#/api/core/org';

import { DelObj } from './api';

/**
 * 组件属性定义
 */
interface Props {
  /** 是否显示搜索框 */
  showSearch?: boolean;
  /** 是否显示操作按钮（添加/删除） */
  showActions?: boolean;
}

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<Props>(), {
  showSearch: true,
  showActions: true,
});

const emit = defineEmits<{
  /** 添加节点事件 */
  add: [node: DeptTree];
  /** 删除节点事件 */
  delete: [node: DeptTree];
  /** 刷新树事件 */
  reload: [];
  /** 选中节点事件 */
  select: [
    selectedKeys: (number | string)[],
    e: { selected: boolean; selectedNodes: DeptTree[] },
  ];
}>();

/** 双向绑定：选中的部门ID */
const selectDeptId = defineModel<(number | string)[]>('selectDeptId', {
  default: () => [],
});

/** 双向绑定：搜索关键词 */
const searchValue = defineModel<string>('searchValue', {
  default: '',
});

/** 部门树数据 */
const deptTreeData = ref<DeptTree[]>([]);
/** 原始部门树数据（用于搜索过滤） */
const originDeptTreeData = ref<DeptTree[]>([]);
/** 骨架屏加载状态 */
const loading = ref(true);
/** 展开的节点 */
const expandedKeys = ref<(number | string)[]>([]);

/** 是否有树数据 */
const hasTreeData = computed(() => deptTreeData.value.length > 0);

/**
 * 递归获取所有节点的 key
 */
function getAllKeys(data: DeptTree[]): (number | string)[] {
  const keys: (number | string)[] = [];
  for (const item of data) {
    keys.push(item.id);
    if (item.children?.length) {
      keys.push(...getAllKeys(item.children));
    }
  }
  return keys;
}

/**
 * 加载部门树数据
 */
async function loadTree() {
  loading.value = true;
  searchValue.value = '';
  selectDeptId.value = [];

  try {
    const data = await getOrgTree({});
    deptTreeData.value = data;
    originDeptTreeData.value = data;
    expandedKeys.value = getAllKeys(data);
  } finally {
    loading.value = false;
  }
}

/**
 * 高亮搜索关键词
 */
function highlightText(
  text: string,
  keyword: string,
): null | { after: string; before: string; match: string } {
  if (!keyword || !text.includes(keyword)) return null;
  const index = text.indexOf(keyword);
  return {
    before: text.slice(0, index),
    match: keyword,
    after: text.slice(index + keyword.length),
  };
}

// 监听搜索变化进行过滤
watch(searchValue, (val) => {
  deptTreeData.value = val
    ? filterTree(originDeptTreeData.value, (node) => node.label.includes(val))
    : originDeptTreeData.value;
  expandedKeys.value = getAllKeys(deptTreeData.value);
});

/**
 * 节点选中事件
 */
function handleSelect(selectedKeys: (number | string)[], e: any) {
  emit('select', selectedKeys, e);
}

/**
 * 添加节点
 */
function handleAdd(e: Event, node: DeptTree) {
  e.stopPropagation();
  emit('add', node);
}

/**
 * 删除节点
 */
async function handleDelete(e: Event | undefined, node: DeptTree) {
  e?.stopPropagation();
  await DelObj(node.id);
  message.success('删除成功');
  await loadTree();
  emit('delete', node);
}

/**
 * 阻止事件冒泡
 */
function stopPropagation(e: Event | undefined) {
  e?.stopPropagation();
}

onMounted(loadTree);

defineExpose({ loadTree });
</script>

<template>
  <div :class="$attrs.class" class="dept-tree h-full">
    <Skeleton :loading="loading" :paragraph="{ rows: 8 }" active>
      <div class="dept-tree__container">
        <!-- 搜索区域 -->
        <div v-if="props.showSearch" class="dept-tree__search">
          <InputSearch
            v-model:value="searchValue"
            :placeholder="$t('system.common.search')"
            allow-clear
          />
        </div>

        <!-- 树区域 -->
        <div class="dept-tree__content">
          <Transition name="fade" mode="out-in">
            <!-- 用 div 包裹 Tree 组件，确保 Transition 内有单个根元素 -->
            <div v-if="hasTreeData" key="tree">
              <Tree
                v-bind="$attrs"
                v-model:expanded-keys="expandedKeys"
                v-model:selected-keys="selectDeptId"
                :field-names="{ title: 'label', key: 'id' }"
                :show-line="{ showLeafIcon: false }"
                :tree-data="deptTreeData"
                :virtual="false"
                block-node
                class="dept-tree__tree"
                @select="handleSelect"
              >
                <template #title="{ label, data }">
                  <div class="dept-tree__node">
                    <!-- 节点标题，支持搜索高亮 -->
                    <span class="dept-tree__label">
                      <template v-if="highlightText(label, searchValue)">
                        {{ highlightText(label, searchValue)?.before }}
                        <span class="dept-tree__highlight">
                          {{ highlightText(label, searchValue)?.match }}
                        </span>
                        {{ highlightText(label, searchValue)?.after }}
                      </template>
                      <template v-else>{{ label }}</template>
                    </span>

                    <!-- 操作按钮 -->
                    <span v-if="props.showActions" class="dept-tree__actions">
                      <PlusOutlined
                        class="dept-tree__action dept-tree__action--add"
                        @click="(e) => handleAdd(e, data)"
                      />
                      <Popconfirm
                        title="确认删除该部门吗？"
                        @confirm="(e) => handleDelete(e, data)"
                        @cancel="stopPropagation"
                      >
                        <DeleteOutlined
                          class="dept-tree__action dept-tree__action--delete"
                          @click="stopPropagation"
                        />
                      </Popconfirm>
                    </span>
                  </div>
                </template>
              </Tree>
            </div>

            <!-- 空状态 -->
            <div v-else key="empty" class="dept-tree__empty">
              <Empty
                :image="Empty.PRESENTED_IMAGE_SIMPLE"
                description="无部门数据"
              />
            </div>
          </Transition>
        </div>
      </div>
    </Skeleton>
  </div>
</template>

<style scoped lang="less">
.dept-tree {
  height: 100%;
  padding: 0;

  :deep(.ant-skeleton) {
    height: 100%;
  }

  &__container {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  &__search {
    padding: 0 0 14px;
    border-bottom: 1px solid var(--border-color, #edf0f2);
  }

  &__content {
    flex: 1;
    padding: 10px 2px 0 0;
    overflow-y: auto;
    scrollbar-width: thin;
  }

  :deep(.dept-tree__tree) {
    .ant-tree-treenode {
      width: 100%;
      padding: 2px 0;
    }

    .ant-tree-node-content-wrapper {
      flex: 1;
      min-width: 0;
      min-height: 36px;
      padding: 5px 9px;
      border-radius: 7px;
      transition:
        color 0.18s ease,
        background 0.18s ease;

      &:hover {
        background: var(--component-background-light, #f4f6f8);
      }

      &.ant-tree-node-selected,
      &.ant-tree-node-selected:hover {
        color: #fff;
        font-weight: 600;
        background: hsl(var(--primary)) !important;
        box-shadow: 0 2px 8px hsl(var(--primary) / 22%);

        .dept-tree__highlight {
          color: #fff;
          background: rgb(255 255 255 / 18%);
        }

        .dept-tree__action {
          color: rgb(255 255 255 / 88%);

          &:hover {
            color: #fff;
            background: rgb(255 255 255 / 18%);
          }
        }
      }
    }

    .ant-tree-switcher {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 22px;
    }
  }

  &__node {
    display: flex;
    gap: 8px;
    align-items: center;
    justify-content: space-between;
  }

  &__label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__highlight {
    padding: 0 2px;
    font-weight: 600;
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 8%);
    border-radius: 3px;
  }

  &__actions {
    display: flex;
    flex: none;
    visibility: hidden;
    gap: 3px;
    opacity: 0;
    transition: opacity 0.18s ease;
  }

  &__node:hover &__actions,
  &__node:focus-within &__actions {
    visibility: visible;
    opacity: 1;
  }

  &__action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 25px;
    height: 25px;
    padding: 0;
    font-size: 13px;
    cursor: pointer;
    border-radius: 5px;
    transition:
      color 0.18s ease,
      background 0.18s ease;

    &--add:hover {
      color: hsl(var(--primary));
      background: hsl(var(--primary) / 10%);
    }

    &--delete:hover {
      color: #ff4d4f;
      background: rgb(255 77 79 / 9%);
    }
  }

  &__empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 220px;
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
