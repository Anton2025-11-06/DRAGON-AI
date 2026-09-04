<template>
  <div class="sys-menu-view h-full p-4">
    <a-card :bordered="false" class="h-full">
      <div class="flex items-center justify-between mb-4">
        <span class="text-base font-medium">菜单管理</span>
        <a-button type="primary" @click="handleCreate(0)">
          <template #icon><PlusOutlined /></template>
          新增根目录
        </a-button>
      </div>
      <a-spin :spinning="loading">
        <a-tree
          v-model:expanded-keys="expandedKeys"
          :auto-expand-parent="true"
          :default-expand-all="true"
          block-node
          :tree-data="treeData"
        >
          <template #title="{ menu_id, menu_name, menu_type }">
            <div
              class="flex items-center justify-between w-full pr-2"
              @mouseenter="handleMouseEnter(menu_id)"
              @mouseleave="handleMouseLeave"
            >
              <span class="flex items-center gap-1">
                {{ menu_name }}
                <a-tag v-if="menu_type === 3" color="blue" class="ml-1">按钮</a-tag>
                <a-tag v-else-if="menu_type === 1" color="green" class="ml-1">目录</a-tag>
              </span>
              <div v-show="hoveredNodeId === menu_id" class="flex gap-2">
                <a-button
                  size="small"
                  type="link"
                  @click.stop="handleCreate(menu_id)"
                >
                  新增
                </a-button>
                <a-button
                  size="small"
                  type="link"
                  @click.stop="handleEdit(menu_id)"
                >
                  编辑
                </a-button>
                <a-popconfirm
                  title="确定要删除该菜单吗？其子菜单将一并删除"
                  ok-text="确认删除"
                  cancel-text="取消"
                  @confirm="handleDelete(menu_id)"
                >
                  <a-button size="small" type="link" danger @click.stop>
                    删除
                  </a-button>
                </a-popconfirm>
              </div>
            </div>
          </template>
        </a-tree>
      </a-spin>
    </a-card>

    <a-drawer
      v-model:open="drawerOpen"
      :title="form.menu_id ? '编辑菜单' : '新增菜单'"
      width="520"
      destroy-on-close
    >
      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :label-col="{ style: { width: '100px' } }"
      >
        <a-form-item label="父级菜单" v-if="form.parent_id > 0">
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
        <template v-if="form.menu_type !== 3">
          <a-form-item label="路由路径" name="path">
            <a-input
              v-model:value="form.path"
              :placeholder="
                form.menu_type === 1 ? '顶级目录如 /system，子目录如 sub' : '子级菜单如 user'
              "
            />
          </a-form-item>
          <a-form-item label="组件路径" v-if="form.menu_type === 2" name="component">
            <a-input
              v-model:value="form.component"
              placeholder="如 views/wemirr/system/user/index.vue"
            />
          </a-form-item>
        </template>
        <a-form-item label="权限标识" v-if="form.menu_type === 3" name="perm">
          <a-input
            v-model:value="form.perm"
            placeholder="如 system:user:add"
          />
        </a-form-item>
        <a-form-item label="图标">
          <a-input v-model:value="form.icon" placeholder="如 lucide:user" />
        </a-form-item>
        <a-form-item label="排序号" name="sort">
          <a-input-number v-model:value="form.sort" :min="0" style="width: 100%" />
        </a-form-item>
        <a-form-item label="是否显示">
          <a-switch v-model:checked="form.visible" :checked-value="1" :un-checked-value="0" />
        </a-form-item>
        <a-form-item label="状态">
          <a-switch v-model:checked="form.status" :checked-value="1" :un-checked-value="0" />
        </a-form-item>
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

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { PlusOutlined } from '@ant-design/icons-vue';
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
const hoveredNodeId = ref<number | null>(null);
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
    treeData.value = (await defHttp.get<MenuNode[]>('/api/system/menus/tree')) ?? [];
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
  parentName.value = parentId === 0 ? '根目录' : parent?.menu_name ?? '';
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
  parentName.value = node.parent_id === 0 ? '根目录' : parent?.menu_name ?? '';
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

<style lang="less" scoped>
.sys-menu-view {
  .ant-tree-node-content-wrapper {
    flex: 1;
    width: 100%;
  }
}
</style>