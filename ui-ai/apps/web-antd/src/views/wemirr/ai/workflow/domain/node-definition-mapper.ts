import type { WorkflowNodeDefinition } from './node-definitions';

import type { WorkflowNodeDefinitionResp } from '#/api/ai-workflow/types';

export function mapBackendNodeDefinition(
  definition: WorkflowNodeDefinitionResp,
): WorkflowNodeDefinition {
  return {
    type: definition.type,
    displayName: definition.displayName,
    description: definition.description,
    category: definition.category,
    icon: definition.icon,
    color: definition.color,
    inputs: definition.inputs,
    outputs: definition.outputs,
    formComponent: definition.configSchema.formComponent,
    defaultConfig: { ...definition.defaultConfig },
  };
}

export function mapBackendNodeDefinitions(
  definitions: WorkflowNodeDefinitionResp[],
): WorkflowNodeDefinition[] {
  return definitions.map(mapBackendNodeDefinition);
}
