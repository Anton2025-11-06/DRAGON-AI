import { defHttp } from '#/api/request';

// 智能体分页查询
export function pageList(params: any) {
  return defHttp.post('/api/workflow/chat-agents/page', params);
}

// 获取智能体详情
export function getDetail(id: number) {
  return defHttp.get(`/api/workflow/chat-agents/${id}/detail`);
}

// 新增智能体
export function create(data: any) {
  return defHttp.post('/api/workflow/chat-agents/create', data);
}

// 修改智能体
export function modify(id: number, data: any) {
  return defHttp.put(`/api/workflow/chat-agents/${id}/modify`, data);
}

// 删除智能体
export function remove(id: number) {
  return defHttp.delete(`/api/workflow/chat-agents/${id}`);
}

// 上传头像
export function uploadAvatar(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return defHttp.post('/api/workflow/chat-agents/avatar', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
}

// 获取可用工具类列表
export function getTools() {
  return defHttp.get('/api/workflow/tools');
}

// 获取知识库列表（分页接口，设置大页数，归属 service_rag）
export function getKnowledgeBaseList() {
  return defHttp.post('/api/rag/knowledge-bases/page', {
    current: 1,
    size: 1000,
  });
}

// 获取模型列表（分页接口，设置大页数）
export function getModelList() {
  return defHttp.get('/api/workflow/models/list');
}

// 将工具类名称列表转换为配置JSON
export function convertToolClassNamesToConfig(toolClassNames: string[]) {
  return defHttp.post('/api/workflow/tools/classes/convert', toolClassNames);
}
