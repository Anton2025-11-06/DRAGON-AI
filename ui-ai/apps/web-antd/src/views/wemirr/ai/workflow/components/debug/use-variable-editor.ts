/**
 * 变量编辑器 Hook
 * 提供变量编辑的表单态与 JSON 校验
 *
 * 注：变量编辑不再直连后端（原 update_variable 接口已随外部暂停链路废弃）。
 * 落库的编辑只能作为审批结论的一部分，以 (源节点id, 变量名, 新值) 三元组
 * 随提交接口下发；此处的编辑仅用于面板内的临时查看与草稿构造。
 */

import { ref } from 'vue';

import { message } from 'ant-design-vue';

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
 * @param executionId 执行ID（响应式引用，仅供调用方定位上下文）
 */
export function useVariableEditor(executionId: () => null | string) {
  // 编辑不落库，因此不再需要执行ID发起请求；保留入参以不破坏调用方
  void executionId;

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
   * 保存变量编辑（仅本地生效，不落库）
   */
  async function saveEdit(): Promise<boolean> {
    if (!editState.value) {
      message.error('没有正在编辑的变量');
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
      // 编辑结果交由调用方写入本地视图；需要影响执行流程的改动走审批表单的 edits
      cancelEdit();
      return true;
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
