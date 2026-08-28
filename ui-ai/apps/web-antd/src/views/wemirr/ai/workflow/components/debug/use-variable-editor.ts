/**
 * 变量编辑器 Hook
 * 提供变量编辑功能，包括 JSON 验证和后端 API 调用
 *
 */

import { ref } from 'vue';

import { message } from 'ant-design-vue';

import { updateVariable } from '#/api/ai-workflow';

/**
 * 变量编辑状态
 */
export interface VariableEditState {
  /** 节点ID */
  nodeId: string;
  /** 节点名称 */
  nodeName: string;
  /** 变量名 */
  variableName: string;
  /** 原始值 */
  originalValue: any;
}

/**
 * JSON 验证结果
 */
export interface JsonValidationResult {
  /** 是否有效 */
  valid: boolean;
  /** 解析后的值 */
  value?: any;
  /** 错误信息 */
  error?: string;
}

/**
 * 变量编辑器 Hook
 * @param executionId 执行ID（响应式引用）
 */
export function useVariableEditor(executionId: () => null | string) {
  /** 是否正在编辑 */
  const isEditing = ref(false);

  /** 当前编辑状态 */
  const editState = ref<null | VariableEditState>(null);

  /** 编辑值（JSON 字符串） */
  const editValue = ref('');

  /** JSON 验证错误 */
  const validationError = ref<null | string>(null);

  /** 是否正在保存 */
  const isSaving = ref(false);

  /**
   * 验证 JSON 格式
   */
  function validateJson(jsonString: string): JsonValidationResult {
    if (!jsonString.trim()) {
      return {
        valid: false,
        error: 'JSON 值不能为空',
      };
    }

    try {
      const value = JSON.parse(jsonString);
      return {
        valid: true,
        value,
      };
    } catch (error) {
      return {
        valid: false,
        error: error instanceof Error ? error.message : '无效的 JSON 格式',
      };
    }
  }

  /**
   * 开始编辑变量
   */
  function startEdit(
    nodeId: string,
    nodeName: string,
    variableName: string,
    currentValue: any,
  ) {
    editState.value = {
      nodeId,
      nodeName,
      variableName,
      originalValue: currentValue,
    };
    editValue.value = JSON.stringify(currentValue, null, 2);
    validationError.value = null;
    isEditing.value = true;
  }

  /**
   * 取消编辑
   */
  function cancelEdit() {
    isEditing.value = false;
    editState.value = null;
    editValue.value = '';
    validationError.value = null;
  }

  /**
   * 更新编辑值并验证
   */
  function updateEditValue(value: string) {
    editValue.value = value;
    const result = validateJson(value);
    validationError.value = result.valid ? null : result.error || '无效的 JSON';
  }

  /**
   * 保存变量编辑
   */
  async function saveEdit(): Promise<boolean> {
    if (!editState.value) {
      message.error('没有正在编辑的变量');
      return false;
    }

    const execId = executionId();
    if (!execId) {
      message.error('执行ID不存在，无法更新变量');
      return false;
    }

    // 验证 JSON
    const validation = validateJson(editValue.value);
    if (!validation.valid) {
      validationError.value = validation.error || '无效的 JSON';
      message.error('JSON 格式无效');
      return false;
    }

    isSaving.value = true;

    try {
      // 构建完整的变量名（包含节点ID前缀）
      const fullVariableName = `${editState.value.nodeId}.${editState.value.variableName}`;

      // 调用后端 API 更新变量
      await updateVariable(execId, fullVariableName, validation.value);

      message.success('变量已更新');
      cancelEdit();
      return true;
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : '更新变量失败';
      message.error(errorMessage);
      return false;
    } finally {
      isSaving.value = false;
    }
  }

  /**
   * 检查值是否已修改
   */
  function isModified(): boolean {
    if (!editState.value) return false;

    const validation = validateJson(editValue.value);
    if (!validation.valid) return true; // 如果无效，认为已修改

    return (
      JSON.stringify(validation.value) !==
      JSON.stringify(editState.value.originalValue)
    );
  }

  /**
   * 重置为原始值
   */
  function resetToOriginal() {
    if (editState.value) {
      editValue.value = JSON.stringify(editState.value.originalValue, null, 2);
      validationError.value = null;
    }
  }

  /**
   * 格式化 JSON
   */
  function formatJson() {
    const validation = validateJson(editValue.value);
    if (validation.valid) {
      editValue.value = JSON.stringify(validation.value, null, 2);
    }
  }

  /**
   * 压缩 JSON
   */
  function minifyJson() {
    const validation = validateJson(editValue.value);
    if (validation.valid) {
      editValue.value = JSON.stringify(validation.value);
    }
  }

  return {
    // 状态
    isEditing,
    editState,
    editValue,
    validationError,
    isSaving,

    // 方法
    validateJson,
    startEdit,
    cancelEdit,
    updateEditValue,
    saveEdit,
    isModified,
    resetToOriginal,
    formatJson,
    minifyJson,
  };
}

export type UseVariableEditorReturn = ReturnType<typeof useVariableEditor>;
