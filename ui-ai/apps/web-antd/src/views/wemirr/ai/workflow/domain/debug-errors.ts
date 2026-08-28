export const WorkflowDebugErrorType = {
  NETWORK: 'NETWORK_ERROR',
  TIMEOUT: 'TIMEOUT_ERROR',
  VALIDATION: 'VALIDATION_ERROR',
  EXECUTION: 'EXECUTION_ERROR',
  CONFIGURATION: 'CONFIGURATION_ERROR',
  PERMISSION: 'PERMISSION_ERROR',
  RESOURCE: 'RESOURCE_ERROR',
  UNKNOWN: 'UNKNOWN_ERROR',
} as const;

export type WorkflowDebugErrorType =
  (typeof WorkflowDebugErrorType)[keyof typeof WorkflowDebugErrorType];

export const WORKFLOW_DEBUG_ERROR_TYPES = Object.values(WorkflowDebugErrorType);
