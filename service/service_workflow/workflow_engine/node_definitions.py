# -*- coding: utf-8 -*-
"""节点定义清单：GET /api/workflow/workflows/node-definitions 数据源。

对齐前端 types.ts WorkflowNodeDefinitionResp 结构；前端 NodePanel 用它渲染节点面板，
属性面板用 configSchema 校验。此清单是后端权威（引擎注册表支持的全部类型）。
"""
from __future__ import annotations

from typing import Any


def _port(pid: str, name: str, direction: str, multiple: bool = True) -> dict:
    return {"id": pid, "name": name, "direction": direction, "multiple": multiple}


def _field(name: str, label: str, component: str, value_type: str,
           required: bool = False, default: Any = None, placeholder: str = "",
           help_text: str = "", options: list = None, rules: list = None) -> dict:
    f: dict = {"name": name, "label": label, "component": component,
               "valueType": value_type, "required": required}
    if default is not None:
        f["defaultValue"] = default
    if placeholder:
        f["placeholder"] = placeholder
    if help_text:
        f["help"] = help_text
    if options:
        f["options"] = [{"label": o[0] if isinstance(o, tuple) else o,
                         "value": o[1] if isinstance(o, tuple) else o} for o in options]
    if rules:
        f["rules"] = rules
    return f


def _def(node_type, display, desc, category, icon, color, *, start=False,
         terminal=False, inputs=None, outputs=None, form_component="",
         required_fields=None, output_variables=None, fields=None, default_config=None) -> dict:
    return {
        "type": node_type,
        "displayName": display,
        "description": desc,
        "category": category,
        "icon": icon,
        "color": color,
        "start": start,
        "terminal": terminal,
        "inputs": inputs or [_port("input", "输入", "input")],
        "outputs": outputs or [_port("output", "输出", "output")],
        "configSchema": {
            "formComponent": form_component,
            "requiredFields": required_fields or [],
            "outputVariables": output_variables or [],
            "fields": fields or [],
        },
        "defaultConfig": default_config or {},
    }


IN_PORT = [_port("input", "输入", "input")]
OUT_PORT = [_port("output", "输出", "output")]


