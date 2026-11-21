/**
 * ACL 授权弹窗用的部门组织树缓存。
 *
 * 为什么单独一份 composable 而不是塞进组件：
 * - 各资源列表页（知识库/文档/工作流/模板/工具/技能/MCP/编排四模式）都挂同一个
 *   AclGrantModal；每个列表页又可能同时开着多个不同资源的授权弹窗（不同 :key 会
 *   重建实例）。组织树整张平台一份，不该跟着组件实例走。
 * - Vue 组件 <script setup> 每次实例化都会跑一遍，把 promise 缓存放这儿天然跨实例
 *   共享，也不需要引 pinia；页面刷新才重建，符合"部门结构当日不变"的直觉。
 * - 失败要能重试：拉部门树本身很少失败（后端只要 grant 权限即可），但网络抖一下就会
 *   让 promise 永远卡在 rejected 态；出错时清空缓存让下一次调用能重新发。
 */
import type { AclDeptTreeNode } from '#/api/acl';

import { ref } from 'vue';

import { GetDeptTree } from '#/api/acl';

/** 组织树本体（组件树渲染直接用它） */
export const aclDeptTree = ref<AclDeptTreeNode[]>([]);
/** 拉取中：给 a-tree-select 的 :loading 用，让空态显示"加载中"而不是"暂无部门" */
export const aclDeptTreeLoading = ref(false);
/** 上一次失败的错误消息；成功后清空。用于在 not-found 位置告诉用户为什么是空的 */
export const aclDeptTreeError = ref('');
/** 已加载树的 id 集合；用来判定「已删除/不存在的历史行」要不要补一个占位节点 */
export const aclDeptTreeIdSet = ref<Set<number>>(new Set());

let deptTreePromise: null | Promise<AclDeptTreeNode[]> = null;

function walkIds(list: AclDeptTreeNode[], acc: Set<number>) {
  for (const n of list) {
    acc.add(n.id);
    if (n.children?.length) walkIds(n.children, acc);
  }
}

/**
 * 首次调用发一次请求；后续调用共享同一个 promise。
 * 拉失败清空 promise，让下一次能重新触发（否则一个 500 会让整个页面会话都看不到部门）。
 */
export function ensureAclDeptTree(): Promise<AclDeptTreeNode[]> {
  if (deptTreePromise) return deptTreePromise;
  aclDeptTreeLoading.value = true;
  aclDeptTreeError.value = '';
  deptTreePromise = GetDeptTree()
    .then((list) => {
      const data = list ?? [];
      aclDeptTree.value = data;
      const ids = new Set<number>();
      walkIds(data, ids);
      aclDeptTreeIdSet.value = ids;
      return data;
    })
    .catch((error: any) => {
      deptTreePromise = null;
      aclDeptTreeError.value = error?.message || '部门树加载失败';
      return [] as AclDeptTreeNode[];
    })
    .finally(() => {
      aclDeptTreeLoading.value = false;
    });
  return deptTreePromise;
}

/**
 * 给单行组一份「已合并历史部门」的树：
 * 若当前行选中的 granteeId 不在服务端返回的树里（多半是部门被硬删了），补一个
 * disabled=true 的顶层节点用行上带的 grantee_name 显示；否则 a-tree-select 会
 * 回显成裸 id，看不出当时授给了谁，也没有把它"自动挨掉"（挨掉=静默撤一条授权）。
 */
export function buildDeptTreeForRow(
  base: AclDeptTreeNode[],
  idSet: Set<number>,
  rowId: number | undefined,
  fallbackLabel: string | undefined,
): AclDeptTreeNode[] {
  if (!rowId || idSet.has(rowId)) return base;
  const orphan: AclDeptTreeNode = {
    children: [],
    disabled: true,
    id: rowId,
    label: fallbackLabel || `已删除部门(${rowId})`,
    nodeType: 1,
    parentId: 0,
    sort: 0,
  };
  return [orphan, ...base];
}

/**
 * a-tree-select 的搜索匹配：不依赖组件内部 title 反射，改 fieldNames 后仍工作；
 * 大小写不敏感，与业务同学在其他搜索框里的习惯对齐。
 */
export function filterAclDeptNode(input: string, node: any): boolean {
  if (!input) return true;
  const text = String(node?.label ?? node?.title ?? '');
  return text.toLowerCase().includes(input.toLowerCase());
}
