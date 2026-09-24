/**
 * AI 工作流类型定义
 * 对应后端 AI 工作流编排功能的数据结构
 * 同步自后端 Java 配置类
 */

// ==================== 枚举类型 ====================

/**
 * 节点类型枚举
 * 基于 LangChain4j workflow runtime 的编排语义
 * 同步自: com.wemirr.platform.ai.core.enums.NodeType
 */
export type NodeType =
  // 工作流边界
  | 'APPROVAL' // 人工审批（暂停整条流等结论，由「再提交」接口带回）
  | 'CODE' // 代码
  | 'DOC_EXTRACTOR' // 文档读取
  // 智能体节点
  | 'END' // 结束/回答
  | 'HTTP_REQUEST' // HTTP请求
  | 'IF_ELSE' // 条件分支
  | 'KNOWLEDGE_RETRIEVAL' // 知识检索
  // 控制流节点
  | 'LIST_OPERATOR' // 列表处理
  | 'LLM' // 大模型
  | 'LOOP' // 循环
  | 'MCP_TOOL' // MCP 工具
  | 'PARALLEL' // 并行
  | 'PARAMETER_EXTRACTOR' // 结构化提取 Agent
  // 能力节点
  | 'QUESTION_CLASSIFIER' // 分类路由 Agent
  | 'REPLY' // 指定回复
  | 'START' // 用户输入
  | 'TEMPLATE' // 模板转换
  | 'TOOL' // 工具
  // 外部系统节点
  | 'VARIABLE_AGGREGATOR' // 变量聚合
  | 'VARIABLE_ASSIGNER' // 变量赋值
  | 'WORKFLOW'; // 工作流（嵌套调用平台内另一条已发布工作流）

/**
 * 工作流状态枚举
 */
export type WorkflowStatus = 'DRAFT' | 'PUBLISHED';

/**
 * 执行状态枚举
 */
export type ExecutionStatus =
  | 'CANCELLED'
  | 'COMPLETED'
  | 'FAILED'
  | 'PAUSED'
  | 'PENDING'
  | 'RUNNING';

/**
 * 节点执行状态
 * AWAITING = 审批节点挂起等人工结论（非终态，恢复提交时必然重跑该节点）
 * TIMEOUT / CANCELLED = 并行屏障「任一完成」短路掉的分支（审批不同意不再砍节点）
 */
export type NodeExecutionStatus =
  | 'AWAITING'
  | 'CANCELLED'
  | 'COMPLETED'
  | 'FAILED'
  | 'PENDING'
  | 'RUNNING'
  | 'TIMEOUT';

/**
 * 模板分类枚举
 */
export type TemplateCategory =
  | 'CONVERSATION'
  | 'CUSTOM'
  | 'EXTRACTION'
  | 'GENERATION'
  | 'RAG'
  | 'SUMMARY';

// ==================== 基础数据结构 ====================

/**
 * 节点位置
 */
export interface NodePosition {
  x: number;
  y: number;
}

/**
 * 工作流节点
 */
export interface WorkflowNode {
  /** 节点ID */
  id: string;
  /** 节点类型 */
  type: NodeType;
  /** 节点标签/名称 */
  label: string;
  /** 节点位置 */
  position: NodePosition;
  /** 节点配置数据 */
  data: Record<string, any>;
}

export type WorkflowHandle = 'input' | 'output' | `branch:${string}`;

/**
 * 工作流边
 */
export interface WorkflowEdge {
  /** 边ID */
  id: string;
  /** 源节点ID */
  source: string;
  /** 源端口ID */
  sourceHandle?: WorkflowHandle;
  /** 目标节点ID */
  target: string;
  /** 目标端口ID */
  targetHandle?: WorkflowHandle;
}

/**
 * 工作流图定义
 */
export interface WorkflowGraph {
  /** 节点列表 */
  nodes: WorkflowNode[];
  /** 边列表 */
  edges: WorkflowEdge[];
}

export type WorkflowDiagnosticSeverity = 'ERROR' | 'SUGGESTION' | 'WARNING';

/**
 * 工作流诊断问题
 */
export interface WorkflowDiagnosticIssue {
  /** 问题代码 */
  code: string;
  /** 严重级别 */
  severity: WorkflowDiagnosticSeverity;
  /** 节点ID */
  nodeId?: string;
  /** 节点名称 */
  nodeLabel?: string;
  /** 节点类型 */
  nodeType?: NodeType;
  /** 问题说明 */
  message: string;
  /** 修复建议 */
  suggestion?: string;
}

/**
 * 变量定义
 */
export interface VariableDefinition {
  /** 变量名 */
  name: string;
  /** 变量类型 */
  type: 'array' | 'boolean' | 'number' | 'object' | 'string';
  /** 默认值 */
  defaultValue?: any;
  /** 描述 */
  description?: string;
  /** 是否必填 */
  required?: boolean;
}

// ==================== 请求类型 ====================

/**
 * 工作流分页查询请求
 */
export interface WorkflowPageReq {
  /** 当前页码 */
  current?: number;
  /** 每页大小 */
  size?: number;
  /** 工作流名称 */
  name?: string;
  /** 状态 */
  status?: WorkflowStatus;
}

/**
 * 工作流保存请求
 */
export interface WorkflowSaveReq {
  /** 工作流名称 */
  name: string;
  /** 工作流描述 */
  description?: string;
  /** 工作流图定义 */
  graph?: WorkflowGraph;
  /** 输入变量定义 */
  inputVariables?: VariableDefinition[];
  /** 输出变量定义 */
  outputVariables?: VariableDefinition[];
  /** 变更说明 */
  changeLog?: string;
}

/**
 * 提交后可选的两种结论；两者都继续走下游，分流由下游条件节点拿 review 自己判
 */
export type ApprovalAction = 'APPROVE' | 'REJECT';

/**
 * 审批节点的暂停范围（只决定「等人工结论时停多大范围」，不决定拒绝后砍谁）
 * DOWNSTREAM = 仅本节点及下游等待；ALL = 整条工作流一起停（在跑分支会被取消，恢复后重跑）
 */
export type ApprovalPauseScope = 'ALL' | 'DOWNSTREAM';

/**
 * 一份审批结论：凭哪份待办答、答什么、改了哪些字段
 */
export interface ApprovalDecisionReq {
  /** pendingApprovals[].approvalToken；结论落到哪条执行的哪个节点由后端按它解析 */
  approvalToken: string;
  action: ApprovalAction;
  /** 键 = editableFields[].name，值 = 改后的完整值 */
  fieldValues?: Record<string, any>;
  /** 审批意见 */
  opinion?: string;
}

/**
 * submit 的唯一入参（新建会话与后续动作共用一个类，意图只看给了哪几个键）
 */
export interface WorkflowSubmitReq {
  /** 不传=新建会话；传=对已有会话的任一后续动作 */
  executionId?: string;
  /** 新建时必填；带 executionId 时可省 */
  workflowId?: number | string;
  /** 业务输入；不传沿用上轮 inputs */
  values?: Record<string, any>;
  /** 审批结论，一份待办一条 */
  decisions?: ApprovalDecisionReq[];
  /** 放弃未答审批并全量重跑；仅带 executionId 时有意义 */
  restart?: boolean;
}

/**
 * submit 的唯一返回体（四种意图同形，调用方不需按分支解析）
 */
export interface SubmitResult {
  executionId: string;
  status: ExecutionStatus;
  /** 本次提交后的当前挂起代次 */
  pauseGeneration: number;
  /** 重复答复同一份凭据：后台未做任何动作 */
  duplicated?: boolean;
  /** 提交后仍欠的审批 */
  pendingApprovals?: PendingApproval[];
}

/**
 * 一份待办里审批方能改的那一行
 * `name` 是审批节点透传给下游的输出键名，同一份清单内天然唯一，回传时就拿它当 fieldValues 的键
 */
export interface EditableApprovalField {
  name: string;
  /** 给人看的字段名 */
  label: string;
  /** 当前值（没填过的审批入参是空数组） */
  value?: any;
  /**
   * 值形状标记：目前只有开始节点的「审批入参」（APPROVER）会带，它的值契约是数组
   *——按普通文本框渲染会把一个工号提交成字符串
   */
  valueType?: string;
  /** 这行值是不是必须给出（标记位，面板据此提醒） */
  required?: boolean;
  /** 值从哪个节点来，只用于展示 */
  sourceNodeLabel?: string;
}

