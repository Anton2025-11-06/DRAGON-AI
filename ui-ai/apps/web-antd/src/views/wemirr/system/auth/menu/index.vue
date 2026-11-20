<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import {
  ApartmentOutlined,
  ControlOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  PlusOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

import { defHttp } from '#/api/request';

defineOptions({ name: 'SystemMenuManage' });

// ==================== 类型 ====================
interface MenuNode {
  menu_id: number;
  parent_id: number;
  menu_name: string;
  menu_type: number;
  path?: string;
  component?: string;
  perm?: string;
  icon?: string;
  sort?: number;
  visible?: number;
  status?: number;
  children?: MenuNode[];
}

// ==================== 状态 ====================
const loading = ref(false);
const saving = ref(false);
const hoveredNodeId = ref<null | number>(null);
const expandedKeys = ref<string[]>([]);
const treeData = ref<MenuNode[]>([]);
const drawerOpen = ref(false);
const formRef = ref();
const parentName = ref('');

const form = reactive<Record<string, any>>({
  menu_id: 0,
  parent_id: 0,
  menu_name: '',
  menu_type: 1,
  path: '',
  component: '',
  perm: '',
  icon: '',
  sort: 0,
  visible: 1,
  status: 1,
});

const rules = {
  menu_name: [{ required: true, message: '请输入菜单名称' }],
};

// ==================== API ====================
async function fetchMenuTree() {
  loading.value = true;
  try {
    // GET /api/system/menus/tree
    treeData.value =
      (await defHttp.get<MenuNode[]>('/api/system/menus/tree')) ?? [];
  } finally {
    loading.value = false;
  }
}

function handleMouseEnter(nodeId: number) {
  hoveredNodeId.value = nodeId;
}

function handleMouseLeave() {
  hoveredNodeId.value = null;
}

function handleCreate(parentId: number) {
  const parent = findNode(treeData.value, parentId);
  parentName.value = parentId === 0 ? '根目录' : (parent?.menu_name ?? '');
  Object.assign(form, {
    menu_id: 0,
    parent_id: parentId,
    menu_name: '',
    menu_type: parentId === 0 ? 1 : 2,
    path: '',
    component: '',
    perm: '',
    icon: '',
    sort: 0,
    visible: 1,
    status: 1,
  });
  drawerOpen.value = true;
}

function handleEdit(menuId: number) {
  const node = findNode(treeData.value, menuId);
  if (!node) return;
  const parent = findNode(treeData.value, node.parent_id);
  parentName.value =
    node.parent_id === 0 ? '根目录' : (parent?.menu_name ?? '');
  Object.assign(form, {
    menu_id: node.menu_id,
    parent_id: node.parent_id,
    menu_name: node.menu_name,
    menu_type: node.menu_type,
    path: node.path ?? '',
    component: node.component ?? '',
    perm: node.perm ?? '',
    icon: node.icon ?? '',
    sort: node.sort ?? 0,
    visible: node.visible ?? 1,
    status: node.status ?? 1,
  });
  drawerOpen.value = true;
}

async function handleDelete(menuId: number) {
  try {
    // DELETE /api/system/menus/{menu_id}
    await defHttp.delete(`/api/system/menus/${menuId}`);
    message.success('删除成功');
    await fetchMenuTree();
  } catch {
    message.error('删除失败');
  }
}

async function handleSave() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  saving.value = true;
  try {
    // 表单字段转换为后端 snake_case
    const payload = {
      parent_id: form.parent_id,
      menu_name: form.menu_name,
      menu_type: form.menu_type,
      path: form.path || null,
      component: form.component || null,
      perm: form.perm || null,
      icon: form.icon || null,
      sort: form.sort,
      visible: form.visible,
      status: form.status,
    };
    if (form.menu_id) {
      // PUT /api/system/menus/{menu_id}
      await defHttp.put(`/api/system/menus/${form.menu_id}`, payload);
      message.success('更新成功');
    } else {
      // POST /api/system/menus
      await defHttp.post('/api/system/menus', payload);
      message.success('新增成功');
    }
    drawerOpen.value = false;
    await fetchMenuTree();
  } catch {
    // 错误信息由全局拦截器提示
  } finally {
    saving.value = false;
  }
}

function findNode(list: MenuNode[], id: number): MenuNode | undefined {
  for (const node of list) {
    if (node.menu_id === id) return node;
    const found = findNode(node.children ?? [], id);
    if (found) return found;
  }
  return undefined;
}

onMounted(fetchMenuTree);
</script>