def build_node_definitions() -> list[dict]:
    defs = []

    # ---------- 边界 ----------
    defs.append(_def(
        "START", "开始", "定义工作流输入参数（表单字段）", "basic", "PlayCircle", "#52c41a",
        start=True, inputs=[],
        outputs=[_port("output", "输出", "output")],
        form_component="StartNodeForm",
        output_variables=["每个输入字段"],
        fields=[
            _field("fields", "输入字段", "InputFieldList", "array", False, []),
        ],
        default_config={"fields": [
            {"name": "query", "label": "用户问题", "type": "PARAGRAPH", "required": True},
        ]},
    ))
    defs.append(_def(
        "END", "结束", "定义工作流输出变量与回答模板", "basic", "StopCircle", "#ff4d4f",
        terminal=True, inputs=IN_PORT, outputs=[],
        form_component="EndNodeForm",
        fields=[
            _field("outputs", "输出变量", "OutputFieldList", "array", False, []),
            _field("answerTemplate", "回答模板", "Textarea", "string", False, "",
                   "支持 {{节点ID.变量}} 引用"),
            _field("outputMode", "输出模式", "Select", "string", False, "TEXT",
                   options=[("TEXT", "文本"), ("JSON", "JSON"), ("TEMPLATE", "模板")]),
        ],
        default_config={"outputs": [], "outputMode": "TEMPLATE", "streaming": True},
    ))

    # ---------- AI ----------
    defs.append(_def(
        "LLM", "大模型", "调用大语言模型推理，支持流式/Vision/结构化输出", "ai", "Robot", "#1677ff",
        form_component="LlmNodeForm",
        required_fields=["modelId"],
        output_variables=["output", "text", "reasoning", "usage"],
        fields=[
            _field("modelId", "模型", "ModelSelect", "number", True, None, "", "模型广场中启用的模型"),
            _field("systemPrompt", "系统提示词", "Textarea", "string", False, ""),
            _field("promptTemplate", "用户提示词", "PromptEditor", "string", False, "",
                   "支持 {{节点ID.变量}} 引用"),
            _field("temperature", "温度", "Slider", "number", False, 0.7,
                   rules=[{"type": "min", "value": 0, "message": "最小 0"},
                          {"type": "max", "value": 2, "message": "最大 2"}]),
            _field("maxTokens", "最大Token", "InputNumber", "number", False, 2048),
            _field("streaming", "流式输出", "Switch", "boolean", False, True),
            _field("visionEnabled", "图像理解", "Switch", "boolean", False, False),
            _field("memoryEnabled", "对话记忆", "Switch", "boolean", False, False),
            _field("structuredOutput", "结构化输出", "StructuredOutputForm", "object", False,
                   {"enabled": False}),
        ],
        default_config={"temperature": 0.7, "maxTokens": 2048, "streaming": True,
                        "outputVariable": "output"},
    ))
    defs.append(_def(
        "QUESTION_CLASSIFIER", "问题分类器", "LLM 智能分类并路由到不同分支", "ai", "BranchesOutlined", "#722ed1",
        outputs=[_port("branch:{categoryId}", "分类分支", "output")],
        form_component="QuestionClassifierForm",
        required_fields=["modelId", "categories"],
        output_variables=["category", "categoryName"],
        fields=[
            _field("modelId", "分类模型", "ModelSelect", "number", True),
            _field("inputVariable", "输入变量", "VariableSelect", "string", True),
            _field("instructions", "分类说明", "Textarea", "string", False,
                   "将用户问题分类到最合适的类别。"),
            _field("categories", "分类类别", "ClassCategoryList", "array", True, []),
        ],
        default_config={"categories": [
            {"id": "cat_1", "name": "售前咨询"},
            {"id": "cat_2", "name": "售后支持"},
        ]},
    ))
    defs.append(_def(
        "PARAMETER_EXTRACTOR", "参数提取器", "LLM 从文本中提取结构化参数", "ai", "FilterOutlined", "#13c2c2",
        form_component="ParameterExtractorForm",
        required_fields=["modelId", "parameters"],
        output_variables=["每个提取参数"],
        fields=[
            _field("modelId", "提取模型", "ModelSelect", "number", True),
            _field("inputVariable", "输入变量", "VariableSelect", "string", True),
            _field("instructions", "提取说明", "Textarea", "string", False,
                   "从文本中提取结构化参数。"),
            _field("parameters", "参数列表", "ExtractParameterList", "array", True, []),
        ],
        default_config={"parameters": [], "inferenceMode": "PROMPT_BASED"},
    ))
    defs.append(_def(
        "AGENT", "智能体", "调用平台已配置的智能体完成复杂任务", "ai", "UserSwitchOutlined", "#eb2f96",
        form_component="AgentNodeForm",
        required_fields=["agentId"],
        output_variables=["output"],
        fields=[
            _field("agentId", "智能体", "AgentSelect", "number", True),
            _field("outputVariable", "输出变量名", "Input", "string", False, "output"),
        ],
    ))

    # ---------- 控制流 ----------
    defs.append(_def(
        "IF_ELSE", "条件分支", "按条件路由（IF/ELIF/ELSE 多分支，AND/OR 组合）", "control", "ForkOutlined", "#fa8c16",
        outputs=[_port("branch:{branchId}", "条件分支", "output")],
        form_component="IfElseNodeForm",
        output_variables=["branch", "branchLabel"],
        fields=[
            _field("branches", "分支列表", "ConditionBranchList", "array", False, [
                {"id": "if_1", "label": "IF", "type": "IF",
                 "conditions": [{"variable": "", "operator": "EQUALS", "value": ""}],
                 "operator": "AND"},
                {"id": "else_1", "label": "ELSE", "type": "ELSE"},
            ]),
        ],
    ))
    defs.append(_def(
        "LOOP", "循环", "while 循环执行子图，直到退出条件满足", "control", "SyncOutlined", "#faad14",
        outputs=[_port("branch:body", "循环体", "output"), _port("output", "退出", "output")],
        form_component="LoopNodeForm",
        required_fields=["exitCondition"],
        output_variables=["loopResult", "iterations"],
        fields=[
            _field("exitCondition", "退出条件", "ExpressionInput", "string", True,
                   "{{flag}} == true", "", "支持 true/false 或简单比较表达式"),
            _field("maxIterations", "最大迭代", "InputNumber", "number", False, 1000),
            _field("loopVariable", "循环变量名", "Input", "string", False, "loopIndex"),
        ],
    ))
    defs.append(_def(
        "ITERATION", "迭代", "对数组逐元素执行子图（体内用 {{item}}/{{index}}）", "control", "RetweetOutlined", "#2f54eb",
        outputs=[_port("branch:body", "迭代体", "output"), _port("output", "汇总", "output")],
        form_component="IterationNodeForm",
        required_fields=["arrayVariable"],
        output_variables=["items", "count"],
        fields=[
            _field("arrayVariable", "数组变量", "VariableSelect", "string", True),
            _field("processingMode", "处理模式", "Select", "string", False, "SEQUENTIAL",
                   options=[("SEQUENTIAL", "顺序"), ("PARALLEL", "并行")]),
            _field("parallelCount", "并行数", "InputNumber", "number", False, 3),
            _field("iterationTimeout", "单次超时(ms)", "InputNumber", "number", False, 0),
            _field("maxIterations", "最大迭代", "InputNumber", "number", False, 1000),
        ],
    ))
    defs.append(_def(
        "PARALLEL", "并行", "多分支并行执行，按策略汇合（ALL/ANY/FIRST）", "control", "AppstoreOutlined", "#a0d911",
        outputs=[_port("branch:{id}", "并行分支", "output")],
        form_component="ParallelNodeForm",
        output_variables=["branches", "completed"],
        fields=[
            _field("waitStrategy", "等待策略", "Select", "string", False, "ALL",
                   options=[("ALL", "全部完成"), ("ANY", "任一完成"), ("FIRST", "第一个完成")]),
            _field("branches", "分支配置", "BranchList", "array", False, []),
            _field("timeout", "超时(ms)", "InputNumber", "number", False, 0),
        ],
    ))
    defs.append(_def(
        "VARIABLE_ASSIGNER", "变量赋值", "设置/转换全局变量", "control", "EditOutlined", "#bfbfbf",
        form_component="VariableAssignerForm",
        output_variables=["每个赋值变量"],
        fields=[
            _field("assignments", "赋值列表", "AssignmentList", "array", False, []),
        ],
    ))
    defs.append(_def(
        "VARIABLE_AGGREGATOR", "变量聚合", "合并多分支输出变量", "control", "MergeCellsOutlined", "#8c8c8c",
        form_component="VariableAggregatorForm",
        output_variables=["每个聚合变量"],
        fields=[
            _field("groups", "聚合组", "AggregationGroupList", "array", False, []),
        ],
    ))

    # ---------- 数据 ----------
    defs.append(_def(
        "TEMPLATE", "模板转换", "用模板引擎转换/拼接文本", "data", "BlockOutlined", "#0958d9",
        form_component="TemplateNodeForm",
        required_fields=["template"],
        output_variables=["output"],
        fields=[
            _field("template", "模板内容", "Textarea", "string", True, ""),
            _field("engine", "模板引擎", "Select", "string", False, "SIMPLE",
                   options=[("SIMPLE", "简单变量替换"), ("JINJA2", "Jinja2")]),
            _field("variables", "模板变量", "TemplateVariableList", "array", False, []),
            _field("strictMode", "严格模式", "Switch", "boolean", False, False),
        ],
    ))
    defs.append(_def(
        "CODE", "代码执行", "执行 Python 代码（受限沙箱）", "data", "CodeOutlined", "#531dab",
        form_component="CodeNodeForm",
        required_fields=["code"],
        output_variables=["main() 返回的每个变量"],
        fields=[
            _field("language", "语言", "Select", "string", False, "PYTHON",
                   options=[("PYTHON", "Python")]),
            _field("code", "代码", "CodeEditor", "string", True,
                   "def main(**kwargs):\n    return {'output': ''}"),
            _field("inputs", "输入变量", "CodeVariableList", "array", False, []),
            _field("timeout", "超时(ms)", "InputNumber", "number", False, 10000),
            _field("sandboxEnabled", "沙箱模式", "Switch", "boolean", False, True),
        ],
    ))
    defs.append(_def(
        "LIST_OPERATOR", "列表处理", "过滤/排序/切片/提取等 12 种数组操作", "data", "OrderedListOutlined", "#08979c",
        form_component="ListOperatorForm",
        required_fields=["inputVariable"],
        output_variables=["output", "count"],
        fields=[
            _field("inputVariable", "输入数组", "VariableSelect", "string", True),
            _field("operationType", "操作类型", "Select", "string", True, "FILTER",
                   options=[("FILTER", "过滤"), ("SORT", "排序"), ("SLICE", "切片"),
                            ("EXTRACT", "提取字段"), ("UNIQUE", "去重"), ("LIMIT", "限量"),
                            ("CONCAT", "合并"), ("FIRST", "首元素"), ("LAST", "末元素"),
                            ("COUNT", "计数"), ("REVERSE", "反转"), ("FLATTEN", "扁平化")]),
        ],
    ))
    defs.append(_def(
        "DOC_EXTRACTOR", "文档提取", "从 PDF/Word/Excel 等文档提取文本", "data", "FileTextOutlined", "#c41d7f",
        form_component="DocExtractorForm",
        required_fields=["fileVariable"],
        output_variables=["content", "metadata"],
        fields=[
            _field("fileVariable", "文件变量", "VariableSelect", "string", True),
            _field("extractMetadata", "提取元数据", "Switch", "boolean", False, False),
        ],
    ))
    defs.append(_def(
        "KNOWLEDGE_RETRIEVAL", "知识检索", "从知识库检索相关内容（支持混合检索/重排）", "data", "DatabaseOutlined", "#389e0d",
        form_component="KnowledgeRetrievalForm",
        required_fields=["knowledgeBaseIds", "queryVariable"],
        output_variables=["documents", "text", "count"],
        fields=[
            _field("knowledgeBaseIds", "知识库", "KnowledgeBaseSelect", "array", True),
            _field("queryVariable", "查询变量", "VariableSelect", "string", True),
            _field("topK", "检索数量", "InputNumber", "number", False, 5),
            _field("scoreThreshold", "相似度阈值", "Slider", "number", False, 0,
                   rules=[{"type": "min", "value": 0, "message": "最小 0"},
                          {"type": "max", "value": 1, "message": "最大 1"}]),
            _field("retrievalMode", "检索模式", "Select", "string", False, "VECTOR",
                   options=[("VECTOR", "向量"), ("FULLTEXT", "全文"), ("HYBRID", "混合")]),
            _field("rerankConfig", "重排序", "RerankForm", "object", False, {"enabled": False}),
        ],
    ))

    # ---------- 外部 ----------
    defs.append(_def(
        "HTTP_REQUEST", "HTTP 请求", "调用外部 HTTP 接口", "external", "ApiOutlined", "#d4380d",
        form_component="HttpRequestNodeForm",
        required_fields=["url"],
        output_variables=["response", "statusCode", "body"],
        fields=[
            _field("url", "请求地址", "Input", "string", True, "",
                   rules=[{"type": "url", "value": None, "message": "请输入合法 URL"}]),
            _field("method", "请求方法", "Select", "string", False, "GET",
                   options=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]),
            _field("headers", "请求头", "KeyValueList", "object", False, {}),
            _field("queryParams", "查询参数", "KeyValueList", "object", False, {}),
            _field("bodyType", "请求体类型", "Select", "string", False, "NONE",
                   options=[("NONE", "无"), ("JSON", "JSON"), ("RAW", "原始文本"),
                            ("X_WWW_FORM_URLENCODED", "表单"), ("FORM_DATA", "multipart"),
                            ("BINARY", "二进制")]),
            _field("auth", "认证", "AuthForm", "object", False, {"type": "NONE"}),
            _field("retry", "重试", "RetryForm", "object", False, {"enabled": False}),
        ],
    ))
    defs.append(_def(
        "TOOL", "工具", "调用动态函数工具或 MCP 工具", "external", "ToolOutlined", "#d46b08",
        form_component="ToolNodeForm",
        required_fields=["toolName"],
        output_variables=["output"],
        fields=[
            _field("mcpServerId", "MCP 服务", "McpServerSelect", "number", False),
            _field("toolName", "工具", "ToolSelect", "string", True),
            _field("toolParams", "工具参数", "JsonValueEditor", "object", False, {}),
        ],
    ))
    return defs


# 模块级缓存（进程内只构建一次）
_NODE_DEFINITIONS: list[dict] | None = None


def get_node_definitions() -> list[dict]:
    global _NODE_DEFINITIONS
    if _NODE_DEFINITIONS is None:
        _NODE_DEFINITIONS = build_node_definitions()
    return _NODE_DEFINITIONS