/**
 * 此刻欠人答的一份审批（详情 pendingApprovals 的元素，审批面板的数据源）
 * 结论要落到哪条执行的哪个节点是内部事实，不外发：答复只认 approvalToken
 */
export interface PendingApproval {
  /** 一次性凭据：答完即失效，重开一轮全部换号 */
  approvalToken: string;
  /** 本执行里正挂着的那个节点 id（画布高亮用，等子流程时就是那个【工作流】节点） */
  nodeId: string;
  nodeLabel: string;
  /** 给人看的那句话；等子流程时会说清是哪条子流的哪一道 */
  title: string;
  /** 真正出结论的那道审批节点名 */
  approvalNodeLabel: string;
  allowedActions: ApprovalAction[];
  editableFields: EditableApprovalField[];
}

/**
 * 执行历史分页查询请求
 */
export interface WorkflowExecutionPageReq {
  /** 当前页码 */
  current?: number;
  /** 每页大小 */
  size?: number;
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  workflowId?: string;
  /** 执行状态 */
  status?: ExecutionStatus;
}

/**
 * 模板分页查询请求
 */
export interface WorkflowTemplatePageReq {
  /** 当前页码 */
  current?: number;
  /** 每页大小 */
  size?: number;
  /** 模板名称 */
  name?: string;
  /** 模板分类 */
  category?: TemplateCategory;
  /** 是否只查询内置模板 */
  builtInOnly?: boolean;
}

/**
 * 模板保存请求
 */
export interface WorkflowTemplateSaveReq {
  /** 模板名称 */
  name: string;
  /** 模板描述 */
  description?: string;
  /** 模板分类 */
  category: TemplateCategory;
  /** 模板图标 */
  icon?: string;
  /** 工作流图定义 */
  graph?: WorkflowGraph;
}

// ==================== 响应类型 ====================

/**
 * 工作流分页响应
 */
export interface WorkflowPageResp {
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  /** 工作流名称 */
  name: string;
  /** 工作流描述 */
  description?: string;
  /** 当前版本号 */
  currentVersion: number;
  /** 状态 */
  status: WorkflowStatus;
  /** 节点数量 */
  nodeCount: number;
  /** 创建时间 */
  createTime: string;
  /** 更新时间 */
  updateTime: string;
}

/**
 * 工作流详情响应
 */
export interface WorkflowDetailResp {
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  /** 工作流名称 */
  name: string;
  /** 工作流描述 */
  description?: string;
  /** 工作流图定义 */
  graph?: WorkflowGraph;
  /** 输入变量定义 */
  inputVariables?: VariableDefinition[];
  /** 输出变量定义 */
  outputVariables?: VariableDefinition[];
  /** 当前版本号 */
  currentVersion: number;
  /** 状态 */
  status: WorkflowStatus;
  /** 创建时间 */
  createTime: string;
  /** 更新时间 */
  updateTime: string;
}

/**
 * 工作流版本响应
 */
export interface WorkflowVersionResp {
  /** 版本ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  workflowId: string;
  /** 版本号 */
  version: number;
  /** 工作流图快照 */
  graphSnapshot?: WorkflowGraph;
  /** 变更说明 */
  changeLog?: string;
  /** 是否已发布 */
  published: boolean;
  /** 创建人ID (字符串类型，避免 JavaScript 大数精度丢失) */
  createdBy: string;
  /** 创建时间 */
  createdTime: string;
}

/**
 * 节点执行状态
 */
export interface NodeExecutionState {
  /** 执行顺序（从1开始） */
  order?: number;
  /** 执行状态 */
  status?: NodeExecutionStatus | string;
  /** 所属分支/并行端口 id */
  branch?: string;
  /** 本轮沿用（未重跑）标记：带 decisions 的续跑里跳过上轮已完成节点 */
  skip?: boolean;
  /** 节点「返回内容」开关的执行时快照（父工作流取子执行结果时按它筛对外可见的输出） */
  emitted?: boolean;
  /** 输入数据 */
  input?: Record<string, any>;
  /** 输出数据 */
  output?: Record<string, any>;
  /** 错误信息 */
  error?: string;
  /** 执行耗时(毫秒) */
  duration?: number;
  /** 节点自定义名称（画布 label，执行时快照，供详情展示） */
  label?: string;
  /** 节点类型（START/LLM/...） */
  nodeType?: string;
  /** 大模型记忆：跨轮对话历史（随节点状态一起落库） */
  llmMessages?: Array<{
    content?: string;
    role: 'assistant' | 'system' | 'user';
    round?: number;
    ts?: number;
  }>;
  /** 审批结论（true=同意） */
  review?: boolean;
  /** 【工作流】节点已发起的子执行 id（恢复轮的认据：有值就不再重跑子流程） */
  childExecutionId?: string;
  /** 审批人标识 */
  reviewBy?: string;
  /** 审批意见 */
  reviewOpinion?: string;
  /** 审批编辑留痕（含改前改后值） */
  reviewDiff?: Array<{
    newValue?: any;
    nodeId: string;
    oldValue?: any;
    /** 子路径（空=整个变量被改） */
    path?: string;
    varName: string;
  }>;
}

/**
 * 工作流执行响应
 */
export interface WorkflowExecutionResp {
  /** 执行记录ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  /** 执行ID(UUID) */
  executionId: string;
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  workflowId: string;
  /** 工作流名称 */
  workflowName?: string;
  /** 执行时的工作流版本 */
  workflowVersion: number;
  /** 执行状态 */
  status: ExecutionStatus;
  /** 输入参数 */
  inputs?: Record<string, any>;
  /** 输出结果 */
  outputs?: Record<string, any>;
  /** 节点执行状态(各节点的执行详情) */
  nodeStates?: Record<string, NodeExecutionState>;
  /** 错误信息 */
  errorMessage?: string;
  /** 开始时间 */
  startTime?: string;
  /** 结束时间 */
  endTime?: string;
  /** 执行耗时(毫秒) */
  duration?: number;
  /** 输入Token数 */
  inputTokens?: number;
  /** 输出Token数 */
  outputTokens?: number;
  /** 总Token数 */
  totalTokens?: number;
  /** LLM调用次数 */
  llmCallCount?: number;
  /** 已执行的节点ID列表 */
  executedNodes?: string[];
  /** 当前节点ID（暂停时） */
  currentNodeId?: string;
  /** 本轮提交模式（空=首次执行） */
  submitMode?: 'CONTINUE' | 'RETRY';
  /** 图拓扑指纹（再提交前的漂移校验依据） */
  graphHash?: string;
  /** 当前挂起代次：与事件帧上的对不上时以详情为准 */
  pauseGeneration?: number;
  /** 此刻还欠人答的审批（仅 PAUSED 行有值；空数组=没人在等，停在等子流程） */
  pendingApprovals?: PendingApproval[];
  /** 执行用户ID (字符串类型，避免 JavaScript 大数精度丢失) */
  userId?: string;
  /** 创建时间 */
  createdTime: string;
}

/**
 * 工作流模板响应
 */
export interface WorkflowTemplateResp {
  /** 模板ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  /** 模板名称 */
  name: string;
  /** 模板描述 */
  description?: string;
  /** 模板分类 */
  category: TemplateCategory;
  /** 模板分类描述 */
  categoryDesc?: string;
  /** 模板图标 */
  icon?: string;
  /** 工作流图定义 */
  graph?: WorkflowGraph;
  /** 是否内置模板 */
  builtIn: boolean;
  /** 节点数量 */
  nodeCount: number;
  /** 创建时间 */
  createTime: string;
}

export interface WorkflowNodePortDefinitionResp {
  id: string;
  name: string;
  direction: 'input' | 'output';
  multiple?: boolean;
}

export interface WorkflowNodeConfigRuleResp {
  type: 'max' | 'min' | 'oneOf' | 'regex' | 'url';
  value?: any;
  message: string;
}

export interface WorkflowNodeConfigOptionResp {
  label: string;
  value: string;
}

