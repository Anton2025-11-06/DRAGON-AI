# -*- coding: utf-8 -*-
"""AI 节点：LLM / QUESTION_CLASSIFIER / PARAMETER_EXTRACTOR / AGENT。

模型调用统一走 WorkflowModelClient（模型广场兼容层）。
LLM 流式输出通过 runtime.emit('node.delta') 推送（对齐前端 StreamTokenEvent）。
"""
from __future__ import annotations

import json
from typing import Optional

from common.common_constants.model_constant import (
    MODEL_TYPES_STREAMABLE, MT_AUDIO_TO_TEXT, MT_IMAGE_EMBEDDING,
    MT_IMAGE_TO_VIDEO, MT_IMAGE_UNDERSTAND, MT_OCR, MT_TEXT_EMBEDDING,
    MT_TEXT_RERANK, MT_TEXT_TO_AUDIO, MT_TEXT_TO_IMAGE, MT_TEXT_TO_TEXT,
    MT_TEXT_TO_VIDEO, MT_VIDEO_UNDERSTAND,
)
from common.common_model import entry as cm_entry
from common.common_model.base import ModelResult
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.model_client import (
    ChatMessage, WorkflowModelClient,
)
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeExecutionError, NodeResult, issue,
)


class LLMNodeExecutor(BaseNodeExecutor):
    """LLM 大模型节点：经 common_model 支持全部 12 种能力类型（非桥接，类型化直连）。

    - 依据所选模型登记的 category（12 类之一）+ provider，交给对应 common_model 实现；
    - 上游输入：文本(prompt/消息)、图片(imageVariable)、音频(audioVariable)、视频(videoVariable)、
      向量输入(inputVariable)、重排(queryVariable/documentsVariable) —— 均支持 {{节点.变量}} 引用；
    - 下游输出：文本类产出 text、向量类产出 vectors/dimension、重排产出 scores、
      生成类产出 url/urls，供下游节点参数引用。
    - 支持 stream 的三类（文生文/图片理解/视频理解）流式逐 token 发 node.delta。
    """

    node_type = "LLM"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        category = client.config.category
        provider = client.config.provider
        output_var = cfg.get("outputVariable") or "output"

        # 动态发现校验：该 (类型, 供应商) 是否有 common_model 实现（不硬编码）
        if not cm_entry.supports(category, provider):
            raise NodeExecutionError(
                f"模型能力类型/供应商暂不支持调用：{category}/{provider}")
        _, inst = client.acall(category)

        # 文生文走对话族（保留 system/记忆/上下文/结构化输出等完整能力）
        if category == MT_TEXT_TO_TEXT:
            return await self._run_text_to_text(ctx, inst, output_var)

        # 其余 11 类：按类型翻译入参 → 类型化调用 → 映射产出
        kwargs = self._build_kwargs(ctx, category)
        streaming = bool(cfg.get("streaming", True)) and category in MODEL_TYPES_STREAMABLE
        if streaming:
            full, reasoning, usage = "", "", {}
            async for c in inst.astream(**kwargs):
                if c.content:
                    full += c.content
                    await self.emit_delta(c.content)
                if c.reasoning_content:
                    reasoning += c.reasoning_content
                if c.usage:
                    usage = c.usage
            r = ModelResult(content=full, reasoning_content=reasoning, usage=usage)
        else:
            r = await inst.ainvoke(**kwargs)

        if r.usage:
            self.runtime.bump_usage(r.usage.get("prompt_tokens", 0),
                                    r.usage.get("completion_tokens", 0))
        output = self._map_output(category, r, output_var)
        stream_text = r.content if category in (
            MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR, MT_AUDIO_TO_TEXT) else None
        return NodeResult(output=output, stream_text=stream_text)

    # ---------- 文生文（对话族，功能最全） ----------

    async def _run_text_to_text(self, ctx, inst, output_var) -> NodeResult:
        cfg = self.config
        messages = [m.to_openai() for m in self._build_messages(ctx)]
        kwargs: dict = {"messages": messages}
        if cfg.get("temperature") is not None:
            kwargs["temperature"] = float(cfg["temperature"])
        if cfg.get("maxTokens"):
            kwargs["max_tokens"] = int(cfg["maxTokens"])
        if cfg.get("thinking"):
            kwargs["thinking"] = True
        so = cfg.get("structuredOutput") or {}
        if so.get("enabled") and so.get("jsonSchema"):
            schema = self._parse_schema(so["jsonSchema"])
            if schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema,
                                    "strict": bool(so.get("strictMode", False))},
                }
        streaming = bool(cfg.get("streaming", True))
        full_text, reasoning, usage = "", "", {}
        if streaming:
            async for chunk in inst.astream(**kwargs):
                if chunk.content:
                    full_text += chunk.content
                    await self.emit_delta(chunk.content)
                if chunk.reasoning_content:
                    reasoning += chunk.reasoning_content
                if chunk.usage:
                    usage = chunk.usage
        else:
            r = await inst.ainvoke(**kwargs)
            full_text, reasoning, usage = r.content, r.reasoning_content, r.usage
        if usage:
            self.runtime.bump_usage(usage.get("prompt_tokens", 0),
                                    usage.get("completion_tokens", 0))
        output = {output_var: full_text, "text": full_text}
        if reasoning:
            output["reasoning"] = reasoning
        if usage:
            output["usage"] = {"inputTokens": usage.get("prompt_tokens"),
                               "outputTokens": usage.get("completion_tokens")}
        if so.get("enabled"):
            try:
                output["structured"] = json.loads(full_text)
            except (json.JSONDecodeError, TypeError):
                output["structured"] = None
        return NodeResult(output=output, stream_text=full_text)

    # ---------- 按类型翻译入参（解析上游变量引用，兼容直接贴 URL/文本） ----------

    def _ref(self, ctx, ref):
        """单值解析：先按变量引用解析；解析不到则当作字面量（URL/文本）。"""
        if ref is None:
            return None
        if not isinstance(ref, str):
            return ref
        s = ref.strip()
        if not s:
            return None
        val = ctx.resolve(s)
        return val if val is not None else s

    def _ref_list(self, ctx, ref) -> list:
        """列表解析：引用/字面量归一为字符串数组（图片 URL 列表用）。"""
        v = self._ref(ctx, ref)
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v if x]
        return [str(v)]

    def _ref_one(self, ctx, ref):
        """单值媒体引用：若解析为列表（如上游 urls）取首个，归一为字符串 URL。"""
        v = self._ref(ctx, ref)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        return str(v) if v is not None else None

    def _text(self, ctx, key) -> str:
        """取渲染后的提示词模板（promptTemplate）。"""
        tpl = self.cfg(key) or ""
        return ctx.render(tpl) if tpl else ""

    def _build_kwargs(self, ctx, category) -> dict:
        cfg = self.config
        kw: dict = {}
        if category == MT_IMAGE_UNDERSTAND:
            kw["prompt"] = self._text(ctx, "promptTemplate")
            kw["image_urls"] = self._ref_list(ctx, cfg.get("imageVariable") or cfg.get("imageVariables"))
        elif category == MT_OCR:
            kw["image_urls"] = self._ref_list(ctx, cfg.get("imageVariable") or cfg.get("imageVariables"))
            p = self._text(ctx, "promptTemplate")
            if p:
                kw["prompt"] = p
        elif category == MT_VIDEO_UNDERSTAND:
            kw["prompt"] = self._text(ctx, "promptTemplate")
            kw["video_url"] = self._ref_one(ctx, cfg.get("videoVariable"))
        elif category == MT_TEXT_EMBEDDING:
            kw["input"] = self._ref(ctx, cfg.get("inputVariable")) or self._text(ctx, "promptTemplate")
        elif category == MT_IMAGE_EMBEDDING:
            kw["image_urls"] = self._ref_list(ctx, cfg.get("imageVariable"))
        elif category == MT_TEXT_RERANK:
            kw["query"] = self._ref(ctx, cfg.get("queryVariable"))
            kw["documents"] = self._ref_list(ctx, cfg.get("documentsVariable"))
            if cfg.get("topN"):
                kw["top_n"] = int(cfg["topN"])
        elif category == MT_TEXT_TO_IMAGE:
            kw["prompt"] = self._text(ctx, "promptTemplate")
            if cfg.get("size"):
                kw["size"] = cfg.get("size")
            if cfg.get("imageN"):
                kw["n"] = int(cfg.get("imageN"))
        elif category == MT_TEXT_TO_VIDEO:
            kw["prompt"] = self._text(ctx, "promptTemplate")
            if cfg.get("size"):
                kw["size"] = cfg.get("size")
        elif category == MT_IMAGE_TO_VIDEO:
            kw["image_url"] = self._ref_one(ctx, cfg.get("imageVariable"))
            p = self._text(ctx, "promptTemplate")
            if p:
                kw["prompt"] = p
            if cfg.get("size"):
                kw["size"] = cfg.get("size")
        elif category == MT_TEXT_TO_AUDIO:
            kw["text"] = self._text(ctx, "promptTemplate") or \
                ("" if self._ref(ctx, cfg.get("inputVariable")) is None
                 else str(self._ref(ctx, cfg.get("inputVariable"))))
            if cfg.get("voice"):
                kw["voice"] = cfg.get("voice")
        elif category == MT_AUDIO_TO_TEXT:
            kw["audio_url"] = self._ref_one(ctx, cfg.get("audioVariable"))
        if category in MODEL_TYPES_STREAMABLE and cfg.get("thinking"):
            kw["thinking"] = True
        # 生成/理解类透传采样参数（仅对话族有意义）
        if category in (MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR):
            if cfg.get("temperature") is not None:
                kw["temperature"] = float(cfg["temperature"])
            if cfg.get("maxTokens"):
                kw["max_tokens"] = int(cfg["maxTokens"])
        # 剔除 None/空列表，避免覆盖 common_model 内部默认
        return {k: v for k, v in kw.items()
                if v is not None and not (isinstance(v, (list, str)) and len(v) == 0)}

    # ---------- 产出映射为下游可引用变量 ----------

    def _map_output(self, category, r: ModelResult, output_var) -> dict:
        out: dict = {}
        if category in (MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR, MT_AUDIO_TO_TEXT):
            out[output_var] = r.content
            out["text"] = r.content
            if r.reasoning_content:
                out["reasoning"] = r.reasoning_content
        elif category in (MT_TEXT_EMBEDDING, MT_IMAGE_EMBEDDING):
            vectors = r.vectors or []
            out[output_var] = vectors
            out["vectors"] = vectors
            out["count"] = len(vectors)
            out["dimension"] = len(vectors[0]) if vectors else 0
        elif category == MT_TEXT_RERANK:
            out[output_var] = r.scores or []
            out["scores"] = r.scores or []
        elif category == MT_TEXT_TO_IMAGE:
            urls = r.urls or ([r.url] if r.url else [])
            out[output_var] = urls
            out["urls"] = urls
            out["url"] = urls[0] if urls else None
        elif category in (MT_TEXT_TO_VIDEO, MT_IMAGE_TO_VIDEO):
            out[output_var] = r.url
            out["url"] = r.url
            out["video_url"] = r.url
        elif category == MT_TEXT_TO_AUDIO:
            # 不同厂商 TTS 返回形态不同：通义 qwen-tts 返回远程 url，智谱 glm-tts 直接返回音频字节流。
            # 无 url 但有二进制时，编码为 base64 data URI，保证下游/前端拿到可直接播放的 audio 源。
            audio_url = r.url
            if not audio_url and r.audio_bytes:
                import base64
                fmt = (r.raw or {}).get("format") or "wav"
                audio_url = "data:audio/{};base64,{}".format(
                    fmt, base64.b64encode(r.audio_bytes).decode())
            out[output_var] = audio_url
            out["url"] = audio_url
            out["audio_url"] = audio_url
        if r.usage:
            out["usage"] = {"inputTokens": r.usage.get("prompt_tokens"),
                            "outputTokens": r.usage.get("completion_tokens")}
        return out

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
        if not data.get("promptTemplate") and not any(
                data.get(k) for k in
                ("inputVariable", "imageVariable", "audioVariable",
                 "videoVariable", "queryVariable", "documentsVariable")):
            issues.append(issue("LLM_NO_PROMPT", "WARNING", "LLM 节点未填写输入（提示词/媒体变量）", node))
        return issues


class QuestionClassifierNodeExecutor(BaseNodeExecutor):
    """QUESTION_CLASSIFIER（对照 MaxKB intent-node）：LLM 分类 → branch:{categoryId} 路由。"""

    node_type = "QUESTION_CLASSIFIER"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
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

        result = await client.chat(prompt=prompt, temperature=0.0)
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

        result = await client.chat(
            prompt=prompt, temperature=0.0,
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