<template>
  <div class="sys-menu-view h-full">
    <a-card :bordered="false" class="menu-card h-full">
      <div class="menu-toolbar">
        <div>
          <h2>菜单管理</h2>
          <p>统一维护目录、页面与按钮权限，树形结构清晰呈现菜单层级</p>
        </div>
        <a-button type="primary" @click="handleCreate(0)">
          <template #icon><PlusOutlined /></template>
          新增根目录
        </a-button>
      </div>

      <div class="menu-tree-panel">
        <div class="tree-panel-head">
          <span>菜单结构</span>
          <span>悬停节点可新增、编辑或删除</span>
        </div>
        <a-spin :spinning="loading">
          <a-tree
            v-model:expanded-keys="expandedKeys"
            :auto-expand-parent="true"
            :default-expand-all="true"
            block-node
            :tree-data="treeData"
            class="menu-tree"
          >
            <template
              #title="{
                menu_id,
                menu_name,
                menu_type,
                path,
                component,
                perm,
                status,
                sort,
              }"
            >
              <div
                class="menu-node"
                @mouseenter="handleMouseEnter(menu_id)"
                @mouseleave="handleMouseLeave"
              >
                <div class="node-leading">
                  <span class="node-type-icon" :class="`type-${menu_type}`">
                    <ApartmentOutlined v-if="menu_type === 1" />
                    <FileTextOutlined v-else-if="menu_type === 2" />
                    <ControlOutlined v-else />
                  </span>
                  <div class="node-copy">
                    <div class="node-name-row">
                      <span class="node-name">{{ menu_name }}</span>
                      <a-tag v-if="menu_type === 3">按钮</a-tag>
                      <a-tag v-else-if="menu_type === 1" color="blue">
                        目录
                      </a-tag>
                      <a-tag v-else color="cyan">菜单</a-tag>
                    </div>
                    <div v-if="perm || component || path" class="node-path">
                      {{ menu_type === 3 ? perm : component || path }}
                    </div>
                  </div>
                </div>

                <div class="node-meta">
                  <span
                    class="status-indicator"
                    :class="{ disabled: status !== 1 }"
                  ></span>
                  <span>{{ status === 1 ? '启用' : '停用' }}</span>
                  <span class="node-sort">排序 {{ sort ?? 0 }}</span>
                </div>

                <div v-show="hoveredNodeId === menu_id" class="node-actions">
                  <a-tooltip title="新增子菜单">
                    <a-button
                      size="small"
                      type="text"
                      @click.stop="handleCreate(menu_id)"
                    >
                      <template #icon><PlusOutlined /></template>
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="编辑">
                    <a-button
                      size="small"
                      type="text"
                      @click.stop="handleEdit(menu_id)"
                    >
                      <template #icon><EditOutlined /></template>
                    </a-button>
                  </a-tooltip>
                  <a-popconfirm
                    title="确定要删除该菜单吗？其子菜单将一并删除"
                    ok-text="确认删除"
                    cancel-text="取消"
                    @confirm="handleDelete(menu_id)"
                  >
                    <a-tooltip title="删除">
                      <a-button size="small" type="text" danger @click.stop>
                        <template #icon><DeleteOutlined /></template>
                      </a-button>
                    </a-tooltip>
                  </a-popconfirm>
                </div>
              </div>
            </template>
          </a-tree>
        </a-spin>
      </div>
    </a-card>

    <a-drawer
      v-model:open="drawerOpen"
      :title="form.menu_id ? '编辑菜单' : '新增菜单'"
      width="560"
      destroy-on-close
      class="menu-form-drawer"
    >
      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :label-col="{ style: { width: '88px' } }"
        class="menu-form"
      >
        <section class="form-section">
          <div class="form-section-title">基础信息</div>
          <a-form-item v-if="form.parent_id > 0" label="父级菜单">
            <a-input :value="parentName" disabled />
          </a-form-item>
          <a-form-item label="菜单类型" name="menu_type">
            <a-radio-group v-model:value="form.menu_type" button-style="solid">
              <a-radio-button :value="1">目录</a-radio-button>
              <a-radio-button :value="2">菜单</a-radio-button>
              <a-radio-button :value="3">按钮</a-radio-button>
            </a-radio-group>
          </a-form-item>
          <a-form-item label="菜单名称" name="menu_name">
            <a-input
              v-model:value="form.menu_name"
              placeholder="请输入菜单名称"
              show-count
              :maxlength="64"
            />
          </a-form-item>
          <a-form-item label="图标">
            <a-input v-model:value="form.icon" placeholder="如 lucide:user" />
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title">路由与权限</div>
          <template v-if="form.menu_type !== 3">
            <a-form-item label="路由路径" name="path">
              <a-input
                v-model:value="form.path"
                :placeholder="
                  form.menu_type === 1
                    ? '顶级目录如 /system'
                    : '子级菜单如 user'
                "
              />
            </a-form-item>
            <a-form-item
              v-if="form.menu_type === 2"
              label="组件路径"
              name="component"
            >
              <a-input
                v-model:value="form.component"
                placeholder="如 views/wemirr/system/user/index.vue"
              />
            </a-form-item>
          </template>
          <a-form-item v-if="form.menu_type === 3" label="权限标识" name="perm">
            <a-input
              v-model:value="form.perm"
              placeholder="如 system:user:add"
            />
          </a-form-item>
        </section>

        <section class="form-section">
          <div class="form-section-title">展示与状态</div>
          <a-form-item label="排序号" name="sort">
            <a-input-number
              v-model:value="form.sort"
              :min="0"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item label="是否显示">
            <a-switch
              v-model:checked="form.visible"
              :checked-value="1"
              :un-checked-value="0"
            />
          </a-form-item>
          <a-form-item label="状态">
            <a-switch
              v-model:checked="form.status"
              :checked-value="1"
              :un-checked-value="0"
            />
          </a-form-item>
        </section>
      </a-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <a-button @click="drawerOpen = false">取消</a-button>
          <a-button type="primary" :loading="saving" @click="handleSave">
            保存
          </a-button>
        </div>
      </template>
    </a-drawer>
  </div>
