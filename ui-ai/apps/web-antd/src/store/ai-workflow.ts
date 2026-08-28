/**
 * AI 工作流状态管理
 * 管理工作流编辑器的状态，使用 Vue Flow 响应式数据
 */
import type {
  ExecutionStatus,
  NodeType,
  WorkflowDetailResp,
  WorkflowDiagnosticIssue,
  WorkflowEdge,
  WorkflowGraph,
  WorkflowNode,
} from '#/api/ai-workflow/types';
import type {
  WorkflowNodeCategory,
  WorkflowNodeDefinition,
} from '#/views/wemirr/ai/workflow/domain/node-definitions';

import { computed, ref } from 'vue';

import { defineStore } from 'pinia';

import {
  createWorkflow,
  executeWorkflowAsync,
  getWorkflowDetail,
  getWorkflowNodeDefinitions,
  updateWorkflow,
} from '#/api/ai-workflow';
import { generateUUID } from '#/utils/uuid';
import { useSSE } from '#/views/wemirr/ai/workflow/components/debug/use-sse';
import { mapBackendNodeDefinitions } from '#/views/wemirr/ai/workflow/domain/node-definition-mapper';
import {
  assertWorkflowHandle,
  INPUT_HANDLE,
  OUTPUT_HANDLE,
  outputHandleForBranch,
} from '#/views/wemirr/ai/workflow/domain/ports';

type CanvasNodeExecutionStatus =
  | 'completed'
  | 'failed'
  | 'running'
  | 'waiting'
  | null;

/** Vue Flow 画布引用类型 */
export interface VueFlowCanvasRef {
  renderWorkflow: (nodes: WorkflowNode[], edges: WorkflowEdge[]) => void;
  exportGraph: () => { edges: WorkflowEdge[]; nodes: WorkflowNode[] };
  clearCanvas: () => void;
  updateNodeData: (nodeId: string, data: any) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  setAllNodesCollapsed: (collapsed: boolean) => void;
  focusNode: (nodeId: string) => void;
  getNodes: () => any[];
  getEdges: () => any[];
}

/**
 * 节点执行状态
 */
export interface NodeExecutionState {
  /** 执行状态 */
  status: 'completed' | 'failed' | 'pending' | 'running' | 'skipped';
  /** 输入数据 */
  input?: any;
  /** 输出数据 */
  output?: any;
  /** 错误信息 */
  error?: string;
  /** 执行耗时(毫秒) */
  duration?: number;
  /** 流式内容（模型节点） */
  streamingContent?: string;
}

/**
 * 执行状态
 */
export interface ExecutionState {
  /** 执行ID */
  executionId: string;
  /** 执行状态 */
  status: ExecutionStatus;
  /** 当前执行节点ID */
  currentNodeId: null | string;
  /** 节点执行状态映射 */
  nodeStates: Map<string, NodeExecutionState>;
  /** 智能体作用域变量 */
  variables: Record<string, any>;
  /** 开始时间 */
  startTime: Date | null;
  /** 结束时间 */
  endTime: Date | null;
}

/**
 * 节点面板项配置
 */
export interface NodePanelItem {
  /** 节点类型 */
  type: NodeType;
  /** 节点标签 */
  label: string;
  /** 节点图标 */
  icon: string;
  /** 节点描述 */
  description: string;
  /** 节点分类 */
  category: WorkflowNodeCategory;
  /** 默认配置 */
  defaultConfig: Record<string, any>;
}

/**
 * 工作流导出校验结果
 */
export interface WorkflowGraphValidationResult {
  /** 是否可以保存 */
  valid: boolean;
  /** 阻断保存的问题 */
  errors: string[];
  /** 可保存但需要注意的问题 */
  warnings: string[];
  /** 结构化诊断问题 */
  issues: WorkflowDiagnosticIssue[];
}

