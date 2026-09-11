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
  | 'AGENT' // 智能体
  | 'CODE' // 代码
  | 'DOC_EXTRACTOR' // 文档读取
  // 智能体节点
  | 'END' // 结束/回答
  | 'HTTP_REQUEST' // HTTP请求
  | 'IF_ELSE' // 条件分支
  | 'ITERATION' // 迭代
  | 'KNOWLEDGE_RETRIEVAL' // 知识检索
  // 控制流节点
  | 'LIST_OPERATOR' // 列表处理
  | 'LLM' // 大模型
  | 'LOOP' // 循环
  | 'PARALLEL' // 并行
  | 'PARAMETER_EXTRACTOR' // 结构化提取 Agent
  // 能力节点
  | 'QUESTION_CLASSIFIER' // 分类路由 Agent
  | 'START' // 用户输入
  | 'TEMPLATE' // 模板转换
  | 'REPLY' // 指定回复
  | 'TOOL' // 工具
  // 外部系统节点
  | 'VARIABLE_AGGREGATOR' // 变量聚合
  | 'VARIABLE_ASSIGNER'; // 变量赋值

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
 * 工作流执行请求
 */
export interface WorkflowExecutionReq {
  /** 输入参数 */
  inputs?: Record<string, any>;
  /** 断点节点ID集合 */
  breakpoints?: string[];
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
  status?: string;
  /** 输入数据 */
  input?: Record<string, any>;
  /** 输出数据 */
  output?: Record<string, any>;
  /** 错误信息 */
  error?: string;
  /** 执行耗时(毫秒) */
  duration?: number;
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
  inputs: WorkflowNodePortDefinitionResp[];
  outputs: WorkflowNodePortDefinitionResp[];
  configSchema: WorkflowNodeConfigSchemaResp;
  defaultConfig: Record<string, any>;
}

export interface ModelSuffixOption {
  /** 接口后缀路径，如 /v1/chat/completions */
  url: string;
  /** 后缀说明（模型广场维护，展示给用户） */
  desc?: string;
}

export interface AiModelOption {
  id: number;
  provider: string;
  type: string;
  name: string;
  baseUrl?: string;
  /** 是否直连：1=baseUrl 含完整接口路径；0=非直连(baseUrl+接口后缀) */
  isDirect?: number;
  /** 非直连时支持的后缀列表 */
  suffixes?: ModelSuffixOption[];
}

