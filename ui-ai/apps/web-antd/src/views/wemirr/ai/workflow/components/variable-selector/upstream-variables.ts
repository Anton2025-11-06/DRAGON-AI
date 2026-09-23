/**
 * 画布上游变量推导（变量选择器与审批节点表单共用的一份口径）。
 *
 * 这里放的是「纯拓扑 + 节点配置」的推导，不碰运行时：同一个函数既给
 * VariableSelector 决定「下游能引用哪些变量」，也给 ApprovalNodeForm 决定
 * 「选择输出参数能选哪些」。后端的镜像实现是
 * `workflow_engine/nodes/approval_nodes.py`（`upstream_layers` /
 * `_resolve_pass_through` / `default_pass_through_name`），两边合并顺序与键名规则
 * 必须一致，否则会出现「下拉里选得到、运行期取不到」的契约漂移。
 */
import type {
  ApprovalPassThroughInput,
  ExtendedVariableType,
  NodeType,
} from '#/api/ai-workflow/types';

export type { ApprovalPassThroughInput };

/** 节点输出变量 */
export interface NodeVariable {
  /** 变量名 */
  name: string;
  /** 变量类型 */
  type: ExtendedVariableType;
  /** 描述 */
  description?: string;
}

/** 节点及其变量 */
export interface NodeWithVariables {
  /** 节点ID */
  id: string;
  /** 节点标签 */
  label: string;
  /** 节点类型 */
  type: NodeType;
  /** 输出变量列表 */
  variables: NodeVariable[];
}

/** vue-flow 画布的最小读视图（store 里的 canvasRef，测试可传等价对象） */
export interface CanvasLike {
  getEdges?: () => Array<{ source?: string; target?: string }>;
  getNodes?: () => Array<{ data?: any; id: string }>;
}

/** 归一后的画布节点 */
export interface GraphNode {
  config: Record<string, any>;
  id: string;
  label: string;
  type: NodeType;
}

/** 归一后的画布图 */
export interface FlowGraph {
  edges: Array<{ source: string; target: string }>;
  nodes: GraphNode[];
}

/** 画布对象归一为纯数据结构（画布未就绪时给空图，调用方不必判空） */
export function readGraph(canvas: CanvasLike | null | undefined): FlowGraph {
  const rawNodes = canvas?.getNodes?.() || [];
  const rawEdges = canvas?.getEdges?.() || [];
  return {
    nodes: rawNodes.map((node) => ({
      config: node.data?.config || {},
      id: node.id,
      label: node.data?.label || node.id,
      type: node.data?.nodeType as NodeType,
    })),
    edges: rawEdges
      .filter((edge) => edge.source && edge.target)
      .map((edge) => ({
        source: String(edge.source),
        target: String(edge.target),
      })),
  };
}

/**
 * 上游节点 ID 集合（变量下拉的可见范围）。
 *
 * 审批节点是「引用屏障」：反向遍历到它就把它计入可引用，但不再回溯它的入边上游。
 * 理由是审批人可能已经改过上游数据，下游能看到的那份是审批节点透传出来的快照；
 * 直接引用审批上游等于把「未经审批放行」的原始值抄进下游，绕过审批的编辑与结论。
 * 要拿审批上游的数据就从审批节点引用（它把上游的键按配置透传了），
 * 不经过任何审批的上游分支不受影响，照常直接引用。
 */
export function upstreamNodeIds(graph: FlowGraph, nodeId: string): Set<string> {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const visited = new Set<string>();
  const queue: string[] = [];

  /** 登记一个上游节点：非屏障节点才继续往回溯 */
  const visit = (id: string) => {
    if (visited.has(id)) return;
    visited.add(id);
    if (nodeById.get(id)?.type !== 'APPROVAL') queue.push(id);
  };

  graph.edges.forEach((edge) => {
    if (edge.target === nodeId && edge.source) visit(edge.source);
  });
  while (queue.length > 0) {
    const currentId = queue.shift() as string;
    graph.edges.forEach((edge) => {
      if (edge.target === currentId && edge.source) visit(edge.source);
    });
  }
  return visited;
}

/**
 * 反向回溯上游，按「距本节点的跳数」分层：第 0 层 = 直接入边来源（保持入边顺序）。
 *
 * 与后端 `upstream_layers` 同口径：审批节点计入上游但不继续回溯（隔着它的原始数据
 * 只能从它放行的那份里拿）。
 */
