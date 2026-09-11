# -*- coding: utf-8 -*-
"""节点执行器注册表（对照 MaxKB application/flow/step_node/__init__.py 的 node_map）。

引擎与节点解耦的核心：NODE_REGISTRY = {node_type: ExecutorClass}。
新增节点类型只需实现 BaseNodeExecutor 子类并在本文件注册。
"""
from service.service_workflow.workflow_engine.nodes.ai_nodes import (
    AgentNodeExecutor, LLMNodeExecutor, ParameterExtractorNodeExecutor,
    QuestionClassifierNodeExecutor,
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
    HttpRequestNodeExecutor, ToolNodeExecutor,
)

NODE_REGISTRY: dict[str, type[BaseNodeExecutor]] = {
    # 边界
    StartNodeExecutor.node_type: StartNodeExecutor,
    EndNodeExecutor.node_type: EndNodeExecutor,
    # AI
    LLMNodeExecutor.node_type: LLMNodeExecutor,
    QuestionClassifierNodeExecutor.node_type: QuestionClassifierNodeExecutor,
    ParameterExtractorNodeExecutor.node_type: ParameterExtractorNodeExecutor,
    AgentNodeExecutor.node_type: AgentNodeExecutor,
    # 控制流
    IfElseNodeExecutor.node_type: IfElseNodeExecutor,
    LoopNodeExecutor.node_type: LoopNodeExecutor,
    IterationNodeExecutor.node_type: IterationNodeExecutor,
    ParallelNodeExecutor.node_type: ParallelNodeExecutor,
    VariableAssignerNodeExecutor.node_type: VariableAssignerNodeExecutor,
    VariableAggregatorNodeExecutor.node_type: VariableAggregatorNodeExecutor,
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
}

# 前端 21 种节点类型全集（types.ts NodeType）
ALL_NODE_TYPES = [
    "START", "END", "LLM", "AGENT", "IF_ELSE", "ITERATION", "LOOP", "PARALLEL",
    "CODE", "TEMPLATE", "REPLY", "HTTP_REQUEST", "TOOL", "KNOWLEDGE_RETRIEVAL",
    "PARAMETER_EXTRACTOR", "QUESTION_CLASSIFIER", "LIST_OPERATOR",
    "VARIABLE_AGGREGATOR", "VARIABLE_ASSIGNER", "DOC_EXTRACTOR",
]
