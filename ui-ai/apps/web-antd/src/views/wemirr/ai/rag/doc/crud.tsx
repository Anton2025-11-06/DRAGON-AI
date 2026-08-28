import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  EditReq,
} from '@fast-crud/fast-crud';

import { h } from 'vue';

import {
  ApartmentOutlined,
  EyeOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import { dict } from '@fast-crud/fast-crud';
import { message } from 'ant-design-vue';

import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';

function resolveNativeUploadFile(value: any): File | null {
  if (value instanceof File) return value;
  if (Array.isArray(value)) return resolveNativeUploadFile(value[0]);
  const candidate =
    value?.originFileObj || value?.raw || value?.file?.originFileObj || value?.file;
  return candidate && candidate !== value
    ? resolveNativeUploadFile(candidate)
    : null;
}

export default function crud(
  props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  const {
    selectedKnowledgeBase,
    vectorizeDocument,
    graphizeDocument,
    startVectorizeStatusPolling,
    openPreviewModal,
    openPreviewChunkModal,
  } = props.context;

  // 格式化文件大小
  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${Number.parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
  };

  // 向量化状态字典
  const vectorizeStatusDict = dict({
    data: [
      { value: 'PENDING', label: '未向量化', color: 'default' },
      { value: 'PROCESSING', label: '向量化中', color: 'processing' },
      { value: 'PROCESSED', label: '已向量化', color: 'success' },
      { value: 'FAILED', label: '向量化失败', color: 'error' },
    ],
  });

  // 图谱化状态字典
  const graphizeStatusDict = dict({
    data: [
      { value: false, label: '未图谱化', color: 'default' },
      { value: true, label: '已图谱化', color: 'success' },
    ],
  });

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) => {
          // 如果没有选中知识库，返回空结果（需要符合 fast-crud 分页格式）
          if (!selectedKnowledgeBase.value) {
            return {
              current: 1,
              size: query.pageSize || 10,
              total: 0,
              records: [],
            };
          }
          // 使用选中的知识库ID查询文档
          const result = await api.PageList({
            ...query,
            knowledgeBaseId: selectedKnowledgeBase.value.id,
          });
          result.records
            .filter((row) => row.status === 'PROCESSING')
            .forEach((row) => startVectorizeStatusPolling?.(row.id));
          return result;
        },
        addRequest: async ({ form }: AddReq) => {
          // 如果没有选中知识库，提示错误
          if (!selectedKnowledgeBase.value) {
            throw new Error('请先选择知识库');
          }
          const file = resolveNativeUploadFile(form.file);
          if (!file) {
            throw new Error('请选择文件');
          }
          // 调用上传API
          return await api.UploadDocument(
            selectedKnowledgeBase.value.id,
            file,
          );
        },
        editRequest: async ({ form }: EditReq) =>
          await api.UpdateObj(form.id, form),
        delRequest: async ({ row }: any) => await api.DelObj(row.id),
      },
      toolbar: {},
      actionbar: {
        show: true,
        buttons: {
          add: {
            show: true,
            text: '上传文档',
            icon: h(UploadOutlined),
            type: 'primary',
          },
        },
      },
      rowHandle: {
        width: 300,
        buttons: {
          edit: { show: true, text: '编辑', title: '编辑文档' },
          remove: { show: true, text: '删除', title: '删除文档' },
          preview: {
            type: 'link',
            text: '预览',
            size: 'small',
            icon: h(EyeOutlined),
            title: '预览文档完整内容',
            show: true,
            click(context: any) {
              if (openPreviewModal) {
                openPreviewModal(context.row.id, context.row.title);
              }
            },
          },
          previewChunk: {
            type: 'link',
            text: '分块',
            size: 'small',
            icon: h(FileTextOutlined),
            title: '预览文档分块内容',
            show: true,
            click(context: any) {
              if (openPreviewChunkModal) {
                openPreviewChunkModal(context.row.id, context.row.title);
              }
            },
          },
          vectorize: {
            type: 'link',
            text: '向量化',
            size: 'small',
            icon: h(ThunderboltOutlined),
            title: '提交向量化任务',
            show: true,
            async click(context: any) {
              // 只对未向量化和向量化失败的文档显示按钮
              if (context.row.vectorized) {
                message.error('文档已向量化，不可重复向量化');
                return;
              }
              try {
                await vectorizeDocument(context.row.id);
                message.success('向量化任务已提交，正在处理中...');

                // 开始轮询状态
                if (startVectorizeStatusPolling) {
                  startVectorizeStatusPolling(context.row.id);
                }

                // 立即刷新列表以显示"向量化中"状态
                if (props.crudExpose?.doRefresh) {
                  await props.crudExpose.doRefresh();
                }
              } catch (error) {
                console.error('向量化任务提交失败:', error);
                message.error('向量化任务提交失败');
              }
            },
          },
          graphize: {
            type: 'link',
            text: '图谱化',
            size: 'small',
            icon: h(ApartmentOutlined),
            title: '提交图谱化任务',
            show: true,
            async click(context: any) {
              // 只对未图谱化的文档显示按钮
              if (context.row.graphed) {
                message.warning('文档已图谱化');
                return;
              }
              try {
                const result = await graphizeDocument(context.row.id);
                if (result.success) {
                  message.success(`图谱化完成，创建了 ${result.nodesCreated} 个节点和 ${result.relationshipsCreated} 个关系`);
                } else {
                  message.error(result.message || '图谱化失败');
                }
                // 刷新列表
                if (props.crudExpose?.doRefresh) {
                  await props.crudExpose.doRefresh();
                }
              } catch (error) {
                console.error('图谱化任务提交失败:', error);
                message.error('图谱化任务提交失败');
              }
            },
          },
        },
      },
      columns: {
        id: hiddenIdColumn,
        file: {
          title: '文件',
          type: 'file-uploader',
          column: { show: false },
          search: { show: false },
          form: {
            show: false,
          },
          addForm: {
            show: true,
            rules: [{ required: true, message: '请选择文件' }],
            component: {
              beforeUpload: () => false,
              limit: 1,
              valueType: 'object',
            },
            valueBuilder({ form }: any) {
              // 初始化为空
              form.file = null;
            },
            valueResolve({ form }: any) {
              form.file = resolveNativeUploadFile(form.file);
            },
          },
        },
        title: {
          title: '文档名称',
          type: 'text',
          search: { show: true },
          column: { ellipsis: true, width: 200 },
          form: {
            show: true,
          },
          addForm: { show: false },
        },
        fileSize: {
          title: '文件大小',
          type: 'text',
          form: { show: false },
          column: {
            width: 120,
            formatter: ({ value }) => formatFileSize(value),
          },
        },
        contentType: {
          title: '文件类型',
          type: 'text',
          search: { show: true },
          column: { width: 100 },
        },
        status: {
          title: '向量化状态',
          type: 'dict-select',
          search: { show: true },
          column: { width: 120 },
          dict: vectorizeStatusDict,
          form: {
            rules: [{ required: true, message: '请选择向量化状态' }],
          },
        },
        graphed: {
          title: '图谱化状态',
          type: 'dict-select',
          search: { show: true },
          column: { width: 120 },
          dict: graphizeStatusDict,
          form: { show: false },
        },
      },
    },
  };
}