export interface WorkflowNodeConfigFieldResp {
  name: string;
  label: string;
  component: string;
  valueType: 'array' | 'boolean' | 'number' | 'object' | 'string';
  required: boolean;
  defaultValue?: any;
  placeholder?: string;
  help?: string;
  options?: WorkflowNodeConfigOptionResp[];
  rules?: WorkflowNodeConfigRuleResp[];
}

export interface WorkflowNodeConfigSchemaResp {
  formComponent: string;
  requiredFields: string[];
  outputVariables: string[];
  fields?: WorkflowNodeConfigFieldResp[];
}

export interface WorkflowNodeDefinitionResp {
  type: NodeType;
  displayName: string;
  description: string;
  category: 'ai' | 'basic' | 'control' | 'data' | 'external';
  icon: string;
  color: string;
  start: boolean;
  terminal: boolean;
  /** 平台能力未实现时在节点面板置灰（不可拖拽），执行器与历史图数据不受影响 */
  disabled?: boolean;
  inputs: WorkflowNodePortDefinitionResp[];
  outputs: WorkflowNodePortDefinitionResp[];
  configSchema: WorkflowNodeConfigSchemaResp;
  defaultConfig: Record<string, any>;
}

export interface AiModelOption {
  id: number;
  provider: string;
  /** 能力类型（12 类 code：text_to_text/... 即 tb_model.category） */
  type: string;
  /** 模型名称（展示名，tb_model.name） */
  name: string;
  /** 模型标识（API 调用名，tb_model.model_name，如画布节点展示） */
  modelName?: string;
  baseUrl?: string;
}

export interface KnowledgeBaseOption {
  id: number;
  name: string;
  description?: string;
}

export interface McpServerOption {
  id: number;
  name: string;
  description?: string;
}

/**
 * MCP 工具入参定义行（由 inputSchema 摊平而来，供节点表单渲染参数绑定行）
 */
export interface McpToolParameterOption {
  name: string;
  type: string;
  description?: string;
  required?: boolean;
}

/**
 * MCP tools/list 返回的工具项
 * 后端原样透传 MCP 协议字段，参数定义在 inputSchema（JSON Schema）里，
 * 需要前端用 agent/mcp 的 schemaParams 摊平成参数行
 */
export interface McpToolOption {
  server_id?: number;
  name: string;
  description?: string;
  inputSchema?: {
    properties?: Record<string, any>;
    required?: string[];
    type?: string;
  };
}

/**
 * 动态函数工具下拉项（GET /tools/options）
 * parameters 为工具登记的参数定义，工作流工具节点据此生成绑定行
 */
export interface DynamicToolOption {
  id: number;
  name: string;
  description?: string;
  timeout?: number;
  parameters?: Array<{
    default?: any;
    description?: string;
    name: string;
    required?: boolean;
    type?: string;
  }>;
}

/**
 * 【工作流】节点「执行用 API Key」下拉项
 * 由 GET /workflows/executable-list 内联返回，而不是让前端再去拉一次
 * /workflow-api-keys/workflows/{id}：那个接口要 workflow:apikey:list 权限，
 * 编辑器使用者不一定有，拿不到就只能面对一个「选不到 key」的死下拉。
 */
export interface ExecutableWorkflowApiKeyOption {
  /** 过期时间（null=永不过期；后端已过滤掉过期与停用的 key） */
  expireTime: null | string;
  id: number;
  name: string;
  /** 每分钟调用上限 */
  rateLimit: number;
}

/**
 * 可被【工作流】节点调用的工作流：已发布且至少有一把可用 API Key
 *（口径与节点执行器的运行前校验一致，否则下拉里会选到一个必然失败的子工作流）
 */
export interface ExecutableWorkflowOption {
  /** 可用 API Key（供「执行用 API Key」下拉，不必再发一次带权限的请求） */
  apiKeys: ExecutableWorkflowApiKeyOption[];
  apiKeyCount: number;
  /** 当前发布版本号（versionMode=LATEST 时实际跑的就是它） */
  currentVersion: number;
  description?: string;
  /** 工作流ID (字符串类型，避免 JavaScript 大数精度丢失) */
  id: string;
  name: string;
}

// ==================== SSE 事件类型 ====================

export const WORKFLOW_RUNTIME_EVENT_TYPES = [
  'workflow.started',
  'workflow.resumed',
  'node.started',
  'node.delta',
  'node.completed',
  'node.failed',
  // 并行屏障被砍分支的终态：超时(node.timeout)/短路取消(node.cancelled)，
  // 收到前节点只有 node.started，页面会一直停在蓝色「执行中」
  'node.timeout',
  'node.cancelled',
  // 审批节点挂起（单节点级暂停，区别于整条流的 workflow.paused）
  'node.paused',
  // 大模型节点插入工具后的调用过程：node.tool_call 是模型要求调哪个工具/传了什么参数，
  // node.tool_result 是这一次调用的结果摘要（完整结果看节点输出的 toolCalls）
  'node.tool_call',
  'node.tool_result',
  'workflow.paused',
  'workflow.completed',
  'workflow.failed',
  'workflow.cancelled',
] as const;

/**
 * 执行事件类型
 */
export type ExecutionEventType = (typeof WORKFLOW_RUNTIME_EVENT_TYPES)[number];

/**
 * 执行事件基础接口
 */
export interface ExecutionEvent {
  /** 事件类型 */
  type: ExecutionEventType;
  /** 执行ID */
  executionId: string;
  /** 时间戳 */
  timestamp: number | string;
  /** 挂起代次：与当前行不符的帧是上一轮残留，直接丢 */
  pauseGeneration?: number;
}

/**
 * 执行开始事件
 */
export interface ExecutionStartedEvent extends ExecutionEvent {
  type: 'workflow.started';
  /** 输入参数 */
  inputs?: Record<string, any>;
}

/**
 * 节点开始事件
 */
export interface NodeStartedEvent extends ExecutionEvent {
  type: 'node.started';
  /** 节点ID */
  nodeId: string;
  /** 节点类型 */
  nodeType: NodeType;
  /** 输入数据 */
  input?: any;
}

/**
 * 节点完成事件
 */
export interface NodeCompletedEvent extends ExecutionEvent {
  type: 'node.completed';
  /** 节点ID */
  nodeId: string;
  /** 输出数据 */
  output?: any;
  /** 执行耗时(毫秒) */
  duration: number;
  /** true = 本轮未重跑，沿用上一轮结果（耗时为上一轮的值） */
  skip?: boolean;
}

/**
 * 节点错误事件
 */
export interface NodeErrorEvent extends ExecutionEvent {
  type: 'node.failed';
  /** 节点ID */
  nodeId: string;
  /** 错误信息 */
  error: string;
  /** 堆栈跟踪 */
  stackTrace?: string;
}

/**
 * 节点超时事件（并行分支等待时限到点，后端停止等待并砍掉该分支）
 */
export interface NodeTimeoutEvent extends ExecutionEvent {
  type: 'node.timeout';
  /** 节点ID */
  nodeId: string;
  /** 所属并行分支 id（画布单出口时为 node:<目标节点id>） */
  branchId?: string;
  /** 砍掉前已执行的耗时(毫秒) */
  duration?: number;
  /** 超时说明 */
  error?: string;
}

/**
 * 节点取消事件（并行「任一完成」下未命中分支被短路取消）
 */
export interface NodeCancelledEvent extends ExecutionEvent {
  type: 'node.cancelled';
  /** 节点ID */
  nodeId: string;
  /** 所属并行分支 id */
  branchId?: string;
  /** 取消前已执行的耗时(毫秒) */
  duration?: number;
  /** 取消说明 */
  error?: string;
}

/**
 * 流式Token事件
 */
export interface StreamTokenEvent extends ExecutionEvent {
  type: 'node.delta';
  /** 节点ID */
  nodeId: string;
  /** Token内容 */
  token: string;
  /** true 表示该增量属于思维链(reasoning)，false/缺省为正文 */
  reasoning?: boolean;
}

/**
 * 大模型节点的工具调用事件（模型要求调用某个已插入的工具）
 */
