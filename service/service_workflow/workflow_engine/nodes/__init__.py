# -*- coding: utf-8 -*-
"""节点执行器注册表（对照 MaxKB application/flow/step_node/__init__.py 的 node_map）。

引擎与节点解耦的核心：NODE_REGISTRY = {node_type: ExecutorClass}。
新增节点类型只需实现 BaseNodeExecutor 子类并在本文件注册。
"""
from service.service_workflow.workflow_engine.nodes.ai_nodes import (
    LLMNodeExecutor, ParameterExtractorNodeExecutor,
    QuestionClassifierNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.approval_nodes import (
    ApprovalNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.base import BaseNodeExecutor
from service.service_workflow.workflow_engine.nodes.compound_nodes import (
    IterationNodeExecutor, LoopNodeExecutor, ParallelNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.control_nodes import (
    EndNodeExecutor, IfElseNodeExecutor, StartNodeExecutor,
    VariableAggregatorNodeExecutor, VariableAssignerNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.data_nodes import (
    CodeNodeExecutor, DocExtractorNodeExecutor, KnowledgeRetrievalNodeExecutor,
    ListOperatorNodeExecutor, ReplyNodeExecutor, TemplateNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.external_nodes import (
    HttpRequestNodeExecutor, McpToolNodeExecutor, ToolNodeExecutor,
)
from service.service_workflow.workflow_engine.nodes.subworkflow_nodes import (
    WorkflowNodeExecutor,
)

NODE_REGISTRY: dict[str, type[BaseNodeExecutor]] = {
    # 边界
    StartNodeExecutor.node_type: StartNodeExecutor,
    EndNodeExecutor.node_type: EndNodeExecutor,
    # AI
    LLMNodeExecutor.node_type: LLMNodeExecutor,
    QuestionClassifierNodeExecutor.node_type: QuestionClassifierNodeExecutor,
    ParameterExtractorNodeExecutor.node_type: ParameterExtractorNodeExecutor,
    # 控制流
    IfElseNodeExecutor.node_type: IfElseNodeExecutor,
    LoopNodeExecutor.node_type: LoopNodeExecutor,
    IterationNodeExecutor.node_type: IterationNodeExecutor,
    ParallelNodeExecutor.node_type: ParallelNodeExecutor,
    VariableAssignerNodeExecutor.node_type: VariableAssignerNodeExecutor,
    VariableAggregatorNodeExecutor.node_type: VariableAggregatorNodeExecutor,
    # 人工审批（业务逻辑：在节点边界挂起，由带 decisions 的 submit 恢复）
    ApprovalNodeExecutor.node_type: ApprovalNodeExecutor,
    # 数据
    TemplateNodeExecutor.node_type: TemplateNodeExecutor,
    ReplyNodeExecutor.node_type: ReplyNodeExecutor,
    CodeNodeExecutor.node_type: CodeNodeExecutor,
    ListOperatorNodeExecutor.node_type: ListOperatorNodeExecutor,
    DocExtractorNodeExecutor.node_type: DocExtractorNodeExecutor,
    KnowledgeRetrievalNodeExecutor.node_type: KnowledgeRetrievalNodeExecutor,
    # 外部
    HttpRequestNodeExecutor.node_type: HttpRequestNodeExecutor,
    ToolNodeExecutor.node_type: ToolNodeExecutor,
    McpToolNodeExecutor.node_type: McpToolNodeExecutor,
    # 嵌套调用：进程内起一条子工作流执行（可随子流程的审批一起挂起）
    WorkflowNodeExecutor.node_type: WorkflowNodeExecutor,
}

# 前端节点类型全集（types.ts NodeType）
ALL_NODE_TYPES = [
    "START", "END", "LLM", "IF_ELSE", "ITERATION", "LOOP", "PARALLEL",
    "CODE", "TEMPLATE", "REPLY", "HTTP_REQUEST", "TOOL", "MCP_TOOL", "KNOWLEDGE_RETRIEVAL",
    "WORKFLOW",
    "PARAMETER_EXTRACTOR", "QUESTION_CLASSIFIER", "LIST_OPERATOR",
    "VARIABLE_AGGREGATOR", "VARIABLE_ASSIGNER", "DOC_EXTRACTOR", "APPROVAL",
]
