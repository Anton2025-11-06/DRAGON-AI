# -*- coding: utf-8 -*-
"""AI 节点：LLM / QUESTION_CLASSIFIER / PARAMETER_EXTRACTOR / AGENT。

模型调用统一走 WorkflowModelClient（模型广场兼容层）。
LLM 流式输出通过 runtime.emit('node.delta') 推送（对齐前端 StreamTokenEvent）。
"""
from __future__ import annotations

import json
from typing import Optional

from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.model_client import (
    ChatMessage, InvokeResult, WorkflowModelClient,
)
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
)


class LLMNodeExecutor(BaseNodeExecutor):
    """LLM 大模型节点（对照 MaxKB ai-chat-node）。

    - systemPrompt / promptTemplate 支持 {{nodeId.var}} 渲染
    - contextVariables：声明式变量注入（name → reference）
    - streaming：流式逐 token 发 node.delta；非流式一次 invoke
    - structuredOutput.enabled：response_format=json_schema 约束输出
    - visionEnabled：imageVariables 渲染为多模态 content 数组
    - memoryEnabled：从 START 输入的 messages 历史注入（一期：inputs.conversation）
    """

    node_type = "LLM"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        # 节点配置所选接口后缀（非直连时 base_url + suffix 拼接 URL）
        client.config.use_suffix = (cfg.get("suffix") or "").strip() or None

        messages = self._build_messages(ctx)
        kwargs = {}
        if cfg.get("temperature") is not None:
            kwargs["temperature"] = float(cfg["temperature"])
        if cfg.get("maxTokens"):
            kwargs["max_tokens"] = int(cfg["maxTokens"])

        so = cfg.get("structuredOutput") or {}
        if so.get("enabled") and so.get("jsonSchema"):
            schema = self._parse_schema(so["jsonSchema"])
            if schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema,
                                    "strict": bool(so.get("strictMode", False))},
                }

        output_var = cfg.get("outputVariable") or "output"
        streaming = bool(cfg.get("streaming", True))

        full_text, reasoning = "", ""
        usage: dict = {}
        if streaming:
            async for chunk in client.stream(messages, **kwargs):
                if chunk.content:
                    full_text += chunk.content
                    await self.emit_delta(chunk.content)
                if chunk.reasoning_content:
                    reasoning += chunk.reasoning_content
                if chunk.usage:
                    usage = chunk.usage
        else:
            result: InvokeResult = await client.invoke(messages, **kwargs)
            full_text, reasoning, usage = result.content, result.reasoning_content, result.usage

        if usage:
            self.runtime.bump_usage(usage.get("prompt_tokens", 0),
                                     usage.get("completion_tokens", 0))

        output = {output_var: full_text, "text": full_text}
        if reasoning:
            output["reasoning"] = reasoning
        if usage:
            output["usage"] = {"inputTokens": usage.get("prompt_tokens"),
                               "outputTokens": usage.get("completion_tokens")}
        # 结构化输出：尝试解析为 JSON 附加字段
        if so.get("enabled"):
            try:
                output["structured"] = json.loads(full_text)
            except (json.JSONDecodeError, TypeError):
                output["structured"] = None
        return NodeResult(output=output, stream_text=full_text)

    def _build_messages(self, ctx: ExecutionContext) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        system = self.cfg("systemPrompt")
        if system:
            messages.append(ChatMessage(role="system", content=ctx.render(system)))
        # 上下文变量注入 system（声明式引用）
        ctx_vars = self.cfg("contextVariables") or []
        if ctx_vars:
            lines = []
            for v in ctx_vars:
                value = ctx.resolve(str(v.get("reference") or "")) if v.get("reference") else None
                text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) \
                    else ("" if value is None else str(value))
                lines.append(f"{v.get('name')}: {text}")
            ctx_block = "以下是可用的上下文变量：\n" + "\n".join(lines)
            if messages and messages[0].role == "system":
                messages[0] = ChatMessage(role="system",
                                          content=messages[0].content + "\n\n" + ctx_block)
            else:
                messages.insert(0, ChatMessage(role="system", content=ctx_block))
        # 记忆（一期：inputs 携带 conversation 数组）
        if self.cfg("memoryEnabled"):
            history = ctx.inputs.get("conversation") or []
            for h in history[-int(self.cfg("memoryWindowSize") or 10):]:
                if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
                    messages.append(ChatMessage(role=h["role"], content=str(h.get("content", ""))))
        # 用户提示词
        prompt = self.cfg("promptTemplate") or ""
        content = ctx.render(prompt) if prompt else ""
        # Vision：多模态 content
        if self.cfg("visionEnabled") and self.cfg("imageVariables"):
            parts = [{"type": "text", "text": content or ""}]
            for ref in self.cfg("imageVariables"):
                url = ctx.resolve(str(ref or ""))
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": str(url)}})
            messages.append(ChatMessage(role="user", content=parts))
        else:
            messages.append(ChatMessage(role="user", content=content))
        return messages

    @staticmethod
    def _parse_schema(raw) -> Optional[dict]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("modelId"):
            issues.append(issue("LLM_NO_MODEL", "ERROR", "LLM 节点未选择模型", node))
        if not data.get("promptTemplate"):
            issues.append(issue("LLM_NO_PROMPT", "WARNING", "LLM 节点未填写提示词模板", node))
        # 非直连模型必须配置接口后缀（前端选中模型时写入 modelIsDirect=0）
        if data.get("modelIsDirect") == 0 and not str(data.get("suffix") or "").strip():
            issues.append(issue("LLM_NO_SUFFIX", "ERROR", "LLM 节点模型为非直连，必须选择接口后缀", node))
        return issues