export interface NodeToolCallEvent extends ExecutionEvent {
  type: 'node.tool_call';
  /** 发起调用的大模型节点ID */
  nodeId: string;
  /** 回填 tool 消息时用的调用 id（同一次调用的 call/result 靠它配对） */
  toolCallId?: string;
  /** 工具名（重名时带 mcp12__ 这类来路前缀，不一定是工具登记的原始名） */
  toolName?: string;
  /** 工具来源 */
  toolKind?: LlmToolKind;
  /** 模型给的入参 */
  arguments?: Record<string, any>;
  /** 第几轮工具调用（从 1 开始；恢复轮回填的那次记 0） */
  round?: number;
  /** true = 子工作流审批结束后补记的那次调用，不是本轮真的又调了一遍 */
  resumed?: boolean;
}

/**
 * 大模型节点的工具结果事件（这一次调用的返回值）
 */
export interface NodeToolResultEvent extends ExecutionEvent {
  type: 'node.tool_result';
  nodeId: string;
  toolCallId?: string;
  toolName?: string;
  toolKind?: LlmToolKind;
  /** 结果 JSON 的文本摘要（后端按事件体积截断，完整结果在节点输出 toolCalls 里） */
  result?: string;
  /** 这一次调用的失败原因（未注册工具/参数非法/调用异常），非空时 result 只有 error */
  error?: null | string;
  round?: number;
  resumed?: boolean;
}

/**
 * 执行完成事件
 */
export interface ExecutionCompletedEvent extends ExecutionEvent {
  type: 'workflow.completed';
  /** 输出结果 */
  outputs?: Record<string, any>;
  /** 总耗时(毫秒) */
  duration: number;
}

/**
 * 执行失败事件
 */
export interface ExecutionFailedEvent extends ExecutionEvent {
  type: 'workflow.failed';
  /** 错误信息 */
  error: string;
}

/**
 * 审批节点挂起事件（节点停在 AWAITING，不路由下游）
 */
export interface NodePausedEvent extends ExecutionEvent {
  type: 'node.paused';
  /** 审批节点ID */
  nodeId: string;
  /** 节点类型 */
  nodeType?: NodeType;
  /** 挂起前已执行的耗时(毫秒) */
  duration?: number;
}

/**
 * 执行暂停事件（本轮跑完了，但有节点停在等人答）
 * 帧上不承载任何审批内容：要看欠谁就去 GET 详情取 pendingApprovals
 */
export interface ExecutionPausedEvent extends ExecutionEvent {
  type: 'workflow.paused';
  /** 停下时所在的节点 id（等结论的那个） */
  nodeId?: string;
  /** 已执行耗时(毫秒) */
  duration?: number;
  /** 当前全局变量（排障展示用） */
  variables?: Record<string, any>;
}

/**
 * 恢复提交的首帧事件（与 workflow.started 二选一，订阅方据此区分「新一轮执行」与「接着跑」）
 */
export interface ExecutionResumedEvent extends ExecutionEvent {
  type: 'workflow.resumed';
  /** 本轮输入参数 */
  inputs?: Record<string, any>;
}

// ==================== START 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.StartNodeConfig

/**
 * 输入字段类型 (START 节点)
 */
export type InputFieldType =
  /** 审批人标识数组（元素可数字可字符串；仅当画布存在审批节点时可用） */
  | 'APPROVER'
  | 'CHECKBOX' // 开关（UI 为 switch；历史名“复选框”，存储值不变以兼容旧图）
  | 'FILE_LIST' // 多文件
  | 'NUMBER' // 数字
  /** @deprecated 已与 SHORT_TEXT 合并为 TEXT，仅旧图兼容读取 */
  | 'PARAGRAPH' // 长文本（无限制）
  | 'SELECT' // 下拉选择
  /** @deprecated 已与 PARAGRAPH 合并为 TEXT，仅旧图兼容读取 */
  | 'SHORT_TEXT' // 短文本（256字符）
  | 'SINGLE_FILE' // 单文件
  | 'TEXT'; // 文本（短文本 + 长文本合并后的统一类型）

/**
 * 输入字段定义
 */
export interface InputField {
  /** 字段名（变量名） */
  name: string;
  /** 显示标签 */
  label: string;
  /** 字段类型 */
  type: InputFieldType;
  /** 是否必填 */
  required?: boolean;
  /** 默认值 */
  defaultValue?: any;
  /** 字段描述 */
  description?: string;
  /** 下拉选项（SELECT 类型使用） */
  options?: string[];
  /** 最大长度（文本类型使用） */
  maxLength?: number;
  /** 最小值（NUMBER 类型使用） */
  minValue?: number;
  /** 最大值（NUMBER 类型使用） */
  maxValue?: number;
  /** 允许的文件类型（文件类型使用） */
  allowedFileTypes?: string[];
  /** 最大文件大小（字节） */
  maxFileSize?: number;
  /** 最大文件数量（FILE_LIST 类型使用） */
  maxFileCount?: number;
  /** 正则表达式验证（文本类型使用） */
  pattern?: string;
  /** 正则表达式验证失败提示 */
  patternMessage?: string;
}

/**
 * START 节点配置 (Workflow Input Boundary)
 * 定义工作流输入字段，支持多种输入类型
 */
export interface StartNodeConfig {
  /** 输入字段列表 */
  fields?: InputField[];
}

// ==================== END 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.EndNodeConfig

/**
 * 输出类型枚举
 */
export type OutputType = 'array' | 'boolean' | 'number' | 'object' | 'string';

/**
 * 输出字段定义
 * 简化设计：每个输出变量有名称、类型、值
 * 值可以是变量引用（如 {{nodeId.varName}}）或固定值
 */
export interface OutputField {
  /** 输出变量名 */
  name: string;
  /** 变量类型 */
  type?: OutputType;
  /** 变量值（可以是变量引用或固定值） */
  value?: string;
  /** 字段描述 */
  description?: string;
}

/**
 * END 结束节点配置
 * 简化设计：只通过 outputs 列表定义输出变量
 */
export interface EndNodeConfig {
  /** 输出变量列表 */
  outputs?: OutputField[];
  /** 回答模板 */
  answerTemplate?: string;
  /** 是否流式输出 */
  streaming?: boolean;
  /** 输出模式 */
  outputMode?: 'JSON' | 'TEMPLATE' | 'TEXT';
}

// ==================== VARIABLE_ASSIGNER 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.VariableAssignerConfig

/**
 * 赋值类型枚举
 */
export type AssignmentType = 'EXPRESSION' | 'LITERAL' | 'VARIABLE';

/**
 * 变量类型枚举
 */
export type VariableType = 'array' | 'boolean' | 'number' | 'object' | 'string';

/**
 * 变量赋值定义
 */
export interface Assignment {
  /** 目标变量名（自定义名字，或上游变量引用 {{nodes.xx.output}}：运行时取其值作为变量名） */
  variableName: string;
  /** 赋值类型 */
  type: AssignmentType;
  /** 值（字面量或变量引用 {{nodeName.variableName}}） */
  value?: any;
  /** 变量类型 */
  variableType?: VariableType;
  /** 转换表达式（可选） */
  transformExpression?: string;
  /** 是否覆盖已存在的变量 */
  overwrite?: boolean;
  /** 变量描述 */
  description?: string;
}

/**
 * 变量赋值节点配置
 * 设置和转换变量
 */
export interface VariableAssignerConfig {
  /** 变量赋值列表 */
  assignments?: Assignment[];
}

// ==================== LLM 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.LLMNodeConfig

/**
 * 结构化输出配置
 */
export interface StructuredOutput {
  /** 是否启用结构化输出 */
  enabled: boolean;
  /** JSON Schema 定义 */
  jsonSchema?: string;
  /** 输出描述 */
  description?: string;
  /** 是否严格模式 */
  strictMode?: boolean;
}

/**
 * 上下文变量定义
 */
export interface ContextVariable {
  /** 变量名（在提示词中使用） */
  name: string;
  /** 变量引用 (支持格式: {{nodeName.variableName}}) */
  reference?: string;
  /** 变量描述 */
  description?: string;
}

/**
 * 大模型节点可插入的工具来源（与后端 ai_nodes.TOOL_KIND_* 逐字对齐）
 * - TOOL：工具库里的动态函数工具
 * - MCP：一个 MCP 连接（插入粒度是连接，里面有几个工具算几个）
 * - WORKFLOW：平台内另一条已发布工作流（子执行，审批挂起会带着父节点一起挂）
 */
