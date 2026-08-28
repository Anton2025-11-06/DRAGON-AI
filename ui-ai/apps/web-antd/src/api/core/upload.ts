import type { AxiosRequestConfig } from '@vben/request';

import { requestClient } from '#/api/request';

/**
 * Axios上传进度事件
 */
export type AxiosProgressEvent = AxiosRequestConfig['onUploadProgress'];

/**
 * 默认上传结果
 */
export interface UploadResult {
  url: string;
  fileName: string;
  ossId: string;
}

/**
 * 通过单文件上传接口
 * @param file 上传的文件
 * @param options 一些配置项
 * @param options.onUploadProgress 上传进度事件
 * @param options.signal 上传取消信号
 * @param options.otherData 其他请求参数 后端拓展可能会用到
 * @returns 上传结果
 */
export function uploadApi(
  file: Blob | File,
  options?: {
    onUploadProgress?: AxiosProgressEvent;
    otherData?: Record<string, any>;
    signal?: AbortSignal;
  },
) {
  const { onUploadProgress, signal, otherData = {} } = options ?? {};
  // 本项目后端暂无文件上传接口，先指向网关统一前缀，后续由后端补充
  return requestClient.upload<UploadResult>(
    '/api/system/files/upload',
    { file, ...otherData },
    { onUploadProgress, signal, timeout: 60_000 },
  );
}

/**
 * 上传api type
 */
export type UploadApi = typeof uploadApi;