export function upstreamLayers(
  graph: FlowGraph,
  nodeId: string,
): GraphNode[][] {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const inSources = (id: string) =>
    graph.edges.filter((edge) => edge.target === id).map((edge) => edge.source);

  const layers: GraphNode[][] = [];
  const visited = new Set<string>([nodeId]);
  let current = inSources(nodeId);
  while (current.length > 0) {
    const nodes: GraphNode[] = [];
    current.forEach((id) => {
      if (visited.has(id)) return;
      visited.add(id);
      const node = nodeById.get(id);
      if (node) nodes.push(node);
    });
    if (nodes.length > 0) layers.push(nodes);
    const next: string[] = [];
    nodes.forEach((node) => {
      if (node.type === 'APPROVAL') return;
      next.push(...inSources(node.id));
    });
    current = next;
  }
  return layers;
}

/** 键分隔符：nodeId 不含 `::`，变量名可含点，所以按前两个 `::` 拆 */
const KEY_SEP = '::';

/** 透传目标编码为树节点键：`nodeId::varName::path`（path 为空表示整个变量） */
export function encodePassThroughKey(
  nodeId: string,
  varName: string,
  path = '',
): string {
  return `${nodeId}${KEY_SEP}${varName}${KEY_SEP}${path}`;
}

/** 树节点键 → 配置项（路径为空时不写 path 键，保持图里的 JSON 精简） */
export function decodePassThroughKey(key: string): ApprovalPassThroughInput {
  const first = key.indexOf(KEY_SEP);
  const second = key.indexOf(KEY_SEP, first + KEY_SEP.length);
  if (first === -1 || second === -1) return { nodeId: '', varName: '' };
  const item: ApprovalPassThroughInput = {
    nodeId: key.slice(0, first),
    varName: key.slice(first + KEY_SEP.length, second),
  };
  const path = key.slice(second + KEY_SEP.length);
  if (path) item.path = path;
  return item;
}

/**
 * 子路径切成 token：与后端 `_dig`/`split_path` 同口径（点号与方括号都是分隔符）。
 * 读写两边用同一份切法，否则「表单里选中的那个 key」与「运行期取的那个 key」会错位。
 */
export function splitPath(path: string): string[] {
  return (path || '')
    .trim()
    .split(/[.[\]]+/)
    .filter(Boolean);
}

/**
 * 透传输出键名的缺省值：子路径末段；末段是数组下标时带上变量名前缀，
 * 免得下游要写 `{{nodes.app.0}}` 这种看不出来源的引用。
 */
export function defaultPassThroughName(varName: string, path: string): string {
  const tokens = splitPath(path);
  if (tokens.length === 0) return varName;
  const last = tokens.at(-1) as string;
  return /^\d+$/.test(last) ? `${varName}_${last}` : last;
}

/** 一项配置落地后的输出键名（别名优先，其次缺省规则） */
export function passThroughOutputName(item: ApprovalPassThroughInput): string {
  return (
    (item.name || '').trim() ||
    defaultPassThroughName(item.varName, item.path || '')
  );
}

/** `passThroughInputs` 归一：丢掉缺项，非数组（脏数据）按未配置处理 */
export function normalizePassThroughInputs(
  raw: any,
): ApprovalPassThroughInput[] {
  if (!Array.isArray(raw)) return [];
  const items: ApprovalPassThroughInput[] = [];
  raw.forEach((entry) => {
    const nodeId = String(entry?.nodeId || '');
    const varName = String(entry?.varName || '');
    if (!nodeId || !varName) return;
    const item: ApprovalPassThroughInput = { nodeId, varName };
    const path = String(entry?.path || '').trim();
    const name = String(entry?.name || '').trim();
    if (path) item.path = path;
    if (name) item.name = name;
    items.push(item);
  });
  return items;
}

/** 变量之下可由配置推出的子字段（对象的 key，可继续下钻） */
export interface PassThroughSubField {
  children: PassThroughSubField[];
  /** 相对变量名的子路径 */
  path: string;
  /** 展示名（路径末段） */
  title: string;
  type: ExtendedVariableType;
}

/** 子字段最多下钻的层数（再多收益低、树也卡） */
const MAX_SUB_FIELD_DEPTH = 3;