export type LlmToolKind = 'MCP' | 'TOOL' | 'WORKFLOW';

/**
 * 大模型节点的一条工具绑定
 * 画布上不配参数：工具参数定义、MCP 连接的 tools/list、子工作流开始节点入参
 * 都在引擎执行时才读，读不到就该工具装配失败（而不是静默不发）。
 * apiKeyId/versionMode/version/workflowName 仅 kind=WORKFLOW 有意义。
 */
export interface LlmToolBinding {
  /** 执行用 API Key（子工作流必填：决定用哪套凭证与限流额度） */
  apiKeyId?: number;
  /** 来路 ID：工具 ID / MCP 连接 ID / 子工作流 ID */
  id: number | string;
  kind: LlmToolKind;
  /** 版本号（仅 versionMode=SPECIFIC 生效） */
  version?: number;
  versionMode?: WorkflowVersionMode;
  /** 子工作流名称（选择时快照，供已选清单展示） */
  workflowName?: string;
}

/**
 * LLM 大模型节点配置 (Workflow Model Node)
 * 经 common_model 支持 12 种能力类型（按所选模型登记的 category 类型化直连），
 * 支持 Vision、结构化输出，及各能力类型的媒体输入变量。
 * 注：streaming/thinking 仅在页面上展示时才会写入配置（由模型登记的能力类型 +
 * supports_stream/supports_thinking 决定）；温度/maxTokens 不再作为节点字段——
 * 全部模型调用参数统一走 params（常用参数），旧图残留字段后端忽略。
 */
export interface LLMNodeConfig {
  /** 模型 ID */
  modelId?: number;
  /** 模型标识（API 调用名，选择时快照，供画布节点展示，不参与运行逻辑） */
  modelName?: string;
  /** 能力类型（12 类 code，决定模型下拉数据源与节点分发调用） */
  modelType?: string;
  /** 系统提示词 */
  systemPrompt?: string;
  /** 用户提示词模板 (支持变量引用: {{nodeName.variableName}}) */
  promptTemplate?: string;
  /** 深度思考（仅能力类型支持且模型管理开启 supports_thinking 时写入） */
  thinking?: boolean;
  /** 是否流式输出（仅能力类型支持且模型管理开启 supports_stream 时写入；带工具时同样生效） */
  streaming?: boolean;
  /** 输出变量名 */
  outputVariable?: string;
  /** 文本类输入变量（向量/文本重排查询外的纯文本输入，支持引用） */
  inputVariable?: string;
  /** 图片输入变量（图片理解/OCR/图片向量/图生视频，引用或 URL） */
  imageVariable?: string;
  /** Vision 开关（图像理解，text_to_text 多模态对话用） */
  visionEnabled?: boolean;
  /** 图像变量列表（Vision 启用时有效） */
  imageVariables?: string[];
  /** 音频输入变量（音频转文字） */
  audioVariable?: string;
  /** 视频输入变量（视频理解） */
  videoVariable?: string;
  /** 重排查询变量（text_rerank） */
  queryVariable?: string;
  /** 重排文档变量（text_rerank，引用字符串数组） */
  documentsVariable?: string;
  /** 重排保留数量 top_n */
  topN?: number;
  /** 生成尺寸（文生图/文生视频/图生视频） */
  size?: string;
  /** 生成图片数量（文生图 n） */
  imageN?: number;
  /** 音色（文生音频） */
  voice?: string;
  /** 结构化输出配置 */
  structuredOutput?: StructuredOutput;
  /**
   * 插入的工具（仅文生文且模型登记了 supports_function_call 时写入）：
   * 流式开关照常生效（tool_calls 由后端按流式分片累加还原）
   */
  tools?: LlmToolBinding[];
  /** 是否把工具调用结果作为内容输出给客户端（关掉只记节点输出与调试事件） */
  emitToolResult?: boolean;
  /** 上下文变量列表 */
  contextVariables?: ContextVariable[];
  /** 是否启用对话记忆（仅文生文/提示词族能力类型可用） */
  memoryEnabled?: boolean;
  /** 注入条数（按消息条数计，1~100，默认 10；一轮 user+assistant = 2 条） */
  memoryLimit?: number;
  /** 记忆范围：SELF 仅本节点 / NODES 指定节点 / WORKFLOW 全图 */
  memoryScope?: 'NODES' | 'SELF' | 'WORKFLOW';
  /** NODES 范围下的节点 id 列表 */
  memoryNodes?: string[];
  /** 超出条数时的策略 */
  memoryStrategy?: 'COMPRESS' | 'DROP_MIDDLE' | 'DROP_NEWEST' | 'DROP_OLDEST';
  /** 自动压缩用的文生文模型 ID（strategy=COMPRESS 时必填） */
  memoryCompressModelId?: number;
  /**
   * 节点级常用参数（按所选模型登记的 common_params 预置，可改值/新增/删除）。
   * 运行时会合并进 model_params 透传给 common_model 实现（覆盖或补充模型默认参数）。
   */
  params?: Array<{
    /** 参数说明 */
    desc?: string;
    /** 参数名 */
    name: string;
    /** 参数类型 */
    type: 'boolean' | 'integer' | 'number' | 'object' | 'string';
    /** 参数值（前端已按类型转换） */
    value: any;
  }>;
}

// ==================== KNOWLEDGE_RETRIEVAL 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.KnowledgeRetrievalConfig

/**
 * 检索模式枚举
 */
export type RetrievalMode = 'FULLTEXT' | 'HYBRID' | 'VECTOR';

/**
 * 过滤运算符枚举
 */
export type FilterOperator =
  | 'CONTAINS'
  | 'EQUALS'
  | 'GREATER_OR_EQUAL'
  | 'GREATER_THAN'
  | 'IN'
  | 'LESS_OR_EQUAL'
  | 'LESS_THAN'
  | 'NOT_EQUALS'
  | 'NOT_IN';

/**
 * 重排序配置
 */
export interface RerankConfig {
  /** 是否启用重排序 */
  enabled: boolean;
  /** 重排序模型 ID */
  rerankModelId?: number;
  /** 重排序后保留的数量 */
  topN?: number;
}

/**
 * 元数据过滤条件
 */
export interface MetadataFilter {
  /** 元数据字段名 */
  field: string;
  /** 过滤运算符 */
  operator: FilterOperator;
  /** 过滤值 */
  value?: any;
}

/**
 * 知识检索节点配置 (Workflow Retrieval Node)
 * 从知识库检索相关内容，支持元数据过滤
 */
export interface KnowledgeRetrievalConfig {
  /** 知识库 ID 列表 */
  knowledgeBaseIds?: number[];
  /** 查询变量 (支持变量引用: {{nodeName.variableName}}) */
  queryVariable?: string;
  /** 检索数量 (Top K) */
  topK?: number;
  /** 相似度阈值 (0-1) */
  scoreThreshold?: number;
  /** 检索模式 */
  retrievalMode?: RetrievalMode;
  /** 重排序配置 */
  rerankConfig?: RerankConfig;
  /** 元数据过滤条件 */
  metadataFilters?: MetadataFilter[];
  /** 输出变量名 */
  outputVariable?: string;
  /** 是否返回元数据 */
  includeMetadata?: boolean;
  /** 是否返回相似度分数 */
  includeScore?: boolean;
}

// ==================== QUESTION_CLASSIFIER 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.QuestionClassifierConfig

/**
 * 分类类别定义
 */
export interface ClassCategory {
  /** 类别 ID（用于输出端口标识） */
  id: string;
  /** 类别名称 */
  name: string;
  /** 类别描述（帮助 LLM 理解分类标准） */
  description?: string;
  /** 示例问题（可选，帮助 LLM 更好地理解） */
  examples?: string[];
}

/**
 * 问题分类器节点配置 (Workflow Routing Node)
 * 使用 LLM 对问题进行智能分类，路由到不同的处理分支
 */
export interface QuestionClassifierConfig {
  /** 分类使用的模型 ID */
  modelId?: number;
  /** 能力类型（问题分类器固定 text_to_text） */
  modelType?: string;
  /** 输入变量（要分类的文本，支持变量引用格式: {{nodeName.variableName}}） */
  inputVariable?: string;
  /** 分类指导说明 */
  instructions?: string;
  /** 分类类别列表 */
  categories?: ClassCategory[];
  /** 是否启用高级模式 */
  advancedMode?: boolean;
  /** 自定义分类提示词模板（高级模式） */
  customPromptTemplate?: string;
}

