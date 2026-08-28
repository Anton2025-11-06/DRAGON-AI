import { defHttp } from '#/api/request';

/**
 * 后端部门树节点 {dept_id, parent_id, dept_name, sort, status, children}
 * 转换为前端树组件所需结构 {id, key, parentId, label, weight, children}
 */
function toDeptTree(list: any[]): any[] {
  return (list ?? []).map((node) => ({
    id: node.dept_id,
    key: String(node.dept_id),
    parentId: node.parent_id,
    label: node.dept_name,
    weight: node.sort ?? 0,
    status: node.status,
    children: toDeptTree(node.children ?? []),
  }));
}

/**
 * 获取部门机构树（GET /api/system/depts/tree）
 */
export async function getOrgTree(query: any) {
  const data = await defHttp.get('/api/system/depts/tree', { params: query });
  return toDeptTree(data);
}