/** JSON Schema 的 type 归一到变量类型（拿不准则按 object 处理） */
function jsonSchemaType(prop: any): ExtendedVariableType {
  const raw = Array.isArray(prop?.type)
    ? prop.type.find((item: string) => item !== 'null')
    : prop?.type;
  switch (raw) {
    case 'array': {
      return 'array';
    }
    case 'boolean': {
      return 'boolean';
    }
    case 'integer':
    case 'number': {
      return 'number';
    }
    case 'object': {
      return 'object';
    }
    case 'string': {
      return 'string';
    }
    default: {
      if (prop?.properties) return 'object';
      return prop?.items ? 'array' : 'object';
    }
  }
}

/** 结构化输出的 jsonSchema（表单里是文本框，解析失败就当没有结构） */
function parseJsonSchema(raw: any): any {
  if (!raw) return null;
  if (typeof raw === 'object') return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function fieldsFromSchema(
  schema: any,
  basePath: string,
  depth: number,
): PassThroughSubField[] {
  const properties = schema?.properties;
  if (
    !properties ||
    typeof properties !== 'object' ||
    depth >= MAX_SUB_FIELD_DEPTH
  ) {
    return [];
  }
  return Object.entries(properties)
    .slice(0, 50)
    .map(([key, prop]) => {
      const path = basePath ? `${basePath}.${key}` : key;
      const type = jsonSchemaType(prop);
      return {
        children:
          type === 'object' ? fieldsFromSchema(prop, path, depth + 1) : [],
        path,
        title: key,
        type,
      };
    });
}

/**
 * 一个输出变量可由「配置」推出的子字段。
 *
 * 目前只有大模型的结构化输出在画布上声明了结构：HTTP 响应体、代码/工具/MCP 返回值、
 * 检索结果这些要运行后才知道有哪些 key，所以不编进候选，改由表单手填子路径。
 */
export function declaredSubFields(
  node: GraphNode,
  variable: NodeVariable,
): PassThroughSubField[] {
  if (node.type !== 'LLM' || variable.name !== 'structured_output') return [];
  const schema = parseJsonSchema(node.config?.structuredOutput?.jsonSchema);
  return schema ? fieldsFromSchema(schema, '', 0) : [];
}

/** 在子字段树里按路径找到对应节点（只用于推类型，找不到不报错） */
function findSubField(
  fields: PassThroughSubField[],
  path: string,
): PassThroughSubField | undefined {
  for (const field of fields) {
    if (field.path === path) return field;
    const hit = findSubField(field.children, path);
    if (hit) return hit;
  }
  return undefined;
}

/** 变量类型族匹配（数组族/对象互相放行）的入参形态 */
function passthroughTag(label: string, variable: NodeVariable): NodeVariable {
  return {
    ...variable,
    description: `透传自 ${label}·${variable.description || variable.name}`,
  };
}

/**
 * 审批节点放行的输入变量（下游引用审批时看到的那批透传键）。
 *
 * - 未配 `passThroughInputs`：由近到远回溯全部上游（隔着普通节点继续往上，上游审批
 *   是屏障不再深入），同名键以先出现的那份（更接近本节点的）为准，开始入参铺最底层兜底；
 * - 配了 `passThroughInputs`：只列选中的 (节点, 变量, 子路径)，输出键名用配置里的
 *   name（缺省取路径末段），按选择顺序先选先胜。
 *
 * 审批结论键（review/reviewOpinion/reviewBy）由调用方（`nodeOutputVariables` 的
 * APPROVAL 分支）合并，同名以结论为准。
 */
export function approvalPassThroughVariables(
  node: GraphNode,
  graph: FlowGraph,
  seen: Set<string> = new Set(),
): NodeVariable[] {
  if (!node.id || seen.has(node.id)) return [];
  const nextSeen = new Set(seen);
  nextSeen.add(node.id);

  const nodeById = new Map(graph.nodes.map((item) => [item.id, item]));
  const merged = new Map<string, NodeVariable>();
  const selection = normalizePassThroughInputs(node.config.passThroughInputs);

  if (selection.length > 0) {
    selection.forEach((item) => {
      const outName = passThroughOutputName(item);
      if (merged.has(outName)) return;
      const source = nodeById.get(item.nodeId);
      const variables = source
        ? nodeOutputVariables(source, graph, nextSeen)
        : [];
      const hit = variables.find((variable) => variable.name === item.varName);
      let type: ExtendedVariableType = hit?.type || 'object';
      if (item.path && source && hit) {
        const field = findSubField(declaredSubFields(source, hit), item.path);
        if (field) type = field.type;
      }
      const trail = item.path ? `.${item.path}` : '';
      merged.set(outName, {
        name: outName,
        type,
        description: `透传自 ${source?.label || item.nodeId}·${item.varName}${trail}`,
      });
    });
    return [...merged.values()];
  }

  upstreamLayers(graph, node.id).forEach((layer) => {
    layer.forEach((source) => {
      nodeOutputVariables(source, graph, nextSeen).forEach((variable) => {
        if (merged.has(variable.name)) return;
        merged.set(variable.name, passthroughTag(source.label, variable));
      });
    });
  });
  const start = graph.nodes.find((item) => item.type === 'START');
  if (start && start.id !== node.id) {
    nodeOutputVariables(start, graph, nextSeen).forEach((variable) => {
      if (merged.has(variable.name)) return;
      merged.set(variable.name, passthroughTag(start.label, variable));
    });
  }
  return [...merged.values()];
}

/** 审批「选择输出参数」的一个可勾选目标：变量的整值，或它的某个子字段 */
export interface ApprovalPassThroughOption {
  /** 下一层可勾选项（由结构化输出 schema 推出） */
  children?: ApprovalPassThroughOption[];
  /** 树节点键：`nodeId::varName::path` */
  key: string;
  nodeId: string;
  nodeName: string;
  /** 相对变量名的子路径（''=整个变量） */
  path: string;
  /** 展示名 */
  title: string;
  type: ExtendedVariableType;
  varName: string;
}

/** 按上游节点分组的可勾选目标 */
export interface ApprovalPassThroughGroup {
  nodeName: string;
  nodeId: string;
  options: ApprovalPassThroughOption[];
}

function toPassThroughOption(
  nodeId: string,
  nodeName: string,
  varName: string,
  field: PassThroughSubField,
): ApprovalPassThroughOption {
  const option: ApprovalPassThroughOption = {
    key: encodePassThroughKey(nodeId, varName, field.path),
    nodeId,
    nodeName,
    path: field.path,
    title: `${field.title}（${field.type}）`,
    type: field.type,
    varName,
  };
  if (field.children.length > 0) {
    option.children = field.children.map((child) =>
      toPassThroughOption(nodeId, nodeName, varName, child),
    );
  }
  return option;
}

/**
 * 审批「选择输出参数」的可选项：按上游节点分组的勾选树（含开始节点入参）。
 *
 * 与 `approvalPassThroughVariables` 用同一批来源，区别是这里不合并同名键——配置者选的
 * 是「哪个节点的哪个变量的哪一层」，(节点, 变量, 子路径) 才是唯一标识。声明了结构的
 * 变量（大模型结构化输出）会展开到每个 key，其余变量只能整值勾选或手填子路径。
 */
export function approvalPassThroughOptions(
  node: GraphNode,
  graph: FlowGraph,
): ApprovalPassThroughGroup[] {
  const seen = new Set<string>([node.id]);
  const groups: Array<{ nodeId: string; nodeName: string }> = [];
  upstreamLayers(graph, node.id).forEach((layer) => {
    layer.forEach((source) =>
      groups.push({ nodeId: source.id, nodeName: source.label }),
    );
  });
  const start = graph.nodes.find(
    (item) => item.type === 'START' && item.id !== node.id,
  );
  if (start) groups.push({ nodeId: start.id, nodeName: start.label });

  const result: ApprovalPassThroughGroup[] = [];
  const taken = new Set<string>();
  groups.forEach((group) => {
    const source = graph.nodes.find((item) => item.id === group.nodeId);
    if (!source || seen.has(source.id)) return;
    seen.add(source.id);
    const options: ApprovalPassThroughOption[] = [];
    nodeOutputVariables(source, graph, seen).forEach((variable) => {
      const dedupe = `${source.id}::${variable.name}`;
      if (taken.has(dedupe)) return;
      taken.add(dedupe);
      const children = declaredSubFields(source, variable).map((field) =>
        toPassThroughOption(source.id, group.nodeName, variable.name, field),
      );
      const option: ApprovalPassThroughOption = {
        key: encodePassThroughKey(source.id, variable.name, ''),
        nodeId: source.id,
        nodeName: group.nodeName,
        path: '',
        title: `${variable.name}（${variable.type}）`,
        type: variable.type,
        varName: variable.name,
      };
      if (children.length > 0) option.children = children;
      options.push(option);
    });
    if (options.length > 0) {
      result.push({ nodeName: group.nodeName, nodeId: group.nodeId, options });
    }
  });
  return result;
}

/** 展平勾选树（表单按它判断「配置里的项还在不在候选里」与手填候选列表） */
export function flattenPassThroughOptions(
  groups: ApprovalPassThroughGroup[],
): ApprovalPassThroughOption[] {
  const items: ApprovalPassThroughOption[] = [];
  const walk = (option: ApprovalPassThroughOption) => {
    items.push(option);
    option.children?.forEach((child) => walk(child));
  };
  groups.forEach((group) => group.options.forEach((option) => walk(option)));
  return items;
}

/**
 * 节点声明的输出变量（APPROVAL 会附带它的透传键）。
 *
 * seen 是透传递归的去重环（防图上出现环时无限展开），调用方不必传。
 */
export function nodeOutputVariables(
  node: GraphNode,
  graph: FlowGraph,
  seen: Set<string> = new Set(),
): NodeVariable[] {
  const { config, type } = node;
  const variables: NodeVariable[] = [];

  switch (type) {
    case 'APPROVAL': {
      // 与后端 ApprovalNodeExecutor 输出对齐：结论三键恒有。同意与不同意都照常
      // 往下游走，要不要以结论分流是下游条件节点的事（审批节点不控制流转）。
      // 透传快照里的同名键以本节点结论为准，与后端的写入顺序一致。
      const conclusion: NodeVariable[] = [
        {
          name: 'review',
          type: 'boolean',
          description: '审批结论（true=同意）',
        },
        {
          name: 'reviewOpinion',
          type: 'string',
          description: '审批意见',
        },
        {
          name: 'reviewBy',
          type: 'string',
          description: '审批人标识',
        },
      ];
      const passThrough = approvalPassThroughVariables(
        node,
        graph,
        seen,
      ).filter((item) => !conclusion.some((own) => own.name === item.name));
      variables.push(...conclusion, ...passThrough);
      break;
    }

    case 'CODE': {
      // 代码节点输出固定为 { result: <返回值> }（参照 MaxKB ToolExecutor）
      variables.push({
        name: 'result',
        type: 'object',
        description: '代码执行返回值（类型不限）',
      });
      break;
    }

    case 'DOC_EXTRACTOR': {
      variables.push({
        name: config.outputVariable || 'text',
        type: 'string',
        description: '提取的文本内容',
      });
      if (config.extractMetadata) {
        variables.push({
          name: 'metadata',
          type: 'object',
          description: '文档元数据',
        });
      }
      break;
    }

    case 'HTTP_REQUEST': {
      variables.push(
        {
          name: config.outputVariable || 'response',
          type: 'object',
          description: 'HTTP 响应',
        },
        {
          name: 'status',
          type: 'number',
          description: 'HTTP 状态码',
        },
        {
          name: 'headers',
          type: 'object',
          description: '响应头',
        },
        {
          name: 'body',
          type: 'object',
          description: '响应体',
        },
      );
      break;
    }

    case 'ITERATION': {
      variables.push(
        {
          name: config.outputVariable || 'results',
          type: 'array',
          description: '迭代结果数组',
        },
        {
          name: 'item',
          type: 'object',
          description: '当前迭代元素',
        },
        {
          name: 'index',
          type: 'number',
          description: '当前迭代索引',
        },
      );
      break;
    }

    case 'KNOWLEDGE_RETRIEVAL': {
      variables.push({
        name: config.outputVariable || 'results',
        type: 'array',
        description: '检索结果列表',
      });
      break;
    }

    case 'LIST_OPERATOR': {
      // 默认输出名与后端 ListOperatorNodeExecutor（cfg.outputVariable or "output"）保持一致
      variables.push(
        {
          name: config.outputVariable || 'output',
          type: 'array',
          description: '列表操作结果',
        },
        {
          name: 'count',
          type: 'number',
          description: '输入数组元素数量',
        },
      );
      break;
    }

    case 'LLM': {
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: 'LLM 输出内容',
      });
      if (config.structuredOutput?.enabled) {
        variables.push({
          name: 'structured_output',
          type: 'object',
          description: '结构化输出',
        });
      }
      break;
    }

    case 'MCP_TOOL': {
      // 与后端 McpToolNodeExecutor 输出对齐：主变量 + content/urls
      variables.push(
        {
          name: config.outputVariable || 'result',
          type: 'object',
          description: 'MCP 工具结果（结构化输出优先，否则文本内容）',
        },
        {
          name: 'content',
          type: 'string',
          description: 'MCP 返回的文本内容',
        },
        {
          name: 'urls',
          type: 'array',
          description: '图片/音频等资源链接',
        },
      );
      break;
    }

    case 'PARAMETER_EXTRACTOR': {
      if (config.parameters && Array.isArray(config.parameters)) {
        config.parameters.forEach((param: any) => {
          // 未填写参数名的行属于待配置状态，不作为可引用变量输出
          if (!param?.name) {
            return;
          }
          variables.push({
            name: param.name,
            type: param.type || 'string',
            description: param.description,
          });
        });
      }
      variables.push(
        {
          name: '__is_success',
          type: 'boolean',
          description: '提取是否成功',
        },
        {
          name: '__reason',
          type: 'string',
          description: '失败原因',
        },
      );
      break;
    }

    case 'QUESTION_CLASSIFIER': {
      variables.push(
        {
          name: 'category',
          type: 'string',
          description: '分类结果',
        },
        {
          name: 'selectedBranch',
          type: 'string',
          description: '选中的分支ID',
        },
      );
      break;
    }

    case 'REPLY': {
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: '回复内容',
      });
      break;
    }

    case 'START': {
      // START 节点的输出是其定义的输入字段
      if (config.fields && Array.isArray(config.fields)) {
        config.fields.forEach((field: any) => {
          variables.push({
            name: field.name,
            type: mapInputFieldType(field.type),
            description: field.description || field.label,
          });
        });
      }
      break;
    }

    case 'TEMPLATE': {
      variables.push({
        name: config.outputVariable || 'output',
        type: 'string',
        description: '模板渲染结果',
      });
      break;
    }

    case 'TOOL': {
      // 工具节点与代码节点同口径，输出 { [outputVariable]: 返回值 }，默认 result
      variables.push({
        name: config.outputVariable || 'result',
        type: 'object',
        description: '工具执行结果',
      });
      break;
    }

    case 'VARIABLE_AGGREGATOR': {
      if (config.groups && Array.isArray(config.groups)) {
        config.groups.forEach((group: any) => {
          // 未填写输出名的聚合组属于待配置状态，不作为可引用变量输出
          if (!group.outputVariable) {
            return;
          }
          variables.push({
            name: group.outputVariable,
            type: group.variableType || 'object',
            description: '聚合变量',
          });
        });
      }
      break;
    }

    case 'VARIABLE_ASSIGNER': {
      if (config.assignments && Array.isArray(config.assignments)) {
        config.assignments.forEach((assignment: any) => {
          variables.push({
            name: assignment.variableName,
            type: assignment.variableType || 'string',
            description: assignment.description,
          });
        });
      }
      break;
    }

    case 'WORKFLOW': {
      // 与后端 WorkflowNodeExecutor 输出对齐：子工作流 END 的全量输出 + 可读文本
      variables.push(
        {
          name: config.outputVariable || 'result',
          type: 'object',
          description: '子工作流 END 的全部输出',
        },
        {
          name: 'text',
          type: 'string',
          description: '子工作流回答的文本内容',
        },
      );
      break;
    }

    default: {
      // 默认输出
      variables.push({
        name: 'output',
        type: 'object',
        description: '节点输出',
      });
    }
  }

  return variables;
}

/**
 * 映射输入字段类型到变量类型
 */
export function mapInputFieldType(fieldType: string): ExtendedVariableType {
  const typeMap: Record<string, ExtendedVariableType> = {
    // 短/长文本已合并为 TEXT，旧图的 SHORT_TEXT / PARAGRAPH 仍保留映射
    TEXT: 'string',
    SHORT_TEXT: 'string',
    PARAGRAPH: 'string',
    NUMBER: 'number',
    SELECT: 'string',
    CHECKBOX: 'boolean',
    SINGLE_FILE: 'File',
    FILE_LIST: 'Array[File]',
    // 审批入参：一组审批人标识（数字或字符串）
    APPROVER: 'array',
  };
  return typeMap[fieldType] || 'string';
}