// ==================== PARAMETER_EXTRACTOR 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.ParameterExtractorConfig

/**
 * 参数类型枚举
 */
export type ParameterType =
  | 'array'
  | 'boolean'
  | 'number'
  | 'object'
  | 'string';

/**
 * 提取参数定义
 */
export interface ExtractParameter {
  /** 前端拖拽/渲染用稳定 key（不持久化语义） */
  id?: string;
  /** 参数名 */
  name: string;
  /** 参数类型 */
  type: ParameterType;
  /** 参数描述（帮助 LLM 理解要提取什么） */
  description?: string;
  /** 是否必填 */
  required?: boolean;
  /** 枚举值（type 为 STRING 时可用于限制取值范围） */
  enumValues?: string[];
}

/**
 * 参数提取器节点配置 (Workflow Structured Extractor)
 * 从自然语言文本中提取结构化参数
 */
export interface ParameterExtractorConfig {
  /** 提取使用的模型 ID */
  modelId?: number;
  /** 能力类型（参数提取器固定 text_to_text） */
  modelType?: string;
  /** 输入变量（要提取参数的文本，支持变量引用格式: {{nodeName.variableName}}） */
  inputVariable?: string;
  /** 提取指导说明 */
  instructions?: string;
  /** 要提取的参数列表 */
  parameters?: ExtractParameter[];
}

// ==================== IF_ELSE 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.IfElseNodeConfig

/**
 * 分支类型枚举
 */
export type BranchType = 'ELIF' | 'ELSE' | 'IF';

/**
 * 逻辑运算符枚举
 */
export type LogicalOperator = 'AND' | 'OR';

/**
 * 比较运算符枚举
 */
export type CompareOperator =
  | 'CONTAINS'
  | 'ENDS_WITH'
  | 'EQUALS'
  | 'GREATER_OR_EQUAL'
  | 'GREATER_THAN'
  | 'IN'
  | 'IS_EMPTY'
  | 'IS_FALSE'
  | 'IS_NOT_EMPTY'
  | 'IS_NOT_NULL'
  | 'IS_NULL'
  | 'IS_TRUE'
  | 'LESS_OR_EQUAL'
  | 'LESS_THAN'
  | 'MATCHES_REGEX'
  | 'NOT_CONTAINS'
  | 'NOT_EQUALS'
  | 'NOT_IN'
  | 'STARTS_WITH';

/**
 * 条件定义
 */
export interface Condition {
  /** 变量引用 (支持格式: {{nodeName.variableName}}) */
  variable: string;
  /** 比较运算符 */
  operator: CompareOperator;
  /** 比较值 */
  value?: any;
  /** 值是否为变量引用 */
  valueIsVariable?: boolean;
}

/**
 * 条件分支定义
 */
export interface ConditionBranch {
  /** 分支 ID（用于输出端口标识） */
  id: string;
  /** 分支标签 */
  label: string;
  /** 分支类型 */
  type: BranchType;
  /** 条件列表（ELSE 分支不需要条件） */
  conditions?: Condition[];
  /** 条件组合方式 */
  operator?: LogicalOperator;
}

/**
 * IF/ELSE 条件分支节点配置 (Workflow Conditional Routing)
 * 支持 IF/ELIF/ELSE 多分支和 AND/OR 条件组合
 */
export interface IfElseNodeConfig {
  /** 条件分支列表（按顺序评估，第一个满足条件的分支被执行） */
  branches?: ConditionBranch[];
}

// ==================== VARIABLE_AGGREGATOR 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.VariableAggregatorConfig

/**
 * 聚合变量类型枚举
 */
export type AggregatorVariableType =
  | 'any'
  | 'array'
  | 'boolean'
  | 'number'
  | 'object'
  | 'string';

/**
 * 聚合策略枚举
 */
export type AggregationStrategy =
  | 'FIRST_NON_NULL'
  | 'LAST_NON_NULL'
  | 'MERGE_OBJECTS'
  | 'MERGE_TO_ARRAY';

/**
 * 聚合组定义
 */
export interface AggregationGroup {
  /** 前端拖拽/渲染用稳定 key（不持久化语义） */
  id?: string;
  /** 输出变量名 */
  outputVariable: string;
  /** 源变量列表（来自不同分支，支持变量引用格式: {{nodeName.variableName}}） */
  sourceVariables: string[];
  /** 变量类型约束 */
  variableType?: AggregatorVariableType;
  /** 聚合策略 */
  strategy?: AggregationStrategy;
}

/**
 * 变量聚合器节点配置
 * 合并多分支输出变量
 */
export interface VariableAggregatorConfig {
  /** 聚合组列表 */
  groups?: AggregationGroup[];
}

// ==================== CODE 节点配置 ====================
// 参照 MaxKB ToolExecutor（apps/common/utils/tool_code.py）：
// 仅 Python；方法名可自定义；支持 import 导包；返回类型由方法定义决定；
// 节点输出统一包装为 { result: <返回值> }，下游用 {{nodeId.result}} 接收。

/**
 * 代码参数类型枚举
 */
export type CodeParameterType =
  | 'array'
  | 'boolean'
  | 'number'
  | 'object'
  | 'string';

/**
 * 代码参数来源枚举
 */
export type CodeInputSource = 'CONSTANT' | 'REFERENCE';

/**
 * 代码输入参数定义
 * 支持：参数名 / 参数类型 / 是否必填 / 参数来源（引用参数或自定义值）
 */
export interface CodeInputVariable {
  /** 前端拖拽/渲染用稳定 key（不持久化语义） */
  id?: string;
  /** 参数名（在代码方法中使用） */
  name: string;
  /** 参数类型 */
  type?: CodeParameterType;
  /** 是否必填 */
  required?: boolean;
  /** 参数来源：REFERENCE 引用参数 / CONSTANT 自定义值 */
  sourceType?: CodeInputSource;
  /** 源变量引用 (sourceType=REFERENCE, 支持格式: {{nodeName.variableName}}) */
  sourceVariable?: string;
  /** 自定义值 (sourceType=CONSTANT, array/object 存 JSON 字符串) */
  value?: any;
}

/**
 * 代码节点配置 (Workflow Code Capability)
 * 执行 Python 代码：import 导包 + 自动识别入口函数（main 优先，无 main 取最后定义的顶层函数）
 * 节点输出统一为 {result: <方法返回值>}
 * 输出限制: 字符串最大 200KB, 数组最大 100 元素
 */
export interface CodeNodeConfig {
  /** Python 代码内容（import + def 方法 或 顶层 return） */
  code?: string;
  /** 输入参数列表 */
  inputs?: CodeInputVariable[];
  /** 执行超时时间（毫秒） */
  timeout?: number;
}

// ==================== TEMPLATE 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.TemplateNodeConfig

/**
 * 模板引擎枚举
 */
export type TemplateEngine = 'FREEMARKER' | 'JINJA2' | 'SIMPLE';

/**
 * 模板变量定义
 */
export interface TemplateVariable {
  /** 变量名（在模板中使用） */
  name: string;
  /** 变量引用 (支持格式: {{nodeName.variableName}}) */
  reference?: string;
  /** 默认值 */
  defaultValue?: any;
  /** 变量类型 */
  type?: string;
}

/**
 * 模板转换节点配置 (Workflow Template Capability)
 * 使用 Jinja2/Freemarker 模板转换数据
 */
export interface TemplateNodeConfig {
  /** 模板引擎类型 */
  engine?: TemplateEngine;
  /** 模板内容（支持 Jinja2/Freemarker 语法） */
  template?: string;
  /** 输入变量列表 */
  variables?: TemplateVariable[];
  /** 输出变量名 */
  outputVariable?: string;
  /** 是否转义 HTML */
  escapeHtml?: boolean;
  /** 是否去除空白 */
  trimWhitespace?: boolean;
  /** 严格模式（变量不存在时报错） */
  strictMode?: boolean;
}

// ==================== DOC_EXTRACTOR 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.DocExtractorConfig

/**
 * 文档类型枚举
 */
