/**
 * 知识库 CRUD 配置
 */
import type {
  AddReq,
  CreateCrudOptionsProps,
  CreateCrudOptionsRet,
  DelReq,
  EditReq,
} from '@fast-crud/fast-crud';

import { dict } from '@fast-crud/fast-crud';

import { hiddenIdColumn } from '#/plugin/fast-crud/shared';

import * as api from './api';

export default function createKnowledgeBaseCrud(
  _props: CreateCrudOptionsProps,
): CreateCrudOptionsRet {
  // 模型字典
  const chatModelDict = dict({ data: [] });
  const embedModelDict = dict({ data: [] });
  const rerankModelDict = dict({ data: [] });

  /** 加载模型数据 */
  const loadModels = async () => {
    try {
      const [chatRes, embedRes, rerankRes] = await Promise.all([
        api.GetChatModels(),
        api.GetEmbeddingModels(),
        api.GetRerankModels(),
      ]);

      const mapModel = (m: any) => ({
        value: m.id,
        label: `${m.provider} / ${m.name}`,
      });

      chatModelDict.setData(
        (chatRes as any)?.map((m: any) => mapModel(m)) || [],
      );
      embedModelDict.setData(
        (embedRes as any)?.map((m: any) => mapModel(m)) || [],
      );
      rerankModelDict.setData(
        (rerankRes as any)?.map((m: any) => mapModel(m)) || [],
      );
    } catch (error) {
      console.error('加载模型数据失败:', error);
    }
  };

  loadModels();

  return {
    crudOptions: {
      request: {
        pageRequest: async (query: any) =>
          await api.KnowledgeBasePageList(query),
        addRequest: async ({ form }: AddReq) =>
          await api.AddKnowledgeBase(form),
        editRequest: async ({ form }: EditReq) =>
          await api.UpdateKnowledgeBase(form.id, form),
        delRequest: async ({ row }: DelReq) =>
          await api.DelKnowledgeBase(row.id),
      },
      // 隐藏表格相关
      table: { show: false },
      search: { show: false },
      toolbar: { show: false },
      actionbar: { show: false },
      pagination: { show: false },
      columns: {
        id: hiddenIdColumn,

        // ==================== 基础信息 ====================
        name: {
          title: '知识库名称',
          type: 'text',
          form: {
            rules: [{ required: true, message: '请输入知识库名称' }],
            component: { placeholder: '请输入知识库名称' },
          },
        },
        description: {
          title: '描述',
          type: 'textarea',
          form: {
            col: { span: 24 },
            component: { placeholder: '请输入描述', rows: 2 },
          },
        },

        // ==================== 模型配置 ====================
        embedModelId: {
          title: '向量模型',
          type: 'dict-select',
          dict: embedModelDict,
          form: {
            rules: [{ required: true, message: '请选择向量模型' }],
            helper: '创建后不可修改',
            component: { placeholder: '请选择向量模型' },
          },
          editForm: {
            component: { disabled: true },
          },
        },
        chatModelId: {
          title: '对话模型',
          type: 'dict-select',
          dict: chatModelDict,
          form: {
            helper: '用于知识库预览对话',
            component: { placeholder: '请选择对话模型', allowClear: true },
          },
        },
        rerankModelId: {
          title: '重排序模型',
          type: 'dict-select',
          dict: rerankModelDict,
          form: {
            helper: '可选，用于优化召回结果排序',
            component: { placeholder: '请选择重排序模型', allowClear: true },
          },
        },

        // ==================== 检索配置 ====================
        topK: {
          title: 'TopK',
          type: 'number',
          form: {
            value: 5,
            col: { span: 12 },
            helper: '单次召回数量',
            component: { min: 1, max: 20, placeholder: '5' },
          },
        },
        scoreThreshold: {
          title: '相似度阈值',
          type: 'number',
          form: {
            value: 0.6,
            col: { span: 12 },
            helper: '0.0-1.0，越高越严格',
            component: { min: 0, max: 1, step: 0.1, placeholder: '0.6' },
          },
        },

        // ==================== 分片配置 ====================
        chunkSize: {
          title: '分片大小',
          type: 'number',
          form: {
            value: 512,
            col: { span: 12 },
            helper: '文档分片大小 (Token)',
            component: { min: 100, max: 2000, placeholder: '512' },
          },
        },
        chunkOverlap: {
          title: '分片重叠',
          type: 'number',
          form: {
            value: 64,
            col: { span: 12 },
            helper: '相邻分片重叠 (Token)',
            component: { min: 0, max: 500, placeholder: '64' },
          },
        },

        // ==================== 高级配置 ====================
        enableGraph: {
          title: '知识图谱',
          type: 'dict-switch',
          dict: dict({
            data: [
              { value: true, label: '启用' },
              { value: false, label: '禁用' },
            ],
          }),
          form: {
            value: false,
            helper: '启用后支持图谱化检索',
            component: {
              checkedChildren: '启用',
              unCheckedChildren: '禁用',
            },
          },
        },
      },
      form: {
        labelWidth: '100px',
        wrapper: {
          width: '50%',
        },
      },
    },
  };
}
