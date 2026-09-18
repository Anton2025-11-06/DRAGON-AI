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
            {"name": "query", "label": "用户问题", "type": "TEXT", "required": True},
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
        "LLM", "大模型", "按模型能力类型调用（支持全部 12 类：文生文/向量/重排/图文理解/OCR/图像音视频生成等）", "ai", "Robot", "#1677ff",
        form_component="LlmNodeForm",
        required_fields=["modelId"],
        output_variables=["output", "text", "reasoning", "usage", "memoryWarning",
                          "vectors", "scores", "urls", "url"],
        fields=[
            _field("modelId", "模型", "ModelSelect", "number", True, None, "", "模型广场中启用的模型（能力类型决定入参形态）"),
            _field("systemPrompt", "系统提示词", "Textarea", "string", False, ""),
            _field("promptTemplate", "提示词/输入文本", "PromptEditor", "string", False, "",
                   "支持 {{节点ID.变量}} 引用（文生文/理解/生成/语音合成类作为主输入）"),
            _field("inputVariable", "输入变量", "VariableSelect", "string", False,
                   "", "文本向量/图生文等单值输入（可引用上游文本/数组）"),
            _field("imageVariable", "图片输入", "VariableSelect", "string", False,
                   "", "图片理解/OCR/图片向量/图生视频的图片来源（引用上游 url、开始节点文件参数或直接贴 URL）"),
            _field("audioVariable", "音频输入", "VariableSelect", "string", False,
                   "", "音频转文字的音频来源（开始节点文件参数取其上传 url）"),
            _field("videoVariable", "视频输入", "VariableSelect", "string", False,
                   "", "视频理解的视频来源（开始节点文件参数取其上传 url）"),
            _field("queryVariable", "重排查询", "VariableSelect", "string", False,
                   "", "文本重排的 query"),
            _field("documentsVariable", "重排文档", "VariableSelect", "string", False,
                   "", "文本重排的候选文档数组"),
            # 温度/maxTokens 等模型调用参数不列为节点字段（12 类能力一致）：
            # 统一走「常用参数」config.params（默认取模型管理登记值），
            # 运行时由 LLMNodeExecutor._merge_node_params 合并进 model_params 透传厂商。
            # 流式/思考由模型能力位裁定（supports_stream/supports_thinking），能力未开启时表单不展示也不写入
            _field("streaming", "流式输出", "Switch", "boolean", False, None,
                   "", "仅文生文/图片理解/视频理解且模型管理开启 supports_stream 时生效"),
            _field("thinking", "深度思考", "Switch", "boolean", False, None,
                   "", "仅文生文/图片理解/视频理解且模型管理开启 supports_thinking 时生效"),
            _field("visionEnabled", "图像理解(对话)", "Switch", "boolean", False, False),
            _field("structuredOutput", "结构化输出", "StructuredOutputForm", "object", False,
                   {"enabled": False}),
            # 记忆（需求 1）：历史存在 node_states[nid].llmMessages，跨轮（同一 execution_id）生效。
            # 向量/重排/语音识别/语音合成四类不能开记忆（表单隐藏 + 后端 validate ERROR），
            # 所以不能进 defaultConfig：否则会给不支持的类型也写上一个已开启的开关。
            _field("memoryEnabled", "记忆", "Switch", "boolean", False, False,
                   "同一执行 id（即会话）内的大模型对话历史，下一轮自动带入"),
            _field("memoryLimit", "记忆条数", "InputNumber", "number", False, 10,
                   "按消息条数计（一轮 = 提问+回答 两条），上限 100",
                   rules=[{"type": "min", "value": 1, "message": "最少 1 条"},
                          {"type": "max", "value": 100, "message": "最多 100 条"}]),
            _field("memoryScope", "记忆范围", "Select", "string", False, "SELF",
                   options=[("SELF", "本节点"), ("NODES", "指定节点"), ("WORKFLOW", "整条工作流")]),
            _field("memoryNodes", "记忆节点", "MultiNodeSelect", "array", False, [],
                   "仅「指定节点」范围生效：只取这些大模型节点的历史"),
            _field("memoryStrategy", "超限策略", "Select", "string", False, "DROP_OLDEST",
                   "历史超出条数上限时怎么处理",
                   options=[("DROP_OLDEST", "丢弃最旧"), ("DROP_MIDDLE", "丢弃中间"),
                            ("DROP_NEWEST", "丢弃最新"), ("COMPRESS", "自动压缩")]),
            _field("memoryCompressModelId", "压缩模型", "ModelSelect", "number", False, None,
                   "仅「自动压缩」需要：选一个文生文模型把旧历史摘要成一条"),
        ],
        # 默认只给输出变量名：调用参数与流式都不预置，避免把模型未开启的参数存进图
        default_config={"outputVariable": "output"},
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
        # 另附带内置状态变量 __is_success / __reason（执行器始终写入，提取失败也不报错）
        output_variables=["每个提取参数", "__is_success", "__reason"],
        fields=[
            _field("modelId", "提取模型", "ModelSelect", "number", True),
            _field("inputVariable", "输入变量", "VariableSelect", "string", True),
            _field("instructions", "提取说明", "Textarea", "string", False,
                   "从文本中提取结构化参数。"),
            _field("parameters", "参数列表", "ExtractParameterList", "array", True, []),
        ],
        # 提取固定走 prompt 方式（后端拼 JSON schema 提示 + response_format=json_object），
        # 不再提供 inferenceMode 选项：模型未登记的 function-call 通道无法保证可用
        default_config={"parameters": []},
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
    defs.append(_def(
        "REPLY", "指定回复", "将引用参数的值或自定义文本作为回复内容输出", "control",
        "CommentOutlined", "#2f54eb",
        form_component="ReplyNodeForm",
        required_fields=["replyType"],
        output_variables=["output"],
        fields=[
            _field("replyType", "回复方式", "Select", "string", False, "TEXT",
                   options=[("TEXT", "自定义文本"), ("VARIABLE", "引用参数")]),
            _field("variableRef", "引用参数", "VariableSelect", "string", False, "",
                   "", "引用上游节点输出的参数，回复其值（需选择「引用参数」方式）"),
            _field("text", "回复内容", "Textarea", "string", False, "",
                   "支持 {{节点ID.变量}} 引用"),
            _field("outputVariable", "输出变量名", "Input", "string", False, "output"),
        ],
        default_config={"replyType": "TEXT", "text": "", "variableRef": "",
                        "outputVariable": "output"},
    ))
    # 人工审批：在节点边界暂停整条流，结论由「指定 executionId 再提交」接口带回
    defs.append(_def(
        "APPROVAL", "审批", "暂停等待人工审批：同意则继续（可编辑上游数据），不同意则取消下游", "control",
        "SafetyCertificateOutlined", "#13c2c2",
        form_component="ApprovalNodeForm",
        required_fields=["pauseScope"],
        output_variables=["review", "reviewOpinion", "reviewBy"],
        fields=[
            _field("approvers", "审批人", "ApproverList", "array", False, [],
                   "允许审批的标识集合（数字或字符串，命中其一即可）；留空=任何持有 api-key 且知道执行 id 者皆可审"),
            _field("pauseScope", "暂停范围", "Select", "string", False, "DOWNSTREAM",
                   "ALL：整条工作流一起停（在跑分支会被取消，恢复后重跑）；DOWNSTREAM：仅本节点及下游等待",
                   options=[("DOWNSTREAM", "本节点及下游"), ("ALL", "整条工作流")]),
            _field("rejectReply", "拒绝回复文案", "Textarea", "string", False, "",
                   "不同意时作为工作流回复输出兜底（下游 END/回复节点被取消时使用）"),
            _field("timeoutHours", "审批时限(小时)", "InputNumber", "number", False, 0,
                   "0=不限；超时自动裁决二期实现"),
        ],
        default_config={"approvers": [], "pauseScope": "DOWNSTREAM", "rejectReply": "",
                        "timeoutHours": 0},
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
        "CODE", "代码执行", "执行 Python 代码（import 导包 + 自动识别入口函数，返回任意值）", "data", "CodeOutlined", "#531dab",
        form_component="CodeNodeForm",
        required_fields=["code"],
        output_variables=["result"],
        fields=[
            _field("code", "代码", "CodeEditor", "string", True,
                   "import json\n\ndef main(**kwargs):\n    return 'hello'"),
            _field("inputs", "参数", "CodeParameterList", "array", False, [],
                   "配置方法入参：参数名 / 类型 / 是否必填 / 来源（引用参数或自定义值），代码中用 kwargs 接收"),
            _field("timeout", "超时(ms)", "InputNumber", "number", False, 10000),
        ],
    ))
    defs.append(_def(
        "LIST_OPERATOR", "列表处理", "过滤/排序/切片/提取等 12 种数组操作", "data", "OrderedListOutlined", "#08979c",
        form_component="ListOperatorForm",
        required_fields=["inputVariable"],
        output_variables=["output", "count"],
        fields=[
            _field("inputVariable", "输入数组", "VariableSelect", "string", True,
                   None, "", "可引用全部上游节点的输出（不限数组）；支持 .字段/[下标] 再次提取，"
                             "上游为 JSON 文本时会自动解析后取值"),
            _field("operationType", "操作类型", "Select", "string", True, "FILTER",
                   options=[("FILTER", "过滤"), ("SORT", "排序"), ("SLICE", "切片"),
                            ("EXTRACT", "提取字段"), ("UNIQUE", "去重"), ("LIMIT", "限量"),
                            ("CONCAT", "合并"), ("FIRST", "首元素"), ("LAST", "末元素"),
                            ("COUNT", "计数"), ("REVERSE", "反转"), ("FLATTEN", "扁平化")]),
        ],
    ))
    defs.append(_def(
        "DOC_EXTRACTOR", "文档提取", "从 PDF/Word/Excel/PPT/Markdown/HTML 等文档提取文本", "data", "FileTextOutlined", "#c41d7f",
        form_component="DocExtractorForm",
        required_fields=["fileVariable"],
        output_variables=["content", "metadata"],
        fields=[
            _field("fileVariable", "文件变量", "VariableSelect", "string", True,
                   None, "", "引用开始节点的文件参数（按上传接口返回的 url 拉取后内存解析，不再支持本地路径）"),
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
        "TOOL", "工具", "调用工具库登记的动态函数工具（Python，与代码节点同一沙箱）", "external",
        "ToolOutlined", "#d46b08",
        form_component="ToolNodeForm",
        required_fields=["toolId"],
        output_variables=["result"],
        fields=[
            _field("toolId", "工具", "ToolSelect", "number", True, None, "",
                   "工具库中启用中的动态函数工具（选择后自动列出它的入参）"),
            _field("inputs", "参数", "CodeParameterList", "array", False, [],
                   "按工具参数定义绑定：引用上游变量或自定义值，未绑定项回落工具默认值"),
            _field("timeout", "超时(ms)", "InputNumber", "number", False, None,
                   "", "留空则取工具登记的超时"),
            _field("outputVariable", "输出变量名", "Input", "string", False, "result"),
        ],
        default_config={"inputs": [], "outputVariable": "result"},
    ))
    defs.append(_def(
        "MCP_TOOL", "MCP 工具", "调用 MCP 连接提供的工具", "external",
        "CloudServerOutlined", "#0e7fa8",
        form_component="McpNodeForm",
        required_fields=["mcpServerId", "toolName"],
        output_variables=["result", "content", "urls"],
        fields=[
            _field("mcpServerId", "MCP 连接", "McpServerSelect", "number", True),
            _field("toolName", "MCP 工具", "McpToolSelect", "string", True,
                   None, "", "来自所选连接的 tools/list"),
            _field("inputs", "参数", "CodeParameterList", "array", False, [],
                   "按工具 inputSchema 绑定：引用上游变量或自定义值"),
            _field("timeout", "超时(ms)", "InputNumber", "number", False, None,
                   "", "留空取节点默认超时"),
            _field("outputVariable", "输出变量名", "Input", "string", False, "result"),
        ],
        default_config={"inputs": [], "outputVariable": "result"},
    ))
    return defs


# 模块级缓存（进程内只构建一次）
_NODE_DEFINITIONS: list[dict] | None = None


def get_node_definitions() -> list[dict]:
    global _NODE_DEFINITIONS
    if _NODE_DEFINITIONS is None:
        _NODE_DEFINITIONS = build_node_definitions()
    return _NODE_DEFINITIONS