export type DocumentType =
  | 'CSV'
  | 'DOC'
  | 'DOCX'
  | 'HTML'
  | 'MD'
  | 'PDF'
  | 'PPT'
  | 'PPTX'
  | 'TXT'
  | 'XLS'
  | 'XLSX';

/**
 * 文档提取器节点配置 (Workflow Document Capability)
 * 从文档 URL 中提取文本 (PDF、Word、Excel、PPT 等)：后端按 URL 拉取字节后内存解析
 */
export interface DocExtractorConfig {
  /** 文件变量 (引用开始节点的文件参数: {{nodeName.fileVariable}}，取其上传后返回的 url) */
  fileVariable?: string;
  /** 支持的文档类型 */
  supportedTypes?: DocumentType[];
  /** 输出变量名 */
  outputVariable?: string;
  /** 是否提取元数据 */
  extractMetadata?: boolean;
  /** 是否保留格式 */
  preserveFormatting?: boolean;
  /** 最大文件大小（字节） */
  maxFileSize?: number;
}

// ==================== LIST_OPERATOR 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.ListOperatorConfig

/**
 * 列表操作类型枚举
 */
export type ListOperationType =
  | 'CONCAT'
  | 'COUNT'
  | 'EXTRACT'
  | 'FILTER'
  | 'FIRST'
  | 'FLATTEN'
  | 'LAST'
  | 'LIMIT'
  | 'REVERSE'
  | 'SLICE'
  | 'SORT'
  | 'UNIQUE';

/**
 * 列表比较运算符枚举
 */
export type ListCompareOperator =
  | 'CONTAINS'
  | 'ENDS_WITH'
  | 'EQUALS'
  | 'GREATER_OR_EQUAL'
  | 'GREATER_THAN'
  | 'IN'
  | 'IS_NOT_NULL'
  | 'IS_NULL'
  | 'LESS_OR_EQUAL'
  | 'LESS_THAN'
  | 'MATCHES'
  | 'NOT_CONTAINS'
  | 'NOT_EQUALS'
  | 'NOT_IN'
  | 'STARTS_WITH';

/**
 * 列表逻辑运算符枚举
 */
export type ListLogicalOperator = 'AND' | 'OR';

/**
 * 排序方向枚举
 */
export type SortDirection = 'ASC' | 'DESC';

/**
 * 保留策略枚举
 */
export type KeepStrategy = 'FIRST' | 'LAST';

/**
 * 过滤条件
 */
export interface ListFilterCondition {
  /** 字段路径 (如: name, address.city) */
  field: string;
  /** 比较运算符 */
  operator: ListCompareOperator;
  /** 比较值 */
  value?: any;
}

/**
 * 过滤配置
 */
export interface ListFilterConfig {
  /** 过滤条件列表 */
  conditions?: ListFilterCondition[];
  /** 条件组合方式 */
  operator?: ListLogicalOperator;
}

/**
 * 排序配置
 */
export interface ListSortConfig {
  /** 排序字段 */
  field?: string;
  /** 排序方向 */
  direction?: SortDirection;
  /** 是否忽略大小写（字符串排序） */
  ignoreCase?: boolean;
}

/**
 * 切片配置
 */
export interface ListSliceConfig {
  /** 起始索引（包含） */
  start?: number;
  /** 结束索引（不包含） */
  end?: number;
  /** 步长 */
  step?: number;
}

/**
 * 提取配置
 */
export interface ListExtractConfig {
  /** 要提取的字段列表 */
  fields?: string[];
  /** 是否扁平化（单字段时） */
  flatten?: boolean;
}

/**
 * 去重配置
 */
export interface ListUniqueConfig {
  /** 去重依据的字段（为空时按整个元素去重） */
  field?: string;
  /** 保留策略 */
  keepStrategy?: KeepStrategy;
}

/**
 * 限制数量配置
 */
export interface ListLimitConfig {
  /** 限制数量 */
  count?: number;
  /** 偏移量 */
  offset?: number;
}

/**
 * 合并配置
 */
export interface ListConcatConfig {
  /** 要合并的其他数组变量 */
  otherArrays?: string[];
  /** 是否去重 */
  removeDuplicates?: boolean;
}

/**
 * 列表操作节点配置 (Workflow List Capability)
 * 对数组进行过滤、排序、切片、提取等操作
 */
export interface ListOperatorConfig {
  /** 输入数组变量 (支持变量引用: {{nodeName.arrayVariable}}) */
  inputVariable?: string;
  /** 操作类型 */
  operationType?: ListOperationType;
  /** 输出变量名 */
  outputVariable?: string;
  /** 过滤配置（FILTER 操作使用） */
  filterConfig?: ListFilterConfig;
  /** 排序配置（SORT 操作使用） */
  sortConfig?: ListSortConfig;
  /** 切片配置（SLICE 操作使用） */
  sliceConfig?: ListSliceConfig;
  /** 提取配置（EXTRACT 操作使用） */
  extractConfig?: ListExtractConfig;
  /** 去重配置（UNIQUE 操作使用） */
  uniqueConfig?: ListUniqueConfig;
  /** 限制数量配置（LIMIT 操作使用） */
  limitConfig?: ListLimitConfig;
  /** 合并配置（CONCAT 操作使用） */
  concatConfig?: ListConcatConfig;
}

// ==================== HTTP_REQUEST 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.HttpRequestNodeConfig

/**
 * HTTP 方法枚举
 */
export type HttpMethod =
  | 'DELETE'
  | 'GET'
  | 'HEAD'
  | 'OPTIONS'
  | 'PATCH'
  | 'POST'
  | 'PUT';

/**
 * 请求体类型枚举
 */
export type BodyType =
  | 'BINARY'
  | 'FORM_DATA'
  | 'JSON'
  | 'NONE'
  | 'RAW'
  | 'X_WWW_FORM_URLENCODED';

/**
 * 认证类型枚举
 */
export type AuthType = 'API_KEY' | 'BASIC' | 'BEARER' | 'NONE';

/**
 * 认证配置
 */
export interface AuthConfig {
  /** 认证类型 */
  type: AuthType;
  /** API Key 值 */
  apiKey?: string;
  /** API Key 请求头名称（默认: Authorization） */
  apiKeyHeader?: string;
  /** API Key 前缀 (如: Bearer, Token) */
  apiKeyPrefix?: string;
  /** Basic Auth 用户名 */
  username?: string;
  /** Basic Auth 密码 */
  password?: string;
  /** Bearer Token */
  bearerToken?: string;
}

/**
 * 重试配置
 */
export interface RetryConfig {
  /** 是否启用重试 */
  enabled: boolean;
  /** 最大重试次数 */
  maxRetries?: number;
  /** 重试间隔（毫秒） */
  retryInterval?: number;
  /** 退避乘数 */
  backoffMultiplier?: number;
  /** 需要重试的 HTTP 状态码（默认: 429, 500, 502, 503, 504） */
  retryStatusCodes?: number[];
}

/**
 * HTTP 请求节点配置 (Workflow HTTP Capability)
 * 发送 HTTP 请求，支持多种认证方式和重试配置
 */
export interface HttpRequestNodeConfig {
  /** 请求 URL (支持变量引用: {{nodeName.variableName}}) */
  url?: string;
  /** 请求方法 */
  method?: HttpMethod;
  /** 请求头（支持变量引用） */
  headers?: Record<string, string>;
  /** 查询参数（支持变量引用） */
  queryParams?: Record<string, string>;
  /** 请求体类型 */
  bodyType?: BodyType;
  /** 请求体内容（根据 bodyType 解析） */
  body?: any;
  /** 认证配置 */
  auth?: AuthConfig;
  /** 连接超时（毫秒） */
  connectTimeout?: number;
  /** 读取超时（毫秒） */
  readTimeout?: number;
  /** 重试配置 */
  retry?: RetryConfig;
  /** 是否验证 SSL 证书 */
  sslVerify?: boolean;
  /** 输出变量名 */
  outputVariable?: string;
  /** 是否解析 JSON 响应 */
  parseJsonResponse?: boolean;
}

// ==================== 其他节点配置 ====================

