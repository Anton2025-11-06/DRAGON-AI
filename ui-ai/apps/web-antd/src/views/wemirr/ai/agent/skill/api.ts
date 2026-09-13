import { defHttp } from '#/api/request';

const BASE_URL = '/api/workflow/skills';

export interface SkillPageReq {
  current: number;
  size: number;
  column?: string;
  asc?: boolean;
  name?: string;
  code?: string;
  category?: string;
  status?: boolean;
}

export interface SkillPageResp {
  id: number;
  name: string;
  code: string;
  description?: string;
  category?: string;
  icon?: string;
  tags?: string[];
  skillPath?: string;
  skillFile?: string;
  resourceCount?: number;
  status: boolean;
}

export interface SkillDetailResp extends SkillPageResp {
  skillContent?: string;
  resourceContents?: Record<string, string>;
  resources?: string[];
  tenantId?: number;
}

export interface SkillSaveReq {
  name: string;
  code: string;
  description?: string;
  category?: string;
  icon?: string;
  tags?: string[];
}

export interface SkillZipUploadReq extends SkillSaveReq {
  /** SKILL.zip 单文件压缩包（内含 SKILL.md） */
  file: File;
}

export interface SkillFileUpdateReq {
  content: string;
  path: string;
}

export const PageList = (params: SkillPageReq) =>
  defHttp.get<{ records: SkillPageResp[]; total: number }>(`${BASE_URL}/page`, {
    params,
  });

export const GetDetail = (id: number) =>
  defHttp.get<SkillDetailResp>(`${BASE_URL}/${id}/detail`);

export const PreviewSkill = (id: number) =>
  defHttp.get<SkillDetailResp>(`${BASE_URL}/${id}/preview`);

export const DownloadSkill = (item: SkillPageResp) =>
  defHttp.downloadFile(`${BASE_URL}/${item.id}/download`, `${item.code}.zip`, {
    method: 'GET',
  });

export const UpdateSkillFile = (id: number, data: SkillFileUpdateReq) =>
  defHttp.put(`${BASE_URL}/${id}/files`, data);

export const RenameObj = (id: number, name: string) =>
  defHttp.request(`${BASE_URL}/${id}/rename`, {
    method: 'PATCH',
    data: { name },
  });

const buildSkillFormData = (data: SkillZipUploadReq) => {
  const formData = new FormData();
  formData.append('name', data.name);
  formData.append('code', data.code);
  data.description && formData.append('description', data.description);
  data.category && formData.append('category', data.category);
  data.icon && formData.append('icon', data.icon);
  data.tags?.length && formData.append('tags', JSON.stringify(data.tags));
  formData.append('file', data.file);
  return formData;
};

export const AddObj = (data: SkillZipUploadReq) =>
  defHttp.post(`${BASE_URL}/upload`, buildSkillFormData(data), {
    headers: { 'Content-Type': 'multipart/form-data' },
  });

export const UpdateObj = (id: number, data: SkillZipUploadReq) =>
  defHttp.put(`${BASE_URL}/${id}/upload`, buildSkillFormData(data), {
    headers: { 'Content-Type': 'multipart/form-data' },
  });

export const DelObj = (id: number) => defHttp.delete(`${BASE_URL}/${id}`);

export const ToggleStatus = (id: number, status: boolean) =>
  defHttp.request(`${BASE_URL}/${id}/status`, {
    method: 'PATCH',
    params: { status },
  });
