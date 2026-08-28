import { defHttp } from '#/api/request';

export function save(obj: any) {
  return obj.dept_id ? UpdateObj(obj) : AddObj(obj);
}

export function AddObj(obj: any) {
  // POST /api/system/depts
  return defHttp.post('/api/system/depts', obj);
}

export function UpdateObj(obj: any) {
  // PUT /api/system/depts/{dept_id}
  return defHttp.put(`/api/system/depts/${obj.dept_id}`, obj);
}

export function DelObj(id: any) {
  // DELETE /api/system/depts/{dept_id}
  return defHttp.delete(`/api/system/depts/${id}`);
}