class QuestionClassifierNodeExecutor(BaseNodeExecutor):
    """QUESTION_CLASSIFIER（对照 MaxKB intent-node）：LLM 分类 → branch:{categoryId} 路由。"""

    node_type = "QUESTION_CLASSIFIER"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        # 节点配置所选接口后缀（非直连时 base_url + suffix 拼接 URL）
        client.config.use_suffix = (cfg.get("suffix") or "").strip() or None
        categories = cfg.get("categories") or []
        if not categories:
            raise ValueError("问题分类器未定义分类类别")

        input_text = self._resolve_input(ctx)
        if cfg.get("advancedMode") and cfg.get("customPromptTemplate"):
            prompt = ctx.render(cfg["customPromptTemplate"])
        else:
            cat_lines = "\n".join(
                f"- {c.get('id')}: {c.get('name')} {c.get('description') or ''} "
                f"{'示例: ' + '; '.join(c.get('examples') or []) if c.get('examples') else ''}"
                for c in categories)
            instructions = cfg.get("instructions") or "将用户问题分类到最合适的类别。"
            prompt = (
                f"{instructions}\n\n类别列表（输出类别 ID，不要输出其他内容）：\n{cat_lines}\n\n"
                f"用户问题：{input_text}\n\n请只输出一个类别 ID。")

        result = await client.invoke(
            [ChatMessage(role="user", content=prompt)], temperature=0.0)
        self.runtime.bump_usage(result.usage.get("prompt_tokens", 0),
                                 result.usage.get("completion_tokens", 0))
        matched_id = self._match_category(result.content, categories)
        matched = next((c for c in categories if str(c.get("id")) == matched_id), None)
        if matched is None:
            raise ValueError(f"分类结果无法匹配任何类别: {result.content[:100]}")
        return NodeResult(
            output={"category": matched_id, "categoryName": matched.get("name"),
                    "input": input_text},
            branch_id=str(matched_id),
        )

    def _resolve_input(self, ctx: ExecutionContext) -> str:
        ref = self.cfg("inputVariable")
        if ref:
            v = ctx.resolve(str(ref))
            if v is None:
                raise ValueError(f"输入变量无法解析: {ref}")
            return str(v)
        # 兜底：START 输出里的第一个字符串字段
        start = self.graph_find_start_output(ctx)
        return start

    def graph_find_start_output(self, ctx: ExecutionContext) -> str:
        start = self.graph.find_start_node() if hasattr(self, "graph") else None
        if start is not None:
            out = ctx.get_node_output(start.id)
            for v in out.values():
                if isinstance(v, str) and v:
                    return v
        return ""

    @staticmethod
    def _match_category(text: str, categories: list) -> Optional[str]:
        import re
        text = (text or "").strip()
        # 直接 ID 匹配
        for c in categories:
            if str(c.get("id")) == text:
                return str(c.get("id"))
        # 从回复中提取 ID
        for c in categories:
            if str(c.get("id")) in text:
                return str(c.get("id"))
        # 名称匹配
        for c in categories:
            if c.get("name") and c["name"] in text:
                return str(c.get("id"))
        # 提取独立 token 尝试
        tokens = re.findall(r"[A-Za-z0-9_\-]+", text)
        for t in tokens:
            for c in categories:
                if str(c.get("id")) == t:
                    return t
        return None

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("modelId"):
            issues.append(issue("QC_NO_MODEL", "ERROR", "问题分类器未选择模型", node))
        if not data.get("categories"):
            issues.append(issue("QC_NO_CATEGORIES", "ERROR", "问题分类器未定义类别", node))
        # 非直连模型必须配置接口后缀
        if data.get("modelIsDirect") == 0 and not str(data.get("suffix") or "").strip():
            issues.append(issue("QC_NO_SUFFIX", "ERROR", "问题分类器模型为非直连，必须选择接口后缀", node))
        return issues