export interface WorkflowAgentOption {
  id: number;
  name: string;
  avatar?: string;
  description?: string;
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

export interface McpToolParameterOption {
  name: string;
  type: string;
  description?: string;
  required?: boolean;
}

export interface McpToolOption {
  name: string;
  description: string;
  parameters: McpToolParameterOption[];
}

// ==================== SSE 事件类型 ====================

export const WORKFLOW_RUNTIME_EVENT_TYPES = [
  'workflow.started',
  'workflow.resumed',
  'node.started',
  'node.delta',
  'node.completed',
  'node.failed',
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
 * 流式Token事件
 */
export interface StreamTokenEvent extends ExecutionEvent {
  type: 'node.delta';
  /** 节点ID */
  nodeId: string;
  /** Token内容 */
  token: string;
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
 * 断点命中事件
 */
export interface BreakpointHitEvent extends ExecutionEvent {
  type: 'workflow.paused';
  /** 节点ID */
  nodeId: string;
  /** 当前变量 */
  variables?: Record<string, any>;
}

// ==================== START 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.StartNodeConfig

/**
 * 输入字段类型 (START 节点)
 */
export type InputFieldType =
  | 'CHECKBOX' // 复选框
  | 'FILE_LIST' // 多文件
  | 'NUMBER' // 数字
  | 'PARAGRAPH' // 长文本（无限制）
  | 'SELECT' // 下拉选择
  | 'SHORT_TEXT' // 短文本（256字符）
  | 'SINGLE_FILE'; // 单文件

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
  /** 目标变量名 */
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
 * LLM 大模型节点配置 (Workflow Model Node)
 * 调用 LLM 进行推理，支持 Vision、Memory、结构化输出
 */
export interface LLMNodeConfig {
  /** 模型 ID */
  modelId?: number;
  /** 模型类型（text_gen/multimodal，决定模型下拉数据源） */
  modelType?: string;
  /** 所选接口后缀（非直连模型时生效，base_url + suffix 拼接调用地址） */
  suffix?: string;
  /** 所选模型是否直连：0=非直连（必须配置 suffix）1=直连（画布校验用） */
  modelIsDirect?: number;
  /** 系统提示词 */
  systemPrompt?: string;
  /** 用户提示词模板 (支持变量引用: {{nodeName.variableName}}) */
  promptTemplate?: string;
  /** 温度参数 (0-2) */
  temperature?: number;
  /** 最大 Token 数 */
  maxTokens?: number;
  /** 是否流式输出 */
  streaming?: boolean;
  /** 输出变量名 */
  outputVariable?: string;
  /** Vision 开关（图像理解） */
  visionEnabled?: boolean;
  /** 图像变量列表（Vision 启用时有效） */
  imageVariables?: string[];
  /** Memory 开关（对话记忆） */
  memoryEnabled?: boolean;
  /** 记忆窗口大小 */
  memoryWindowSize?: number;
  /** 结构化输出配置 */
  structuredOutput?: StructuredOutput;
  /** 上下文变量列表 */
  contextVariables?: ContextVariable[];
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
  /** 模型类型（text_gen/multimodal，决定模型下拉数据源） */
  modelType?: string;
  /** 所选接口后缀（非直连模型时生效） */
  suffix?: string;
  /** 所选模型是否直连：0=非直连（必须配置 suffix）1=直连（画布校验用） */
  modelIsDirect?: number;
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
 * 推理模式枚举
 */
export type InferenceMode = 'FUNCTION_CALL' | 'PROMPT_BASED';

/**
 * 提取参数定义
 */
export interface ExtractParameter {
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
  /** 模型类型（text_gen/multimodal，决定模型下拉数据源） */
  modelType?: string;
  /** 所选接口后缀（非直连模型时生效） */
  suffix?: string;
  /** 所选模型是否直连：0=非直连（必须配置 suffix）1=直连（画布校验用） */
  modelIsDirect?: number;
  /** 输入变量（要提取参数的文本，支持变量引用格式: {{nodeName.variableName}}） */
  inputVariable?: string;
  /** 提取指导说明 */
  instructions?: string;
  /** 要提取的参数列表 */
  parameters?: ExtractParameter[];
  /** 推理模式 */
  inferenceMode?: InferenceMode;
  /** 是否启用记忆（对话历史） */
  memoryEnabled?: boolean;
  /** 记忆窗口大小（启用记忆时有效） */
  memoryWindowSize?: number;
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
  | 'IS_NOT_EMPTY'
  | 'IS_NOT_NULL'
  | 'IS_NULL'
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

// ==================== ITERATION 节点配置 ====================
// 同步自: com.wemirr.platform.ai.core.workflow.config.node.IterationNodeConfig

/**
 * 迭代处理模式枚举
 */
export type ProcessingMode = 'PARALLEL' | 'SEQUENTIAL';

/**
 * 迭代节点配置 (Workflow Iteration)
 * 对数组元素进行批量处理，支持顺序和并行模式
 * 内置变量: item (当前迭代元素), index (当前索引)
 */
export interface IterationNodeConfig {
  /** 要迭代的数组变量 (支持变量引用格式: {{nodeName.variableName}}) */
  arrayVariable?: string;
  /** 处理模式 */
  processingMode?: ProcessingMode;
  /** 并行数量（并行模式时有效） */
  parallelCount?: number;
  /** 单次迭代超时时间（毫秒） */
  iterationTimeout?: number;
  /** 最大迭代次数限制（默认 1000） */
  maxIterations?: number;
  /** 输出变量名（存储迭代结果数组） */
  outputVariable?: string;
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
  | 'string'
  | 'number'
  | 'boolean'
  | 'array'
  | 'object';

/**
 * 代码参数来源枚举
 */
export type CodeInputSource = 'REFERENCE' | 'CONSTANT';

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
 * 从文档中提取文本 (PDF、Word、Excel、PPT 等)
 */
export interface DocExtractorConfig {
  /** 文件变量 (支持变量引用: {{nodeName.fileVariable}}) */
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
 * 工具节点配置
 */
export interface ToolNodeConfig {
  /** MCP服务器ID */
  mcpServerId?: number;
  /** 工具名称 */
  toolName?: string;
  /** 工具参数 */
  toolParams?: Record<string, any>;
  /** 输出变量名 */
  outputVariable?: string;
}

/**
 * 智能体节点配置
 */
export interface AgentNodeConfig {
  /** 智能体ID */
  agentId?: number;
  /** 输出变量名 */
  outputVariable?: string;
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
  AGENT: AgentNodeConfig;
  IF_ELSE: IfElseNodeConfig;
  ITERATION: IterationNodeConfig;
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
}

/**
 * 获取节点配置类型
 */
export type NodeConfig<T extends NodeType> = T extends keyof NodeConfigMap
  ? NodeConfigMap[T]
  : Record<string, any>;
