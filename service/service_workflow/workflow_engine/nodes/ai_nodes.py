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
    MT_IMAGE_TO_VIDEO, MT_IMAGE_UNDERSTAND, MT_MULTIMODAL_EMBEDDING, MT_OCR,
    MT_TEXT_EMBEDDING, MT_TEXT_RERANK, MT_TEXT_TO_AUDIO, MT_TEXT_TO_IMAGE,
    MT_TEXT_TO_TEXT, MT_TEXT_TO_VIDEO, MT_VIDEO_UNDERSTAND,
)
from common.common_model import entry as cm_entry
from common.common_model.base import ModelResult
from common.common_log.log_init import log
from service.service_workflow.workflow_engine import memory
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.model_client import (
    ChatMessage, WorkflowModelClient,
)
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeExecutionError, NodeResult, file_url, issue,
)


class LLMNodeExecutor(BaseNodeExecutor):
    """LLM 大模型节点：经 common_model 支持全部 12 种能力类型（非桥接，类型化直连）。

    - 依据所选模型登记的 category（12 类之一）+ provider，交给对应 common_model 实现；
    - 上游输入：文本(prompt/消息)、图片(imageVariable)、音频(audioVariable)、视频(videoVariable)、
      向量输入(inputVariable)、重排(queryVariable/documentsVariable) —— 均支持 {{节点.变量}} 引用；
    - 对话类可选 Vision（visionEnabled + imageVariables → user 消息拼多模态 content，
      每项按 _ref_list 解析：变量引用/文件数组/直接 URL）；
    - 下游输出：文本类产出 text、向量类产出 vectors/dimension、重排产出 scores、
      生成类产出 url/urls，供下游节点参数引用。
    - 模型调用参数（温度/max_tokens 等）在 12 类上都不占节点字段：全部来自模型管理
      「常用参数」（tb_model.model_params）+ 节点 params 覆盖，合并后由 common_model 注入；
    - 支持 stream 的三类（文生文/图片理解/视频理解）流式逐 token 发 node.delta；
    - 记忆（需求 1）：memoryEnabled 时执行前按范围/策略拼装历史（**不回写节点 input**），
      执行后把本轮 user/assistant 写回 node_states[nid].llmMessages 供下一轮使用。
    """

    node_type = "LLM"

    # 本轮记忆注入结果（每次 execute 重新赋值；先给类属性默认值，避免单测直接调子方法时报 AttributeError）
    _memory_user: Optional[str] = None
    _hist_msgs: list = []
    _hist_prefix: str = ""

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        category = client.config.category
        provider = client.config.provider
        output_var = cfg.get("outputVariable") or "output"
        # 模型能力位归一（模型管理登记的 supports_stream / supports_thinking）：
        # 能力未开启时，即使历史图数据里还残留这两个字段也不向厂商下发，
        # 与前端「未展示即未写入」保持同一口径。直接改写运行时 config 是因为
        # 引擎据此决定 node.completed 是否携带完整输出，若仅在执行器内降级，
        # 节点会「未 emit 过 delta 却被当作已流式」而丢输出。
        stream_capable_model = bool(getattr(client.config, "supports_stream", False))
        thinking_capable_model = bool(getattr(client.config, "supports_thinking", False))
        if cfg.get("streaming") and not stream_capable_model:
            log.info("[LLM] 节点「{}」配了流式但模型 {} 未开启 supports_stream，按非流式执行",
                     self.node.label, client.config.model_name)
            cfg["streaming"] = False
        if cfg.get("thinking") and not thinking_capable_model:
            log.info("[LLM] 节点「{}」配了思考但模型 {} 未开启 supports_thinking，不下发思考入参",
                     self.node.label, client.config.model_name)
            cfg["thinking"] = False

        # 动态发现校验：该 (类型, 供应商) 是否有 common_model 实现（不硬编码）
        if not cm_entry.supports(category, provider):
            raise NodeExecutionError(
                f"模型能力类型/供应商暂不支持调用：{category}/{provider}")
        # 节点级常用参数覆盖：合并进 model_params（经 self.extra 透传给全部 12 类实现）
        self._merge_node_params(client, cfg.get("params"))
        _, inst = client.acall(category)

        # 记忆注入（需求 1）：执行前算出「本轮要拼进去的历史」，执行后写回本轮。
        # 不支持记忆的类型（向量/重排/ASR/TTS）即使历史图数据里残留开关也直接忽略，
        # 静态校验只能拦住新保存的图，老图/直接调 API 提交的图还得靠这里兜底。
        self._memory_user = self._memory_user_text(ctx) if cfg.get("memoryEnabled") is True else None
        hist_msgs, hist_prefix, mem_warning = await memory.build_injection(
            self.runtime, self.node.id, cfg, category, self._compress_call)
        self._hist_msgs = hist_msgs
        self._hist_prefix = hist_prefix

        # 文生文走对话族（保留 system/上下文/结构化输出等完整能力）
        if category == MT_TEXT_TO_TEXT:
            result = await self._run_text_to_text(ctx, inst, output_var)
        else:
            # 其余 11 类：按类型翻译入参 → 类型化调用 → 映射产出
            result = await self._invoke_typed(ctx, inst, category, output_var, cfg)
        if mem_warning:
            result.output["memoryWarning"] = mem_warning
        self._record_memory(result.output)
        return result

    async def _invoke_typed(self, ctx, inst, category, output_var, cfg) -> NodeResult:
        kwargs = self._build_kwargs(ctx, category)
        streaming = (bool(cfg.get("streaming", False))
                     and category in MODEL_TYPES_STREAMABLE)
        if streaming:
            full, reasoning, usage = "", "", {}
            async for c in inst.astream(**kwargs):
                if c.content:
                    full += c.content
                    await self.emit_delta(c.content, reasoning=False)
                if c.reasoning_content:
                    reasoning += c.reasoning_content
                    await self.emit_delta(c.reasoning_content, reasoning=True)
                if c.usage:
                    usage = c.usage
            r = ModelResult(content=full, reasoning_content=reasoning, usage=usage)
        else:
            r = await inst.ainvoke(**kwargs)

        if r.usage:
            self.runtime.bump_usage(r.usage.get("prompt_tokens", 0),
                                    r.usage.get("completion_tokens", 0))
        return NodeResult(output=self._map_output(category, r, output_var))

    # ---------- 记忆（需求 1） ----------

    def _memory_user_text(self, ctx) -> str:
        """本轮记进记忆的 user 侧内容：渲染后的提示词。

        媒体入参（图片/音频/视频 URL）不入记忆——下一轮这些 URL 很可能已过期，
        存进去只会让历史里塞满无法复现的链接。
        """
        raw = self.cfg("promptTemplate")
        if isinstance(raw, (list, tuple)):
            raw = "\n".join(str(x) for x in raw)
        return (ctx.render(str(raw)) if raw else "").strip()

    def _record_memory(self, output: dict) -> None:
        """把本轮问答写回 node_states；未开记忆（`_memory_user` 为 None）直接跳过。

        节点失败时走不到这里（execute 抛异常），因此不会在历史里留下「助手答了个空」
        这种会把后续轮带偏的记录。
        """
        if self._memory_user is None:
            return
        assistant = ""
        for key in ("text", "url"):
            value = (output or {}).get(key)
            if isinstance(value, str) and value:
                assistant = value
                break
        else:
            urls = (output or {}).get("urls")
            if isinstance(urls, (list, tuple)) and urls:
                assistant = str(urls[0])
        memory.append_round(self.runtime.node_states.get(self.node.id),
                            self.runtime.round, self._memory_user, assistant,
                            # 循环体/迭代体/并行分支内的节点每轮覆盖：一个 LOOP 跑 3 轮就把
                            # limit=10 的额度用掉 6 条同一轮的历史，真正跨轮的信息反而被顶掉
                            overwrite=memory.is_compound_body(self.graph, self.node.id))

    async def _compress_call(self, prompt: str) -> str:
        """COMPRESS 策略的摘要调用：用节点指定的压缩模型（必须是文生文）。

        模型不存在/已停用/调用失败都会抛出，由 memory.build_injection 统一降级为
        DROP_OLDEST —— 记忆是增强项，不能因为摘要挂掉把业务节点弄失败。
        """
        model_id = self.cfg("memoryCompressModelId")
        if not model_id:
            raise ValueError("未配置记忆压缩模型")
        client = await WorkflowModelClient.create(
            int(model_id), provider=self.runtime.model_provider,
            http=self.runtime.http_client)
        r = await client.chat(prompt=prompt, temperature=0.0)
        # 压缩消耗计入本执行（用户口径），否则 llm_call_count 会少算一次真实调用
        self.runtime.bump_usage(r.usage.get("prompt_tokens", 0),
                                r.usage.get("completion_tokens", 0))
        return r.content

    @staticmethod
    def _merge_node_params(client, params) -> None:
        """节点级常用参数合并到 client.config.model_params（值已由前端按类型转换，后端不二次转型）。

        兼容两种入参形态：`[{name,type,value}]`（前端表格）或 `{name:value}`（字典）。
        """
        if not params:
            return
        node_params: dict = {}
        if isinstance(params, dict):
            node_params = {k: v for k, v in params.items()
                           if k and v is not None and v != ""}
        elif isinstance(params, (list, tuple)):
            for row in params:
                if not isinstance(row, dict):
                    continue
                name = (row.get("name") or "").strip() if isinstance(row.get("name"), str) else row.get("name")
                if not name:
                    continue
                value = row.get("value")
                # 行未填值：视为「不覆盖」，不能拿空值把模型登记的常用参数打成 null
                if value is None or value == "":
                    continue
                node_params[name] = value
        if not node_params:
            return
        client.config.model_params = {**(client.config.model_params or {}), **node_params}

    # ---------- 文生文（对话族，功能最全） ----------

    async def _run_text_to_text(self, ctx, inst, output_var) -> NodeResult:
        """对话族执行；流式/思考入参已经过能力位归一（见 execute）。"""
        cfg = self.config
        messages = [m.to_openai() for m in self._build_messages(ctx)]
        kwargs: dict = {"messages": messages}
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
        streaming = bool(cfg.get("streaming", False))
        full_text, reasoning, usage = "", "", {}
        if streaming:
            async for chunk in inst.astream(**kwargs):
                if chunk.content:
                    full_text += chunk.content
                    await self.emit_delta(chunk.content, reasoning=False)
                if chunk.reasoning_content:
                    reasoning += chunk.reasoning_content
                    await self.emit_delta(chunk.reasoning_content, reasoning=True)
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
        return NodeResult(output=output)

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
        """列表解析：引用/字面量归一为字符串数组（图片 URL 列表用）。

        元素为开始节点文件参数（上传返回对象）时取其 url，与文档提取器口径一致。
        """
        v = self._ref(ctx, ref)
        if v is None:
            return []
        items = v if isinstance(v, (list, tuple)) else [v]
        return [u for u in (file_url(x) for x in items) if u]

    def _ref_one(self, ctx, ref):
        """单值媒体引用：若解析为列表（如上游 urls）取首个，文件对象取其 url。"""
        return file_url(self._ref(ctx, ref))

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
        elif category == MT_MULTIMODAL_EMBEDDING:
            txt = self._ref(ctx, cfg.get("inputVariable")) or self._text(ctx, "promptTemplate")
            if txt:
                kw["text"] = txt
            imgs = self._ref_list(ctx, cfg.get("imageVariable"))
            if imgs:
                kw["image_urls"] = imgs
            vid = self._ref_one(ctx, cfg.get("videoVariable"))
            if vid:
                kw["video_url"] = vid
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
        # 不再从节点 config 透传 temperature/maxTokens（含理解族）：
        # 调用参数统一走 model_params（模型管理常用参数 + 节点 params）
        # 记忆注入（非对话族形态）：历史压成前缀拼到提示词前面。
        # 只拼已有 prompt 的情况：OCR/图生视频的提示词是可选项，原本为空时硬塞一段
        # 对话记录反而会让厂商拿到一份没预期过的输入（图生视频会把它当画面描述）。
        if self._hist_prefix and kw.get("prompt"):
            kw["prompt"] = memory.render_prompt(self._hist_prefix, kw["prompt"])
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
        elif category in (MT_TEXT_EMBEDDING, MT_IMAGE_EMBEDDING, MT_MULTIMODAL_EMBEDDING):
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
        # 记忆（对话族形态）：历史整段插在 system 之后、本轮 user 之前。
        # 插而不是拼到 user content 里：多轮结构是对话模型的原生训练形态，拼成一堆
        # 文本反而会让它把历史当成「本轮要回答的内容」的一部分。
        if self._hist_msgs:
            pos = 0
            while pos < len(messages) and messages[pos].role == "system":
                pos += 1
            messages[pos:pos] = self._hist_msgs
        # 用户提示词
        prompt = self.cfg("promptTemplate") or ""
        content = ctx.render(prompt) if prompt else ""
        # Vision：多模态 content
        if self.cfg("visionEnabled") and self.cfg("imageVariables"):
            parts = [{"type": "text", "text": content or ""}]
            refs = self.cfg("imageVariables")
            if isinstance(refs, str):
                refs = [refs]
            for ref in refs:
                # 每张图可来自：文件变量（含 FILE_LIST 数组）、上游 urls 数组、直接贴的 URL
                for url in self._ref_list(ctx, ref):
                    parts.append({"type": "image_url", "image_url": {"url": url}})
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
    LLM + JSON schema 约束 → 从文本提取结构化参数。

    除各提取参数外，输出固定附带两个内置状态变量（与前端表单说明、变量选择器口径一致）：
    __is_success（是否提取成功）、__reason（失败原因，成功时为空串）。
    提取失败不抛异常中断流程，交给下游用这两个变量分支处理；
    固定走 prompt 方式（response_format=json_object），不提供 function-call 通道。"""

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
        # 解析/校验失败只置失败原因，不让节点报错（契约：__is_success + __reason）
        try:
            parsed = json.loads(result.content or "")
            if not isinstance(parsed, dict):
                raise ValueError("输出不是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            parsed = {}
            reason = f"输出解析失败: {e}; 原文: {(result.content or '')[:200]}"
        else:
            reason = ""

        # 类型修正 + 必填校验（缺必填同样只记原因）
        output = {}
        missing = []
        for p in parameters:
            name = p["name"]
            value = parsed.get(name)
            output[name] = self._fix_type(value, p.get("type", "string"))
            if p.get("required") and output[name] is None:
                missing.append(name)
        if missing:
            reason = ((reason + "; ") if reason else "") + \
                     "必填参数缺失: " + ", ".join(missing)
        output["__is_success"] = not reason
        output["__reason"] = reason
        if reason:
            log.info("[PARAMETER_EXTRACTOR] 节点「{}」提取未成功: {}",
                     self.node.label, reason)
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