export const useAiWorkflowStore = defineStore('ai-workflow', () => {
  // ==================== 状态 ====================

  /** Vue Flow 画布引用 */
  const canvasRef = ref<null | VueFlowCanvasRef>(null);

  /** 当前工作流 */
  const currentWorkflow = ref<null | WorkflowDetailResp>(null);

  /** 选中的节点 */
  const selectedNode = ref<null | WorkflowNode>(null);

  /** 执行状态 */
  const executionState = ref<ExecutionState | null>(null);

  /** 是否有未保存的更改 */
  const isDirty = ref(false);

  /** 检查点集合 */
  const breakpoints = ref<Set<string>>(new Set());

  /** 是否处于调试模式 */
  const isDebugMode = ref(false);

  /** 加载状态 */
  const loading = ref(false);

  /** 执行中 */
  const executing = ref(false);

  /** 画布图版本，用于让命令式画布导出参与 Vue 响应式诊断 */
  const graphRevision = ref(0);

  /** 后端节点定义是运行时唯一真源 */
  const activeNodeDefinitions = ref<WorkflowNodeDefinition[]>([]);

  /** 节点定义是否已尝试加载 */
  const nodeDefinitionsLoaded = ref(false);

  /** 节点定义加载错误 */
  const nodeDefinitionsError = ref<null | string>(null);

  /** 统一 SSE 客户端 */
  const executionSse = useSSE(import.meta.env.VITE_GLOB_API_URL || '', {
    onNodeStarted: (data) => {
      if (!executionState.value) return;
      executionState.value.currentNodeId = data.nodeId;
      executionState.value.nodeStates.set(data.nodeId, {
        status: 'running',
        input: data.input,
      });
      highlightNode(data.nodeId, 'running');
    },
    onNodeCompleted: (data) => {
      if (!executionState.value) return;
      const nodeState = executionState.value.nodeStates.get(data.nodeId);
      if (nodeState) {
        nodeState.status = 'completed';
        nodeState.output = data.output;
        nodeState.duration = data.duration;
      }
      highlightNode(data.nodeId, 'completed');
    },
    onNodeError: (data) => {
      if (!executionState.value) return;
      const nodeState = executionState.value.nodeStates.get(data.nodeId);
      if (nodeState) {
        nodeState.status = 'failed';
        nodeState.error = data.error;
      }
      highlightNode(data.nodeId, 'failed');
    },
    onStreamToken: (data) => {
      if (!executionState.value) return;
      const nodeState = executionState.value.nodeStates.get(data.nodeId);
      if (nodeState) {
        nodeState.streamingContent =
          (nodeState.streamingContent || '') + data.token;
      }
    },
    onBreakpointHit: (data) => {
      if (!executionState.value) return;
      executionState.value.status = 'PAUSED';
      executionState.value.currentNodeId = data.nodeId;
      executionState.value.variables = data.variables || {};
      highlightNode(data.nodeId, 'paused');
    },
    onExecutionCompleted: (data) => {
      if (executionState.value) {
        executionState.value.status = 'COMPLETED';
        executionState.value.endTime = new Date();
        executionState.value.variables = {
          ...executionState.value.variables,
          ...data.outputs,
        };
      }
      executing.value = false;
    },
    onExecutionFailed: () => {
      if (executionState.value) {
        executionState.value.status = 'FAILED';
        executionState.value.endTime = new Date();
      }
      executing.value = false;
    },
    onNetworkError: () => {
      executing.value = false;
    },
  });

  // ==================== 计算属性 ====================

  /** 当前工作流ID */
  const workflowId = computed(() => currentWorkflow.value?.id);

  /** 当前工作流名称 */
  const workflowName = computed(() => currentWorkflow.value?.name || '');

  /** 当前工作流图 */
  const workflowGraph = computed(() => currentWorkflow.value?.graph);

  /** 是否有选中节点 */
  const hasSelectedNode = computed(() => selectedNode.value !== null);

  /** 是否正在执行 */
  const isExecuting = computed(
    () => executionState.value?.status === 'RUNNING',
  );

  /** 是否已暂停 */
  const isPaused = computed(() => executionState.value?.status === 'PAUSED');

  /** 当前生效的节点面板项 */
  const activeNodePanelItems = computed<NodePanelItem[]>(() =>
    activeNodeDefinitions.value.map((definition) => ({
      type: definition.type,
      label: definition.displayName,
      icon: definition.icon,
      description: definition.description,
      category: definition.category,
      defaultConfig: { ...definition.defaultConfig },
    })),
  );

  function getActiveNodeDefinition(
    type: NodeType,
  ): undefined | WorkflowNodeDefinition {
    return activeNodeDefinitions.value.find(
      (definition) => definition.type === type,
    );
  }

  // ==================== 方法 ====================

  async function loadNodeDefinitions() {
    nodeDefinitionsError.value = null;
    try {
      const definitions = await getWorkflowNodeDefinitions();
      activeNodeDefinitions.value = mapBackendNodeDefinitions(definitions);
    } catch (error) {
      activeNodeDefinitions.value = [];
      const message =
        error instanceof Error ? error.message : '加载后端节点定义失败';
      nodeDefinitionsError.value = message;
      throw error;
    } finally {
      nodeDefinitionsLoaded.value = true;
    }
  }

  /**
   * 设置 Vue Flow 画布引用
   */
  function setCanvasRef(canvas: VueFlowCanvasRef) {
    canvasRef.value = canvas;
  }

  /**
   * 选中节点
   */
  function selectNode(nodeId: string) {
    const nodes = canvasRef.value?.getNodes() || [];
    const node = nodes.find((n: any) => n.id === nodeId);
    if (node) {
      selectedNode.value = {
        id: node.id,
        type: node.data?.nodeType || 'START',
        label: node.data?.label || '',
        position: { x: node.position.x, y: node.position.y },
        data: node.data?.config || {},
      };
    }
  }

  /**
   * 清除选中
   */
  function clearSelection() {
    selectedNode.value = null;
  }

  /**
   * 设置脏状态
   */
  function setDirty(dirty: boolean) {
    isDirty.value = dirty;
    if (dirty) {
      bumpGraphRevision();
    }
  }

  /**
   * 加载工作流
   */
  async function loadWorkflow(id: number | string) {
    loading.value = true;
    try {
      currentWorkflow.value = await getWorkflowDetail(id);
      renderWorkflow();
      isDirty.value = false;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 渲染工作流到画布
   */
  function renderWorkflow() {
    if (!canvasRef.value || !currentWorkflow.value?.graph) return;

    const { nodes, edges } = currentWorkflow.value.graph;
    canvasRef.value.renderWorkflow(nodes || [], edges || []);
    bumpGraphRevision();
  }

  /**
   * 导出工作流图
   */
  function exportGraph(): WorkflowGraph {
    if (!canvasRef.value) {
      return currentWorkflow.value?.graph || { nodes: [], edges: [] };
    }
    return canvasRef.value.exportGraph();
  }

  /**
   * 校验工作流图
   */
  function validateWorkflowGraph(
    graph: WorkflowGraph,
  ): WorkflowGraphValidationResult {
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const errors: string[] = [];
    const warnings: string[] = [];
    const issues: WorkflowDiagnosticIssue[] = [];

    const addIssue = (
      severity: WorkflowDiagnosticIssue['severity'],
      code: string,
      message: string,
      node?: WorkflowNode,
      suggestion?: string,
    ) => {
      issues.push({
        code,
        severity,
        nodeId: node?.id,
        nodeLabel: node?.label,
        nodeType: node?.type,
        message,
        suggestion,
      });
      if (severity === 'ERROR') {
        errors.push(message);
      } else if (severity === 'WARNING') {
        warnings.push(message);
      }
    };

    const startNodes = nodes.filter((node) => node.type === 'START');
    const endNodes = nodes.filter((node) => node.type === 'END');
    if (startNodes.length !== 1) {
      addIssue(
        'ERROR',
        'START_NODE_COUNT_INVALID',
        '智能体工作流必须且只能有一个开始节点',
      );
    }
    if (endNodes.length === 0) {
      addIssue(
        'ERROR',
        'END_NODE_REQUIRED',
        '智能体工作流至少需要一个结束节点',
      );
    }

    const nodeIds = new Set(nodes.map((node) => node.id));
    const outgoingByNode = new Map<string, WorkflowEdge[]>();
    const incomingByNode = new Map<string, WorkflowEdge[]>();
    for (const edge of edges) {
      if (!nodeIds.has(edge.source)) {
        addIssue(
          'ERROR',
          'EDGE_SOURCE_UNKNOWN',
          `连线 ${edge.id || `${edge.source}->${edge.target}`} 的源节点不存在`,
        );
      }
      if (!nodeIds.has(edge.target)) {
        addIssue(
          'ERROR',
          'EDGE_TARGET_UNKNOWN',
          `连线 ${edge.id || `${edge.source}->${edge.target}`} 的目标节点不存在`,
        );
      }
      outgoingByNode.set(edge.source, [
        ...(outgoingByNode.get(edge.source) || []),
        edge,
      ]);
      incomingByNode.set(edge.target, [
        ...(incomingByNode.get(edge.target) || []),
        edge,
      ]);
    }

    if (
      startNodes[0] &&
      (incomingByNode.get(startNodes[0].id)?.length || 0) > 0
    ) {
      addIssue(
        'ERROR',
        'START_HAS_INCOMING_EDGE',
        '开始节点不能有入边',
        startNodes[0],
      );
    }
    for (const node of endNodes) {
      if ((outgoingByNode.get(node.id)?.length || 0) > 0) {
        addIssue(
          'ERROR',
          'END_HAS_OUTGOING_EDGE',
          `结束节点 ${node.label || node.id} 不能有出边`,
          node,
        );
      }
    }

    if (startNodes[0]) {
      const reachable = new Set<string>();
      const queue = [startNodes[0].id];
      while (queue.length > 0) {
        const nodeId = queue.shift()!;
        if (reachable.has(nodeId)) continue;
        reachable.add(nodeId);
        for (const edge of outgoingByNode.get(nodeId) || []) {
          queue.push(edge.target);
        }
      }
      for (const node of nodes) {
        if (!reachable.has(node.id)) {
          addIssue(
            'ERROR',
            'NODE_UNREACHABLE',
            `节点 ${node.label || node.id} 不可从开始节点到达`,
            node,
            '删除孤岛节点或补齐从开始节点到该节点的路径',
          );
        }
      }
    }

    const detectCycle = (
      nodeId: string,
      visiting: Set<string>,
      visited: Set<string>,
    ): boolean => {
      if (visited.has(nodeId)) return false;
      if (visiting.has(nodeId)) return true;
      visiting.add(nodeId);
      const sourceNode = nodes.find((node) => node.id === nodeId);
      const outgoing =
        sourceNode?.type === 'LOOP'
          ? []
          : (outgoingByNode.get(nodeId) || []).map((edge) => edge.target);
      for (const target of outgoing) {
        if (detectCycle(target, visiting, visited)) return true;
      }
      visiting.delete(nodeId);
      visited.add(nodeId);
      return false;
    };
    const visited = new Set<string>();
    for (const node of nodes) {
      if (detectCycle(node.id, new Set(), visited)) {
        addIssue(
          'ERROR',
          'GRAPH_CYCLE_DETECTED',
          `节点 ${node.label || node.id} 所在路径存在环，请使用循环路由表达循环`,
          node,
        );
        break;
      }
    }

    const collectInputNames = () => {
      const names = new Set<string>();
      for (const node of startNodes) {
        const fields = node.data?.fields || node.data?.config?.fields || [];
        if (Array.isArray(fields)) {
          for (const field of fields) {
            if (field?.name) names.add(String(field.name));
          }
        }
      }
      return names;
    };
    const inputNames = collectInputNames();
    const variablePattern = /\{\{\s*([^}]+?)\s*\}\}/g;
    const collectRefs = (value: any, refs: string[]) => {
      if (typeof value === 'string') {
        for (const match of value.matchAll(variablePattern)) {
          refs.push(match[1]?.trim() || '');
        }
      } else if (Array.isArray(value)) {
        value.forEach((item) => collectRefs(item, refs));
      } else if (value && typeof value === 'object') {
        Object.values(value).forEach((item) => collectRefs(item, refs));
      }
    };

    for (const node of nodes) {
      const outgoingCount = outgoingByNode.get(node.id)?.length || 0;
      if (node.type !== 'END' && outgoingCount === 0) {
        addIssue(
          'ERROR',
          'NODE_WITHOUT_NEXT_PATH',
          `节点 ${node.label || node.id} 没有后续路径`,
          node,
        );
      }
      if (node.type === 'LOOP') {
        const maxIterations = Number(node.data?.maxIterations ?? 0);
        if (!Number.isFinite(maxIterations) || maxIterations <= 0) {
          addIssue(
            'ERROR',
            'LOOP_MAX_ITERATIONS_REQUIRED',
            `循环节点 ${node.label || node.id} 必须设置大于 0 的最大迭代次数`,
            node,
          );
        } else if (maxIterations > 50) {
          addIssue(
            'WARNING',
            'LOOP_MAX_ITERATIONS_HIGH',
            `循环节点 ${node.label || node.id} 最大迭代次数为 ${maxIterations}，可能造成高成本运行`,
            node,
            '降低上限，或增加提前退出条件',
          );
        }
      }
      if (node.type === 'PARALLEL') {
        if (outgoingCount < 2) {
          addIssue(
            'ERROR',
            'PARALLEL_BRANCH_TOO_FEW',
            `并行路由 ${node.label || node.id} 至少需要两个分支`,
            node,
          );
        } else if (outgoingCount > 6) {
          addIssue(
            'WARNING',
            'PARALLEL_BRANCH_TOO_MANY',
            `并行路由 ${node.label || node.id} 包含 ${outgoingCount} 个分支，可能造成并发成本峰值`,
            node,
          );
        }
      }
      if (node.type === 'IF_ELSE' || node.type === 'QUESTION_CLASSIFIER') {
        const declaredBranches = new Set<string>();
        const branchSource =
          node.type === 'QUESTION_CLASSIFIER'
            ? node.data?.categories
            : node.data?.branches;
        if (Array.isArray(branchSource)) {
          for (const branch of branchSource) {
            if (branch?.id) {
              declaredBranches.add(outputHandleForBranch(String(branch.id)));
            }
          }
        }
        for (const edge of outgoingByNode.get(node.id) || []) {
          const sourceHandle = assertWorkflowHandle(edge.sourceHandle);
          if (sourceHandle && !declaredBranches.has(sourceHandle)) {
            addIssue(
              'ERROR',
              'BRANCH_HANDLE_UNKNOWN',
              `路由节点 ${node.label || node.id} 的分支端口 ${sourceHandle} 未声明`,
              node,
            );
          }
        }
      }
      if (node.type === 'LLM' && !node.data?.modelId) {
        addIssue(
          'ERROR',
          'AGENT_MODEL_REQUIRED',
          `模型智能体 ${node.label || node.id} 必须选择模型`,
          node,
        );
      }
      if (node.type === 'LLM' && !node.data?.promptTemplate) {
        addIssue(
          'WARNING',
          'AGENT_PROMPT_EMPTY',
          `模型智能体 ${node.label || node.id} 未配置提示词`,
          node,
        );
      }
      if (node.type === 'AGENT' && !node.data?.agentId) {
        addIssue(
          'ERROR',
          'AGENT_ID_REQUIRED',
          `子智能体 ${node.label || node.id} 必须选择目标智能体`,
          node,
        );
      }
      if (node.type === 'KNOWLEDGE_RETRIEVAL') {
        const knowledgeBaseIds = node.data?.knowledgeBaseIds;
        if (!Array.isArray(knowledgeBaseIds) || knowledgeBaseIds.length === 0) {
          addIssue(
            'ERROR',
            'KNOWLEDGE_BASE_REQUIRED',
            `检索智能体 ${node.label || node.id} 必须选择知识库`,
            node,
          );
        }
        if (!node.data?.queryVariable) {
          addIssue(
            'ERROR',
            'KNOWLEDGE_QUERY_REQUIRED',
            `检索智能体 ${node.label || node.id} 必须配置查询变量`,
            node,
          );
        }
      }
      if (node.type === 'QUESTION_CLASSIFIER') {
        const categories = node.data?.categories;
        if (!Array.isArray(categories) || categories.length < 2) {
          addIssue(
            'ERROR',
            'CLASSIFIER_CATEGORIES_REQUIRED',
            `分类路由智能体 ${node.label || node.id} 必须配置分类`,
            node,
          );
        }
      }
      if (node.type === 'PARAMETER_EXTRACTOR') {
        const parameters = node.data?.parameters;
        if (!Array.isArray(parameters) || parameters.length === 0) {
          addIssue(
            'ERROR',
            'EXTRACTOR_PARAMETERS_REQUIRED',
            `结构化提取智能体 ${node.label || node.id} 必须配置参数`,
            node,
          );
        }
      }
      if (node.type === 'TOOL') {
        if (!node.data?.mcpServerId) {
          addIssue(
            'ERROR',
            'TOOL_SERVER_REQUIRED',
            `工具节点 ${node.label || node.id} 必须选择 MCP 服务器`,
            node,
          );
        }
        if (!node.data?.toolName) {
          addIssue(
            'ERROR',
            'TOOL_NAME_REQUIRED',
            `工具节点 ${node.label || node.id} 必须选择具体工具`,
            node,
          );
        }
        if (!node.data?.outputVariable) {
          addIssue(
            'WARNING',
            'TOOL_OUTPUT_VARIABLE_EMPTY',
            `工具节点 ${node.label || node.id} 未配置输出变量`,
            node,
          );
        }
      }
      if (node.type === 'HTTP_REQUEST') {
        const url = String(node.data?.url || '');
        if (!url) {
          addIssue(
            'ERROR',
            'HTTP_URL_REQUIRED',
            `接口调用 ${node.label || node.id} 必须配置 URL`,
            node,
          );
        } else if (!url.startsWith('http://') && !url.startsWith('https://')) {
          addIssue(
            'ERROR',
            'HTTP_URL_INVALID',
            `接口调用 ${node.label || node.id} 的 URL 仅允许 http/https`,
            node,
          );
        }
      }
      const refs: string[] = [];
      collectRefs(node.data, refs);
      for (const ref of refs) {
        if (ref.startsWith('inputs.')) {
          const inputName = ref.slice('inputs.'.length).split('.')[0];
          if (!inputName || !inputNames.has(inputName)) {
            addIssue(
              'ERROR',
              'VARIABLE_INPUT_UNKNOWN',
              `节点 ${node.label || node.id} 引用了未声明输入变量 ${ref}`,
              node,
            );
          }
        } else if (ref.startsWith('nodes.')) {
          const nodeId = ref.split('.')[1];
          if (!nodeId || !nodeIds.has(nodeId)) {
            addIssue(
              'ERROR',
              'VARIABLE_NODE_UNKNOWN',
              `节点 ${node.label || node.id} 引用了不存在的节点变量 ${ref}`,
              node,
            );
          }
        } else if (!ref.startsWith('sys.') && !ref.startsWith('env.')) {
          addIssue(
            'WARNING',
            'VARIABLE_SCOPE_AMBIGUOUS',
            `节点 ${node.label || node.id} 的变量引用 ${ref} 缺少作用域前缀`,
            node,
          );
        }
      }
    }

    return { valid: errors.length === 0, errors, warnings, issues };
  }

  /**
   * 保存工作流
   */
  async function saveWorkflow() {
    if (!currentWorkflow.value) return;

    loading.value = true;
    try {
      const graphData = exportGraph();
      const validation = validateWorkflowGraph(graphData);
      if (!validation.valid) {
        throw new Error(validation.errors.join('；'));
      }

      if (currentWorkflow.value.id) {
        // 更新
        await updateWorkflow(currentWorkflow.value.id, {
          name: currentWorkflow.value.name,
          description: currentWorkflow.value.description,
          graph: graphData,
          inputVariables: currentWorkflow.value.inputVariables,
          outputVariables: currentWorkflow.value.outputVariables,
        });
      } else {
        // 创建
        const id = await createWorkflow({
          name: currentWorkflow.value.name,
          description: currentWorkflow.value.description,
          graph: graphData,
          inputVariables: currentWorkflow.value.inputVariables,
          outputVariables: currentWorkflow.value.outputVariables,
        });
        currentWorkflow.value.id = String(id);
      }

      isDirty.value = false;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 创建新工作流
   */
  function createNewWorkflow(name: string, description?: string) {
    const startNodeId = generateUUID();
    const endNodeId = generateUUID();
    const startConfig = {
      ...(getActiveNodeDefinition('START')?.defaultConfig || { fields: [] }),
    };
    const endConfig = {
      ...(getActiveNodeDefinition('END')?.defaultConfig || {
        outputs: [],
        outputMode: 'TEMPLATE',
      }),
    };
    currentWorkflow.value = {
      id: '',
      name,
      description,
      graph: {
        nodes: [
          {
            id: startNodeId,
            type: 'START',
            label: '开始',
            position: { x: 120, y: 220 },
            data: startConfig,
          },
          {
            id: endNodeId,
            type: 'END',
            label: '结束',
            position: { x: 520, y: 220 },
            data: endConfig,
          },
        ],
        edges: [
          {
            id: generateUUID(),
            source: startNodeId,
            sourceHandle: OUTPUT_HANDLE,
            target: endNodeId,
            targetHandle: INPUT_HANDLE,
          },
        ],
      },
      inputVariables: [],
      outputVariables: [],
      currentVersion: 0,
      status: 'DRAFT',
      createTime: new Date().toISOString(),
      updateTime: new Date().toISOString(),
    };
    isDirty.value = true;

    if (canvasRef.value) {
      renderWorkflow();
    }
  }

  /**
   * 更新节点配置
   */
  function updateNodeConfig(nodeId: string, config: Record<string, any>) {
    if (canvasRef.value) {
      canvasRef.value.updateNodeData(nodeId, { config });
      isDirty.value = true;
      bumpGraphRevision();

      // 更新选中节点
      if (selectedNode.value?.id === nodeId) {
        selectedNode.value = { ...selectedNode.value, data: config };
      }
    }
  }

  /**
   * 更新节点名称
   */
  function updateNodeLabel(nodeId: string, label: string) {
    if (canvasRef.value) {
      canvasRef.value.updateNodeData(nodeId, { label });
      isDirty.value = true;
      bumpGraphRevision();

      if (selectedNode.value?.id === nodeId) {
        selectedNode.value = { ...selectedNode.value, label };
      }
    }
  }

  /**
   * 切换检查点
   */
  function toggleBreakpoint(nodeId: string) {
    if (breakpoints.value.has(nodeId)) {
      breakpoints.value.delete(nodeId);
    } else {
      breakpoints.value.add(nodeId);
    }
    // 触发响应式更新
    breakpoints.value = new Set(breakpoints.value);
  }

  /**
   * 清除所有检查点
   */
  function clearBreakpoints() {
    breakpoints.value.clear();
    breakpoints.value = new Set();
  }

  /**
   * 执行工作流
   */
  async function executeWorkflow(inputs: Record<string, any> = {}) {
    if (!currentWorkflow.value?.id) return;

    executing.value = true;
    try {
      const executionId = await executeWorkflowAsync(currentWorkflow.value.id, {
        inputs,
        breakpoints: isDebugMode.value ? [...breakpoints.value] : undefined,
      });

      // 初始化执行状态
      executionState.value = {
        executionId,
        status: 'RUNNING',
        currentNodeId: null,
        nodeStates: new Map(),
        variables: inputs,
        startTime: new Date(),
        endTime: null,
      };

      // 订阅执行事件
      subscribeExecution(executionId);

      return executionId;
    } catch (error) {
      executing.value = false;
      if (error instanceof Error) {
        throw error;
      }
      throw new Error(String(error || '执行工作流失败'));
    }
  }

  /**
   * 订阅执行事件
   */
  function subscribeExecution(executionId: string) {
    executionSse.connect(executionId);
  }

  /**
   * 关闭事件源
   */
  function closeEventSource() {
    executionSse.disconnect();
  }

  /**
   * 高亮节点（更新执行状态）
   */
  function highlightNode(
    nodeId: string,
    status: 'completed' | 'failed' | 'paused' | 'running',
  ) {
    if (!canvasRef.value) return;

    const statusMap: Record<string, CanvasNodeExecutionStatus> = {
      running: 'running',
      completed: 'completed',
      failed: 'failed',
      paused: 'waiting',
    };
    canvasRef.value.updateNodeData(nodeId, {
      executionStatus: statusMap[status] || null,
    });
  }

  /**
   * 重置节点高亮
   */
  function resetNodeHighlights() {
    if (!canvasRef.value) return;
    const nodes = canvasRef.value.getNodes();
    nodes.forEach((node: any) => {
      canvasRef.value?.updateNodeData(node.id, {
        executionStatus: null,
        executionDuration: null,
      });
    });
  }

  /**
   * 清除执行状态
   */
  function clearExecutionState() {
    executionState.value = null;
    executing.value = false;
    closeEventSource();
    resetNodeHighlights();
  }

  /**
   * 重置当前编辑器会话。
   * 路由复用同一个编辑器实例时，画布和后端节点定义仍然是有效资源，
   * 这里仅清空当前工作流、选中态、调试态和脏状态。
   */
  function resetEditorSession() {
    canvasRef.value?.clearCanvas();
    bumpGraphRevision();
    currentWorkflow.value = null;
    selectedNode.value = null;
    executionState.value = null;
    isDirty.value = false;
    breakpoints.value = new Set();
    isDebugMode.value = false;
    loading.value = false;
    executing.value = false;
    closeEventSource();
  }

  /**
   * 重置状态
   */
  function $reset() {
    resetEditorSession();
    canvasRef.value = null;
    activeNodeDefinitions.value = [];
    nodeDefinitionsLoaded.value = false;
    nodeDefinitionsError.value = null;
  }

  function bumpGraphRevision() {
    graphRevision.value += 1;
  }

  return {
    // 状态
    canvasRef,
    currentWorkflow,
    selectedNode,
    executionState,
    isDirty,
    breakpoints,
    isDebugMode,
    loading,
    executing,
    graphRevision,
    activeNodeDefinitions,
    nodeDefinitionsLoaded,
    nodeDefinitionsError,

    // 计算属性
    workflowId,
    workflowName,
    workflowGraph,
    hasSelectedNode,
    isExecuting,
    isPaused,
    activeNodePanelItems,
    getActiveNodeDefinition,

    // 方法
    loadNodeDefinitions,
    setCanvasRef,
    selectNode,
    clearSelection,
    setDirty,
    loadWorkflow,
    renderWorkflow,
    exportGraph,
    validateWorkflowGraph,
    saveWorkflow,
    createNewWorkflow,
    resetEditorSession,
    updateNodeConfig,
    updateNodeLabel,
    toggleBreakpoint,
    clearBreakpoints,
    executeWorkflow,
    subscribeExecution,
    closeEventSource,
    highlightNode,
    resetNodeHighlights,
    clearExecutionState,
    $reset,
  };
});