</template>

<style lang="less" scoped>
.sys-menu-view {
  padding: 16px;
}

.menu-card {
  overflow: hidden;
  border: 1px solid var(--border-color, #e8ebf0);
  border-radius: 12px;

  :deep(.ant-card-body) {
    height: 100%;
    padding: 20px;
    overflow: auto;
  }
}

.menu-toolbar {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 18px;

  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: var(--text-color, #262b33);
  }

  p {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--text-color-secondary, #8b929a);
  }
}

.menu-tree-panel {
  overflow: hidden;
  border: 1px solid var(--border-color, #e8ebef);
  border-radius: 10px;
}

.tree-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  background: var(--component-background-light, #f7f8fa);
  border-bottom: 1px solid var(--border-color, #edf0f2);

  span:last-child {
    font-size: 12px;
    font-weight: 400;
    color: var(--text-color-secondary, #9299a1);
  }
}

.menu-tree {
  padding: 8px;

  :deep(.ant-tree-treenode) {
    align-items: center;
    padding: 3px 0;
  }

  :deep(.ant-tree-switcher) {
    display: flex;
    align-items: center;
    justify-content: center;
  }

  :deep(.ant-tree-node-content-wrapper) {
    flex: 1;
    width: 100%;
    min-width: 0;
    padding: 0;
    border-radius: 8px;
  }

  :deep(.ant-tree-node-content-wrapper:hover) {
    background: var(--component-background-light, #f5f7fa);
  }
}

.menu-node {
  display: flex;
  gap: 16px;
  align-items: center;
  min-width: 0;
  min-height: 54px;
  padding: 7px 10px;
}

.node-leading {
  display: flex;
  flex: 1;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.node-type-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: 15px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border-radius: 8px;

  &.type-3 {
    color: #7a818a;
    background: var(--component-background-light, #f0f1f3);
  }
}

.node-copy {
  flex: 1;
  min-width: 0;
}

.node-name-row {
  display: flex;
  gap: 8px;
  align-items: center;

  :deep(.ant-tag) {
    padding: 0 6px;
    margin: 0;
    font-size: 11px;
    line-height: 18px;
  }
}

.node-name {
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color, #30353d);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-path {
  margin-top: 3px;
  overflow: hidden;
  font-size: 11px;
  line-height: 16px;
  color: var(--text-color-secondary, #979da5);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.node-meta {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  font-size: 12px;
  color: var(--text-color-secondary, #858c95);
}

.status-indicator {
  width: 6px;
  height: 6px;
  background: #52c41a;
  border-radius: 50%;

  &.disabled {
    background: #b7bcc3;
  }
}

.node-sort {
  padding-left: 10px;
  margin-left: 4px;
  border-left: 1px solid var(--border-color, #e3e6ea);
}

.node-actions {
  display: flex;
  flex: 0 0 96px;
  gap: 2px;
  align-items: center;
  justify-content: flex-end;
}

.form-section {
  padding: 16px 16px 2px;
  margin-bottom: 14px;
  background: var(--component-background-light, #fafbfc);
  border: 1px solid var(--border-color, #edf0f2);
  border-radius: 9px;
}

.form-section-title {
  padding-left: 8px;
  margin-bottom: 16px;
  font-size: 13px;
  font-weight: 600;
  line-height: 18px;
  border-left: 3px solid hsl(var(--primary));
}

@media (max-width: 760px) {
  .sys-menu-view {
    padding: 8px;
  }

  .node-meta,
  .tree-panel-head span:last-child {
    display: none;
  }

  .menu-node {
    gap: 6px;
  }
}
</style>
