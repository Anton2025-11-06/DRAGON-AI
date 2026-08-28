import type { NodeVariable, NodeWithVariables } from './VariableSelector.vue';

export const WORKFLOW_INPUT_SCOPE = 'inputs.';
export const WORKFLOW_NODE_SCOPE = 'nodes.';

export function buildWorkflowVariableReference(
  node: NodeWithVariables,
  variable: NodeVariable,
): string {
  if (node.type === 'START') {
    return `{{${WORKFLOW_INPUT_SCOPE}${variable.name}}}`;
  }
  return `{{${WORKFLOW_NODE_SCOPE}${node.id}.${variable.name}}}`;
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