class ParameterExtractorNodeExecutor(BaseNodeExecutor):
    """PARAMETER_EXTRACTOR（对照 MaxKB parameter-extraction-node）：
    LLM + JSON schema 约束 → 从文本提取结构化参数。"""

    node_type = "PARAMETER_EXTRACTOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        # 节点配置所选接口后缀（非直连时 base_url + suffix 拼接 URL）
        client.config.use_suffix = (cfg.get("suffix") or "").strip() or None
        parameters = cfg.get("parameters") or []
        if not parameters:
            raise ValueError("参数提取器未定义提取参数")

        ref = cfg.get("inputVariable")
        input_text = str(ctx.resolve(str(ref)) or "") if ref else ""
        if not input_text:
            raise ValueError("参数提取器输入变量无法解析" + (f": {ref}" if ref else ""))

        properties = {}
        required = []
        for p in parameters:
            schema = {"type": p.get("type", "string")}
            if p.get("description"):
                schema["description"] = p["description"]
            if p.get("enumValues"):
                schema["enum"] = p["enumValues"]
            properties[p["name"]] = schema
            if p.get("required"):
                required.append(p["name"])

        instructions = cfg.get("instructions") or "从文本中提取结构化参数。"
        prompt = (
            f"{instructions}\n\n待提取文本：\n{input_text}\n\n"
            f"严格输出 JSON 对象，字段：{json.dumps(properties, ensure_ascii=False)}"
            + (f"，必填字段：{required}" if required else ""))

        messages = [ChatMessage(role="user", content=prompt)]
        result = await client.invoke(
            messages, temperature=0.0,
            response_format={"type": "json_object"},
        )
        self.runtime.bump_usage(result.usage.get("prompt_tokens", 0),
                                 result.usage.get("completion_tokens", 0))
        try:
            parsed = json.loads(result.content)
            if not isinstance(parsed, dict):
                raise ValueError("输出不是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"参数提取输出解析失败: {e}; 原文: {result.content[:200]}") from e

        # 类型修正 + 必填校验
        output = {}
        for p in parameters:
            name = p["name"]
            value = parsed.get(name)
            output[name] = self._fix_type(value, p.get("type", "string"))
            if p.get("required") and output[name] is None:
                raise ValueError(f"必填参数缺失: {name}")
        return NodeResult(output=output)

    @staticmethod
    def _fix_type(value, ptype: str):
        if value is None:
            return None
        try:
            if ptype == "number" and isinstance(value, str):
                return float(value) if "." in value else int(value)
            if ptype == "boolean" and isinstance(value, str):
                return value.lower() in ("1", "true", "yes")
        except ValueError:
            return value
        return value

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("modelId"):
            issues.append(issue("PE_NO_MODEL", "ERROR", "参数提取器未选择模型", node))
        if not data.get("parameters"):
            issues.append(issue("PE_NO_PARAMS", "ERROR", "参数提取器未定义参数", node))
        # 非直连模型必须配置接口后缀
        if data.get("modelIsDirect") == 0 and not str(data.get("suffix") or "").strip():
            issues.append(issue("PE_NO_SUFFIX", "ERROR", "参数提取器模型为非直连，必须选择接口后缀", node))
        return issues


class AgentNodeExecutor(BaseNodeExecutor):
    """AGENT 智能体节点：调用平台内已配置的智能体（chat-agents）。

    一期实现：通过 runtime.agent_invoker 钩子（service 层注入对模型会话服务的调用）；
    未注入钩子时报「智能体服务未接入」。
    """

    node_type = "AGENT"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        agent_id = self.cfg("agentId")
        if not agent_id:
            raise ValueError("智能体节点未配置 agentId")
        invoker = getattr(self.runtime, "agent_invoker", None)
        if not callable(invoker):
            raise ValueError("智能体服务未接入（agent_invoker 未注册）")
        # 输入：入边来源输出聚合（简化：第一个字符串值）
        input_text = self._first_input(ctx)
        result = await invoker(int(agent_id), input_text, ctx.inputs)
        output_var = self.cfg("outputVariable") or "output"
        return NodeResult(output={output_var: result})

    def _first_input(self, ctx: ExecutionContext) -> str:
        for e in self.graph.get_in_edges(self.node.id):
            out = ctx.get_node_output(e.source)
            for v in out.values():
                if isinstance(v, str) and v:
                    return v
        return ""

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        if not (node.data or {}).get("agentId"):
            issues.append(issue("AGENT_NO_ID", "ERROR", "智能体节点未选择智能体", node))
        return issues
