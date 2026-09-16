import type { NodeVariable, NodeWithVariables } from './VariableSelector.vue';

export const WORKFLOW_INPUT_SCOPE = 'inputs.';
export const WORKFLOW_NODE_SCOPE = 'nodes.';

/**
 * 子路径允许的字符：字段名（英文/数字/下划线/中划线/中文）、点、方括号、负下标。
 * 后端 ExecutionContext._dig 按 . [ ] 切分逐层取值，因此不支持通配与过滤器。
 */
const VARIABLE_PATH_ALLOWED_REGEX = /^[[\].\w\u4E00-\u9FA5-]+$/;

/**
 * 规范化手写的 JSONPath 风格子路径（再次提取）。
 *
 * 接受写法：`data.list` / `.data.list` / `[0]` / `output[0].name` / `$.data[0]`，
 * 统一返回以 `.` 或 `[` 开头的后缀（拼到变量引用后面）；空输入返回空串；
 * 含非法字符或括号不配对时返回 null，由调用方提示用户。
 */
export function normalizeVariablePathSuffix(raw: string): null | string {
  const trimmed = (raw || '').trim().replace(/^\$\.?/, '');
  if (!trimmed) return '';
  if (!VARIABLE_PATH_ALLOWED_REGEX.test(trimmed)) return null;
  const bracketCount = trimmed.match(/\[/g)?.length ?? 0;
  const closedCount = trimmed.match(/\]/g)?.length ?? 0;
  if (bracketCount !== closedCount) return null;
  return (trimmed.startsWith('[') ? trimmed : `.${trimmed}`).replaceAll(
    /\.{2,}/g,
    '.',
  );
}

export function buildWorkflowVariableReference(
  node: NodeWithVariables,
  variable: NodeVariable,
  /** 子路径/下标后缀（normalizeVariablePathSuffix 的返回值，可为空） */
  pathSuffix = '',
): string {
  if (node.type === 'START') {
    return `{{${WORKFLOW_INPUT_SCOPE}${variable.name}${pathSuffix}}}`;
  }
  return `{{${WORKFLOW_NODE_SCOPE}${node.id}.${variable.name}${pathSuffix}}}`;
}

export function formatWorkflowVariableReferenceLabel(
  referencePath: string,
  resolveNodeLabel?: (nodeId: string) => string | undefined,
): string {
  const normalizedPath = normalizeWorkflowVariableReference(referencePath);
  if (normalizedPath.startsWith(WORKFLOW_INPUT_SCOPE)) {
    const variablePath = normalizedPath.slice(WORKFLOW_INPUT_SCOPE.length);
    return variablePath ? `开始输入.${variablePath}` : '开始输入';
  }

  if (normalizedPath.startsWith(WORKFLOW_NODE_SCOPE)) {
    const rest = normalizedPath.slice(WORKFLOW_NODE_SCOPE.length);
    const [nodeId, ...variableParts] = rest.split('.');
    if (!nodeId) return normalizedPath;
    const variablePath = variableParts.join('.');
    const nodeLabel = resolveNodeLabel?.(nodeId) || nodeId;
    return variablePath ? `${nodeLabel}.${variablePath}` : nodeLabel;
  }

  return normalizedPath;
}

function normalizeWorkflowVariableReference(referencePath: string): string {
  const trimmed = referencePath.trim();
  if (trimmed.startsWith('{{') && trimmed.endsWith('}}')) {
    return trimmed.slice(2, -2).trim();
  }
  return trimmed;
}