/**
 * 工具节点配置 (TOOL)
 * 调用工具库登记的动态 Python 函数工具，与 CODE 节点同一沙箱口径；
 * 参数绑定行复用 CodeInputVariable（引用上游变量 / 自定义值），
 * 未绑定且工具定义里有默认值的参数由后端回落默认值。
 * 输出为 { [outputVariable]: 返回值 }，默认 {{nodeId.result}}
 */
export interface ToolNodeConfig {
  /** 工具ID（工具下拉，启用中的工具） */
  toolId?: number;
  /** 工具名称（展示与旧图兼容，toolId 缺失时后端按名称加载） */
  toolName?: string;
  /** 参数绑定行 */
  inputs?: CodeInputVariable[];
  /** 执行超时（毫秒），留空取工具登记的超时 */
  timeout?: number;
  /** 输出变量名，默认 result */
  outputVariable?: string;
}

/**
 * MCP 工具节点配置 (MCP_TOOL)
 * 调用指定 MCP 连接下的某个工具，参数按该工具 inputSchema 生成绑定行。
 * 输出：result（structuredContent 优先，否则文本 content）/ content / urls
 */
export interface McpNodeConfig {
  /** MCP 连接ID */
  mcpServerId?: number;
  /** 工具名称（来自所选连接的 tools/list） */
  toolName?: string;
  /** 参数绑定行 */
  inputs?: CodeInputVariable[];
  /** 执行超时（毫秒），留空取节点默认超时 */
  timeout?: number;
  /** 输出变量名，默认 result */
  outputVariable?: string;
}

/**
 * 工作流节点的版本策略：LATEST 跟发布走 / SPECIFIC 锁定某个已发布版本
 */
export type WorkflowVersionMode = 'LATEST' | 'SPECIFIC';

/**
 * 工作流节点配置 (WORKFLOW)
 * 嵌套调用平台内另一条已发布工作流：引擎在 workflow 服务内部直接起子执行（不走网关），
 * api-key 只作为「用哪套凭证/限流」的配置项，执行前仍会校验它存在/启用/未过期且未超限。
 * 子流程里有审批节点时子执行落 PAUSED，本节点跟着挂起，子执行终态后自动恢复。
 * 输出：{ [outputVariable]: 子工作流 END 输出, text: 可读文本 }
 */
export interface WorkflowNodeConfig {
  /** 执行用 API Key ID（决定用哪套凭证与限流额度） */
  apiKeyId?: number;
  /** 入参绑定行（子工作流的入参要手配，与 TOOL 节点同一语义） */
  inputs?: CodeInputVariable[];
  /** 输出变量名，默认 result */
  outputVariable?: string;
  /** 子执行整体等待上限（毫秒），留空取节点默认超时 */
  timeout?: number;
  /** 版本号（仅 versionMode=SPECIFIC 生效） */
  version?: number;
  versionMode?: WorkflowVersionMode;
  /** 子工作流 ID（数据源 /workflows/executable-list） */
  workflowId?: number | string;
  /** 子工作流名称（仅展示，执行时以 workflowId 为准） */
  workflowName?: string;
}

/**
 * 循环节点配置
 */
export interface LoopNodeConfig {
  /** 最大迭代次数 */
  maxIterations?: number;
  /** 退出条件表达式 */
  exitCondition?: string;
  /** 循环变量名 */
  loopVariable?: string;
}

/**
 * 并行节点配置
 */
export interface ParallelNodeConfig {
  /** 等待策略: ALL-等待全部, ANY-任一完成, FIRST-第一个完成 */
  waitStrategy?: 'ALL' | 'ANY' | 'FIRST';
  /** 分支配置列表 */
  branches?: Array<{ id: string; name: string }>;
  /** 超时时间，单位毫秒 */
  timeout?: number;
}

// ==================== 变量系统类型 ====================

/**
 * 扩展变量类型
 */
export type ExtendedVariableType =
  | 'array'
  | 'Array[File]'
  | 'Array[number]'
  | 'Array[string]'
  | 'boolean'
  | 'File'
  | 'number'
  | 'object'
  | 'string';

/**
 * 变量引用
 */
export interface VariableReference {
  /** 源节点ID */
  nodeId: string;
  /** 源节点名称 */
  nodeName?: string;
  /** 变量名 */
  variableName: string;
  /** 嵌套路径 */
  path?: string;
  /** 变量类型 */
  type?: ExtendedVariableType;
}

/**
 * 节点输出变量定义
 */
export interface NodeOutputVariable {
  /** 变量名 */
  name: string;
  /** 变量类型 */
  type: ExtendedVariableType;
  /** 描述 */
  description?: string;
}

/**
 * 节点输入变量定义
 */
export interface NodeInputVariable {
  /** 变量名 */
  name: string;
  /** 变量类型 */
  type: ExtendedVariableType;
  /** 是否必填 */
  required?: boolean;
  /** 来源节点 */
  source?: string;
}

// ==================== 节点配置类型映射 ====================

/**
 * 指定回复节点配置
 */
export interface ReplyNodeConfig {
  /** 回复方式: TEXT-自定义文本 / VARIABLE-引用参数（二选一） */
  replyType: 'TEXT' | 'VARIABLE';
  /** 引用参数（VARIABLE 模式，{{node.var}} 格式） */
  variableRef?: string;
  /** 自定义文本（TEXT 模式，支持 {{变量}} 模板） */
  text?: string;
  /** 输出变量名 */
  outputVariable?: string;
}

/**
 * 审批节点「选择输出参数」的一项：放行上游某个输出的整体或某个子字段
 *（唯一标识是 (节点, 变量, 子路径)，同名变量靠源节点区分）
 */
export interface ApprovalPassThroughInput {
  /** 来源节点 ID */
  nodeId: string;
  /** 来源节点输出里的变量名 */
  varName: string;
  /**
   * 变量之下的子路径（空=整个变量），口径同变量引用：`user.name` / `list[0]`
   */
  path?: string;
  /**
   * 透传给下游的输出键名（下游用 {{nodes.<审批>.<name>}} 引用）；
   * 缺省取子路径末段（末段是纯数字时用 `<变量名>_<下标>`）
   */
  name?: string;
}

/**
 * 人工审批节点配置
 * 在节点边界暂停等结论；只收集审批结论，同意与否都照常往下游走
 */
export interface ApprovalNodeConfig {
  /** 审批人集合（数字或字符串，命中其一即可）；空=持 key 且知道 executionId 者皆可审 */
  approvers?: Array<number | string>;
  /** 暂停范围：DOWNSTREAM 仅本节点及下游等待 / ALL 整条工作流一起停 */
  pauseScope?: ApprovalPauseScope;
  /**
   * 透传给下游的输入参数：留空 = 全部上游输出透传，选了则只透传选中的几项
   *（审批结论 review/reviewOpinion/reviewBy 不受此配置影响，始终输出）
   */
  passThroughInputs?: ApprovalPassThroughInput[];
}

/**
 * 节点类型到配置类型的映射
 */
export interface NodeConfigMap {
  START: StartNodeConfig;
  END: EndNodeConfig;
  VARIABLE_ASSIGNER: VariableAssignerConfig;
  LLM: LLMNodeConfig;
  KNOWLEDGE_RETRIEVAL: KnowledgeRetrievalConfig;
  QUESTION_CLASSIFIER: QuestionClassifierConfig;
  PARAMETER_EXTRACTOR: ParameterExtractorConfig;
  IF_ELSE: IfElseNodeConfig;
  VARIABLE_AGGREGATOR: VariableAggregatorConfig;
  LOOP: LoopNodeConfig;
  PARALLEL: ParallelNodeConfig;
  CODE: CodeNodeConfig;
  TEMPLATE: TemplateNodeConfig;
  REPLY: ReplyNodeConfig;
  DOC_EXTRACTOR: DocExtractorConfig;
  LIST_OPERATOR: ListOperatorConfig;
  HTTP_REQUEST: HttpRequestNodeConfig;
  TOOL: ToolNodeConfig;
  MCP_TOOL: McpNodeConfig;
  APPROVAL: ApprovalNodeConfig;
  WORKFLOW: WorkflowNodeConfig;
}

/**
 * 获取节点配置类型
 */
export type NodeConfig<T extends NodeType> = T extends keyof NodeConfigMap
  ? NodeConfigMap[T]
  : Record<string, any>;
