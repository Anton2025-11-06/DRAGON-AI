/**
 * AI 工作流端口契约。
 * 前端画布、保存图、后端执行统一使用这些端口 ID。
 */

export const INPUT_HANDLE = 'input';
export const OUTPUT_HANDLE = 'output';
export const BRANCH_HANDLE_PREFIX = 'branch:';

export type WorkflowHandle =
  | `${typeof BRANCH_HANDLE_PREFIX}${string}`
  | typeof INPUT_HANDLE
  | typeof OUTPUT_HANDLE;

export function outputHandleForBranch(branchId: string): WorkflowHandle {
  return `${BRANCH_HANDLE_PREFIX}${branchId}`;
}

export function assertWorkflowHandle(
  handle?: null | string,
): undefined | WorkflowHandle {
  if (!handle) {
    return undefined;
  }
  if (
    handle === INPUT_HANDLE ||
    handle === OUTPUT_HANDLE ||
    handle.startsWith(BRANCH_HANDLE_PREFIX)
  ) {
    return handle as WorkflowHandle;
  }
  throw new Error(`非法工作流端口: ${handle}`);
}
