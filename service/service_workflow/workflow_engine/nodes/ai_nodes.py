# -*- coding: utf-8 -*-
"""AI 节点：LLM / QUESTION_CLASSIFIER / PARAMETER_EXTRACTOR。

模型调用统一走 WorkflowModelClient（模型广场兼容层）。
LLM 流式输出通过 runtime.emit('node.delta') 推送（对齐前端 StreamTokenEvent）。
LLM 另承载「插入工具」的 tool-call 循环（MCP 连接 / 工具库函数 / 子工作流），见
LLMNodeExecutor._run_with_tools。
"""
from __future__ import annotations

import json
import re
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
from service.service_workflow.workflow_engine import memory, py_sandbox
from service.service_workflow.workflow_engine.context import ExecutionContext
from service.service_workflow.workflow_engine.model_client import (
    ChatMessage, WorkflowModelClient,
)
from service.service_workflow.workflow_engine.nodes.base import (
    APPROVAL_SCOPE_DOWNSTREAM, AwaitingApproval, BaseNodeExecutor,
    NodeExecutionError, NodeResult, file_url, issue,
)
# 子工作流调用核与【工作流】节点共用一份实现（本包内模块，无 service 依赖，不会成环）
from service.service_workflow.workflow_engine.nodes.subworkflow_nodes import (
    build_awaiting_context, child_output_payload, invoke_child_workflow,
    resolve_version,
)

# LLM 节点可插入的三类工具来源（与前端 LlmToolBinding.kind 逐字对齐）
TOOL_KIND_FUNCTION = "TOOL"
TOOL_KIND_MCP = "MCP"
TOOL_KIND_WORKFLOW = "WORKFLOW"

# 工具参数/开始节点入参的类型名 → JSON Schema type。
# 表里没有的一律按 string 处理：那是模型最宽容、不会因为格式不合被厂商拒收的一种。
# 键全部小写（COMMON 类型名与前端 InputFieldType 同名时自动合并）。
_JSON_TYPE_MAP = {
    "string": "string", "text": "string", "short_text": "string",
    "paragraph": "string", "select": "string", "single_file": "string",
    "file": "string", "url": "string",
    "number": "number", "integer": "number", "int": "number", "float": "number",
    "boolean": "boolean", "bool": "boolean", "checkbox": "boolean",
    "array": "array", "list": "array", "file_list": "array",
    # START 的审批人字段本来就是「审批人标识数组」（元素可数字可字符串）
    "approver": "array",
    "object": "object", "json": "object",
}


def _json_type(raw) -> str:
    """节点/工具/厂商侧类型名 → JSON Schema type。"""
    return _JSON_TYPE_MAP.get(str(raw or "string").strip().lower(), "string")


def _coerce_type(json_type: str) -> str:
    """JSON Schema type → py_sandbox.coerce_constant 认识的类型名。"""
    return json_type if json_type in ("number", "boolean", "array", "object") else "string"


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _truncate(text: str, limit: int) -> str:
    """回填给模型的文本截断：一次 MCP 调用可能返回整页 HTML，原样塞回去下一轮就爆上下文。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断，原文共 {len(text)} 字）"


def _parse_tool_args(raw) -> tuple[dict, Optional[str]]:
    """模型给的 arguments → (参数 dict, 错误说明)。

    arguments 按 OpenAI 规范是 JSON 字符串，个别模型直接给 dict，两种都接受。
    解析不了不能当成「无参数」硬调：那会让工具拿着空参数跑出一版错答案，
    把错误原文回填给模型至少还有机会自己改。
    """
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    try:
        data = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}, f"工具参数不是合法 JSON: {str(raw)[:200]}"
    if not isinstance(data, dict):
        return {}, "工具参数不是 JSON 对象"
    return data, None


def _object_schema(properties: dict, required: list) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _normalize_json_schema(raw) -> tuple[dict, dict]:
    """厂商给的 JSON Schema（MCP inputSchema）→ (可用的 object schema, 参数类型表)。

    只做最小修补（补 type/properties、把 required 里的野字段剔干净），
    余下关键字（$defs/additionalProperties…）原样保留：它们对模型理解参数有用，
    而且剔了反而可能把带 $ref 的 schema 变成无法解析的断链。
    """
    schema = dict(raw) if isinstance(raw, dict) else {}
    if "type" not in schema:
        # 没写 type 但 properties 都齐全是 MCP 工具入参里的常见写法：补上即可，
        # 不能当成「不是 object 型」把参数定义整块清掉
        schema["type"] = "object"
    elif schema.get("type") != "object":
        # 非 object 型没法当函数参数：退化成空对象，只留下对模型理解全局有用的关键字
        schema = {k: v for k, v in schema.items()
                  if k in ("$defs", "definitions", "additionalProperties")}
        schema["type"] = "object"
    props = schema.get("properties")
    if not isinstance(props, dict):
        props = {}
    schema["properties"] = props
    types = {name: _coerce_type(_json_type((prop or {}).get("type")))
             for name, prop in props.items()}
    required = [r for r in (schema.get("required") or []) if r in props]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)
    return schema, types


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

    # tool-call 轮次上限（与「子工作流调用深度」无关）：只防模型反复调工具把 token 烧穿，
    # 超限直接节点失败，而不是默默把后面的调用丢掉。8 轮足够覆盖「查三次再综合」的
    # 正常用法，再往上基本都是模型在原地打转
    MAX_TOOL_ROUNDS = 100
    # 回填给模型的工具结果文本上限（事件里的摘要另算一个更小的口径）
    TOOL_RESULT_MAX_CHARS = 4000
    # node.tool_result 事件里带的结果摘要长度（SSE 帧不推大体积，完整结果看节点输出）
    TOOL_EVENT_SUMMARY_CHARS = 500

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
        # 工具的两道前置闸门：只在文生文、且模型开了 supports_function_call 时生效。
        # 先判类型再判能力位：向量/重排那 11 类没有 function-call 通道，能力位也无从谈起。
        # 未登记工具调用能力时必须剔掉 cfg["tools"]（覆盖历史图），否则只会拿到一个
        # 「厂商拒收 tools 参数」的报错，而不是用户能看懂的「这个模型不支持工具」。
        if cfg.get("tools") and category != MT_TEXT_TO_TEXT:
            log.info("[LLM] 节点「{}」配了工具但模型能力类型 {} 不支持调用工具，本次忽略",
                     self.node.label, category)
            cfg["tools"] = []
        if cfg.get("tools") and not bool(getattr(client.config, "supports_function_call", False)):
            log.info("[LLM] 节点「{}」配了工具但模型 {} 未开启 supports_function_call，本次调用忽略工具",
                     self.node.label, client.config.model_name)
            cfg["tools"] = []
        # 带工具时不再改写 streaming：流式与非流式都支持，选哪条由节点开关决定
        # （tool_calls 的分片累加在 ChatMLMixin._chat_stream 里完成，见 _call_round）。

        # 动态发现校验：该 (类型, 供应商) 是否有 common_model 实现（不硬编码）
        if not cm_entry.supports(category, provider):
            raise NodeExecutionError(
                f"模型能力类型/供应商暂不支持调用：{category}/{provider}")
        # 节点级常用参数覆盖：合并进 model_params（经 self.extra 透传给全部 12 类实现）
        self._merge_node_params(client, cfg.get("params"))
        _, inst = client.acall(category)

        # 记忆注入：执行前算出「本轮要拼进去的历史」，执行后写回本轮。
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
        """对话族执行；流式/思考/工具入参已经过能力位归一（见 execute）。"""
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
        if cfg.get("tools"):
            # 带工具：走 tool-call 循环，每轮是流式还是非流式仍由 streaming 开关决定
            return await self._run_with_tools(ctx, inst, output_var, kwargs, so)
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
        """单值解析：先按变量引用解析；解析不到则当作字面量（URL/文本）。

        引用类字段在画布上是 VariableInput（可插入多个引用），多引用按模板拼成文本。
        """
        if ref is None:
            return None
        if not isinstance(ref, str):
            return ref
        s = ref.strip()
        if not s:
            return None
        val = ctx.resolve_ref(s)
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
                value = ctx.resolve_ref(v.get("reference")) if v.get("reference") else None
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

    # ---------- 插入工具：装配（需求 6/7：参数不在画布上配，执行时才现读定义） ----------

    async def _build_tool_specs(self, ctx) -> tuple[dict, list]:
        """按 cfg["tools"] 逐条装配 OpenAI function schema。

        返回 ({对外工具名: spec}, 装配失败说明列表)。一条来源装配失败不拖垮整个节点
        （某个 MCP 连接此刻不通，不该让已就绪的工具也用不了）；一条都没装配成功时
        由调用方判失败 —— 否则会退化成「配了工具却什么都不调」的普通问答，看不出来。
        """
        from service.service_workflow.services.tool_service import ToolService

        specs: dict = {}
        errors: list = []
        for binding in self.cfg("tools") or []:
            if not isinstance(binding, dict):
                continue
            kind = str(binding.get("kind") or "").upper()
            raw_id = binding.get("id")
            if not kind or raw_id in (None, ""):
                errors.append("有一条未选完整的工具绑定已忽略")
                continue
            try:
                if kind == TOOL_KIND_FUNCTION:
                    await self._assemble_tool_function(specs, ToolService, int(raw_id))
                elif kind == TOOL_KIND_MCP:
                    await self._assemble_tool_mcp(specs, int(raw_id))
                elif kind == TOOL_KIND_WORKFLOW:
                    await self._assemble_tool_workflow(specs, int(raw_id), binding)
                else:
                    errors.append(f"未知的工具来源类型 {kind}")
            except Exception as e:  # noqa: BLE001  单个来源装配失败只记一条，其余工具照常可用
                log.warning("[LLM] 节点「{}」工具装配失败 kind={} id={}: {}",
                            self.node.label, kind, raw_id, e)
                errors.append(f"{kind}#{raw_id} 装配失败: {e}")
        return specs, errors

    async def _assemble_tool_function(self, specs: dict, tool_service, tool_id: int) -> None:
        """TOOL：读工具登记的 parameters_schema 当参数定义（与工具节点同一份解析口径）。"""
        tool = await tool_service.load_for_node(tool_id, None)
        properties: dict = {}
        required: list = []
        types: dict = {}
        for p in tool_service.parse_parameters(tool.get("parameters_schema")):
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            jt = _json_type(p.get("type"))
            types[name] = _coerce_type(jt)
            prop: dict = {"type": jt}
            if p.get("description"):
                prop["description"] = str(p["description"])
            properties[name] = prop
            if p.get("required"):
                required.append(name)
        self._add_spec(
            specs, kind=TOOL_KIND_FUNCTION, real_name=str(tool.get("name") or ""),
            description=str(tool.get("description") or ""),
            parameters=_object_schema(properties, required), param_types=types,
            meta={"ref_id": tool_id, "tool": tool})

    async def _assemble_tool_mcp(self, specs: dict, server_id: int) -> None:
        """MCP 插入的粒度是「连接」：运行时向该连接 tools/list 取全部工具。"""
        from service.service_workflow.services.mcp_service import McpServerService

        row = await McpServerService.get_by_id(server_id)
        # 不存在的与已停用的在这里就拦掉（与 ToolService.load_for_node 同一口径）：
        # 装配失败只记一条 toolErrors，其余工具照常可用，比等模型真调用时才报错早一轮
        McpServerService.ensure_usable(row)
        items = await McpServerService.list_tools(row, server_id)
        if not items:
            raise ValueError("该 MCP 连接没有提供工具")
        for item in items:
            real = str(item.get("name") or "").strip()
            if not real:
                continue
            parameters, types = _normalize_json_schema(item.get("inputSchema"))
            self._add_spec(
                specs, kind=TOOL_KIND_MCP, real_name=real,
                description=str(item.get("description") or ""),
                parameters=parameters, param_types=types,
                meta={"ref_id": server_id, "server_id": server_id})

    async def _assemble_tool_workflow(self, specs: dict, workflow_id: int,
                                      binding: dict) -> None:
        """WORKFLOW：入参 = 子工作流开始节点的输入字段（现读发布快照，不手配）。"""
        from service.service_workflow.services.workflow_service import WorkflowService
        from service.service_workflow.workflow_engine.graph import WorkflowGraph

        if not binding.get("apiKeyId"):
            raise ValueError("未选择执行用 API Key")
        detail = await WorkflowService.detail(workflow_id)
        if detail is None:
            raise ValueError("子工作流不存在（已被删除？）")
        wf_name = str(detail.get("name") or f"workflow_{workflow_id}")
        version = await resolve_version(workflow_id, binding, wf_name)
        snapshot = await WorkflowService.get_snapshot(workflow_id, version)
        if not snapshot:
            raise ValueError(f"子工作流 v{version} 的发布快照不存在，请先发布")
        start = WorkflowGraph(snapshot).find_start_node()
        properties: dict = {}
        required: list = []
        types: dict = {}
        for f in ((start.data or {}).get("fields") if start else None) or []:
            name = str(f.get("name") or "").strip()
            if not name:
                continue
            jt = _json_type(f.get("type"))
            types[name] = _coerce_type(jt)
            prop: dict = {"type": jt}
            label = f.get("label")
            description = f.get("description")
            desc = (str(label) if label else "") + (str(description) if description else "")
            if f.get("defaultValue") is not None:
                # 不填不等于不传：子工作流自己的 START 节点会用这个默认值兜底，
                # 说清楚免得模型以为必须自己凑一个
                desc = f"{desc}（不填时子工作流用自己的默认值 {f['defaultValue']}）".strip()
            if desc:
                prop["description"] = desc
            properties[name] = prop
            if f.get("required"):
                required.append(name)
        # 解析出的版本号回写进绑定：SPECIFIC 模式下即使本轮与下轮之间又发了新版，
        # 同一个节点在一次执行里看到的仍是同一份图
        merged = {**binding, "workflowName": wf_name}
        if str(binding.get("versionMode") or "LATEST").upper() == "SPECIFIC":
            merged["version"] = version
        self._add_spec(
            specs, kind=TOOL_KIND_WORKFLOW, real_name=wf_name,
            description=f"调用子工作流「{wf_name}」并返回它的执行结果",
            parameters=_object_schema(properties, required), param_types=types,
            meta={"ref_id": workflow_id, "workflow_id": workflow_id,
                  "api_key_id": binding.get("apiKeyId"), "binding": merged})

    def _add_spec(self, specs: dict, *, kind: str, real_name: str, description: str,
                  parameters: dict, param_types: dict, meta: dict) -> str:
        """登记一个可被模型调用的工具，返回注册表的 key。

        不做任何字符归一：发给厂商的 tools[].function.name、模型回的工具名、事件与调用
        记录里的名字，全都是 real_name 本身，一个名字走到底。工具名满不满厂商的字符集
        约束（OpenAI 兼容口径是 ^[a-zA-Z0-9_-]{1,64}$）由登记侧保证，不在这里兜底。

        key 只在一种情况下和真实名不同：两条来路的工具真实名撞车时必须占住不同的 key，
        否则后登记的会把前一个静默覆盖掉；展示仍取 spec["name"]，不把这个前缀泄露给人看。
        """
        ref_id = meta.get("ref_id")
        name = str(real_name or "") or f"{kind.lower()}_{ref_id}"
        if name in specs:
            base = f"{kind.lower()}{ref_id}__{name}"
            name = base
            suffix = 2
            while name in specs:
                name = f"{base}_{suffix}"
                suffix += 1
        specs[name] = {"kind": kind, "name": real_name, "description": description,
                       "parameters": parameters, "param_types": param_types, **meta}
        return name

    # ---------- 插入工具：tool-call 循环 ----------

    async def _run_with_tools(self, ctx, inst, output_var, base_kwargs, so) -> NodeResult:
        """带工具的文生文：tool-call 循环，直到模型不再要求调用工具。

        每轮调完一次模型就走一次 _call_round（流式开关在那里生效）：流式时思维链与
        正文逐 token emit，工具调用照旧能拿到（分片在 _chat_stream 里拼好）。

        messages 就地增长（assistant.tool_calls + role=tool 回填），下一轮模型才看得到
        上一轮的工具结果。usage 每轮都计入本执行，llm_call_count 也就如实反映调了几次。
        """
        specs, errors = await self._build_tool_specs(ctx)
        output: dict = {}
        if errors:
            output["toolErrors"] = errors
        if not specs:
            raise NodeExecutionError(
                f"节点「{self.node.label}」的工具一个都没装配成功"
                + (f"：{'；'.join(errors)}" if errors else ""))
        messages = base_kwargs["messages"]
        call_log: list = []
        await self._replay_resumed_child(messages, specs, call_log)
        kwargs = {**base_kwargs, "tools": [
            {"type": "function", "function": {
                "name": name,
                "description": spec.get("description") or "",
                "parameters": spec.get("parameters")
                              or {"type": "object", "properties": {}}}}
            for name, spec in specs.items()]}
        full_text, reasoning, usage = "", "", {}
        rounds = 0
        streaming = bool(self.config.get("streaming", False))
        while True:
            content, thinking, round_usage, calls = await self._call_round(
                inst, kwargs, streaming)
            if round_usage:
                self.runtime.bump_usage(round_usage.get("prompt_tokens", 0),
                                        round_usage.get("completion_tokens", 0))
                usage = round_usage
            if thinking:
                reasoning += thinking
            if not calls:
                full_text = content
                break
            rounds += 1
            if rounds > self.MAX_TOOL_ROUNDS:
                raise NodeExecutionError(
                    f"节点「{self.node.label}」工具调用已到 {self.MAX_TOOL_ROUNDS} 轮上限，"
                    f"模型仍在要求调用「{(calls[0].get('function') or {}).get('name')}」，已终止")
            # 这条 assistant 必须原样回填（含 tool_calls）：厂商按 tool_call_id 配对，
            # 少了它下一条请求就是「一个没有上文的 tool 消息」，直接被拒
            messages.append({"role": "assistant", "content": content,
                             "tool_calls": calls})
            for call in calls:
                tool_text = await self._invoke_bound_tool(ctx, call, specs, rounds, call_log)
                messages.append({"role": "tool",
                                 "tool_call_id": str(call.get("id") or ""),
                                 "content": tool_text})
        output[output_var] = full_text
        output["text"] = full_text
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
        if call_log:
            output["toolCalls"] = call_log
        return NodeResult(output=output)

    async def _call_round(self, inst, kwargs: dict, streaming: bool):
        """tool-call 循环里的单次模型调用：返回 (正文, 思考, usage, tool_calls)。

        非流式一次拿全；流式边收边 emit_delta（体感改善就在这条路上：思考与答案逐
        token 出，不用等整轮生成完）。tool_calls 不必在这里认识分片：
        ChatMLMixin._chat_stream 已按 index 累加并在收尾片上物化成与 ainvoke 同形状的列表。

        中间轮的正文也会随流推给客户端（模型「先查一下再答」的过场话），这是流式的
        固有形态；output 里的答案只取最后一轮，不与中间轮正文串接。
        """
        if not streaming:
            r = await inst.ainvoke(**kwargs)
            return (r.content or ""), (r.reasoning_content or ""), \
                (r.usage or {}), (r.tool_calls or [])
        content, thinking, usage, calls = "", "", {}, []
        async for c in inst.astream(**kwargs):
            if c.content:
                content += c.content
                await self.emit_delta(c.content, reasoning=False)
            if c.reasoning_content:
                thinking += c.reasoning_content
                await self.emit_delta(c.reasoning_content, reasoning=True)
            if c.usage:
                usage = c.usage
            if c.tool_calls:
                calls = c.tool_calls
        return content, thinking, usage, calls

    async def _invoke_bound_tool(self, ctx, call: dict, specs: dict, rounds: int,
                                 call_log: list) -> str:
        """执行模型要求的这一次调用，返回回填给模型的 tool 消息正文。"""
        from service.service_workflow.workflow_engine.engine import WorkflowCancelled

        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        spec = specs.get(name)
        kind = str((spec or {}).get("kind") or "")
        # 展示与记录用真实名：注册表 key 正常就等于它，只有重名时才会被占位前缀改掉
        real_name = str((spec or {}).get("name") or "") or name
        call_id = str(call.get("id") or "")
        args, arg_error = _parse_tool_args(fn.get("arguments"))
        await self._emit_tool_call(call_id, real_name, kind, args, rounds)
        if spec is None:
            # 模型调了一个没装配出来的名字：回填错误而不是整节点失败，
            # 让它有机会改口或先把已有的答复输出，比直接判死这一轮执行更可用
            error = f"未注册的工具: {name}"
        elif arg_error:
            error = arg_error
        else:
            error = None
        if error is not None:
            payload: dict = {"error": error}
        else:
            try:
                payload = await self._call_bound_tool(ctx, spec, args)
            except (AwaitingApproval, WorkflowCancelled):
                # 挂起/取消是控制流信号，被当成「工具报错」喂回模型会把父执行骗过去
                raise
            except Exception as e:  # noqa: BLE001  单个工具失败只回填错误，由模型决定下一步
                log.warning("[LLM] 节点「{}」工具 {} 调用失败: {}", self.node.label,
                            real_name, e)
                payload = {"error": f"{type(e).__name__}: {e}"}
        item_error = payload.get("error") if isinstance(payload, dict) else None
        text = _truncate(_dumps(payload), self.TOOL_RESULT_MAX_CHARS)
        call_log.append({"name": real_name, "kind": kind, "arguments": args,
                         "round": rounds, "result": payload, "error": item_error})
        await self._emit_tool_result(call_id, real_name, kind, rounds, payload, item_error)
        if self.cfg("emitToolResult") and item_error is None:
            # 需求 8：只有勾选「输出工具调用结果」才把结果作为可见内容 emit；
            # emit_delta 自带节点级「返回内容」开关判定，关掉广播时这里自然也不发
            await self.emit_delta(f"{text}\n", reasoning=False)
        return text

    async def _call_bound_tool(self, ctx, spec: dict, args: dict):
        """按 kind 分派到三条调用路径（参数取模型给的值，缺的可选项由来路兜默认）。"""
        kind = spec["kind"]
        if kind == TOOL_KIND_FUNCTION:
            from service.service_workflow.services.tool_service import ToolService
            # 把模型给的参数摊成 CONSTANT 绑定行复用 run_for_node：工具登记里的 default
            # 会替上模型没给的可选项，与 TOOL 节点「留空回落默认值」同一份语义
            rows = [{"name": k, "type": (spec.get("param_types") or {}).get(k, "string"),
                     "required": False, "sourceType": "CONSTANT", "value": v}
                    for k, v in (args or {}).items()]
            return ToolService.json_safe(
                await ToolService.run_for_node(spec["tool"], rows, ctx.resolve_ref))
        if kind == TOOL_KIND_MCP:
            from service.service_workflow.services.mcp_service import McpServerService
            result = await McpServerService.call_tool(
                int(spec["server_id"]), str(spec["name"]), args or {})
            if result.get("isError"):
                raise ValueError(f"MCP 工具 {spec['name']} 调用失败: "
                                 f"{result.get('content') or '未知错误'}")
            body = (result.get("structured") if result.get("structured") is not None
                    else result.get("content"))
            urls = result.get("urls") or []
            return {"result": body, "urls": urls} if urls else body
        # WORKFLOW：进程内起一条子执行；子流程卡在审批时由调用核抛 AwaitingApproval
        # 类型回正：开始节点只校验必填不转类型（见 StartNodeExecutor），模型把数字
        # 写成 "3" 就得在这里按装配时记下的字段类型转好，否则子流程里的数值判断会跑偏
        types = spec.get("param_types") or {}
        payload = await invoke_child_workflow(
            workflow_id=int(spec["workflow_id"]),
            api_key_id=int(spec.get("api_key_id") or 0),
            cfg=spec["binding"], runtime=self.runtime, node_id=self.node.id,
            label=self.node.label, output_var="result",
            inputs={k: py_sandbox.coerce_constant(v, types.get(k, "string"))
                    for k, v in (args or {}).items()})
        return {"result": payload.get("result"), "text": payload.get("text")}

    async def _replay_resumed_child(self, messages: list, specs: dict,
                                    call_log: list) -> None:
        """恢复轮续跑：把上轮挂起的子工作流结果合成一次工具调用回填进 messages。

        子流程审批结束后父执行按 CONTINUE 重跑，本节点从零重建 messages。若直接进循环，
        模型看不到上次那个调用已经有结果，很可能再调一次（把子流程又跑一遍）——
        childExecutionId 加上子行上的 workflowId/inputs 就是那次调用的全部现场信息。
        没带 childExecutionId 的普通工具（函数/MCP）本来就是同步跑完的，不需要这一步。
        """
        state = self.runtime.node_states.get(self.node.id)
        child_id = getattr(state, "childExecutionId", None) if state is not None else None
        if not child_id:
            return
        from service.service_workflow.services.workflow_execution_service import (
            WorkflowExecutionService,
        )
        from service.service_workflow.workflow_engine.engine import (
            STATUS_CANCELLED, STATUS_COMPLETED, STATUS_PAUSED,
        )

        result = await WorkflowExecutionService.get_child_result(child_id)
        status = result.get("status")
        workflow_id = result.get("workflowId")
        if status == STATUS_PAUSED:
            # 父本轮是被其他待决项推起来的：这份子执行还在等结论，继续等它、不重跑
            raise AwaitingApproval(
                self.node.id,
                build_awaiting_context(
                    self.node.id, self.node.label, child_id=child_id,
                    workflow_id=workflow_id,
                    workflow_name=self._workflow_tool_real_name(specs, workflow_id),
                    approval=result.get("approval")),
                APPROVAL_SCOPE_DOWNSTREAM)
        # 子执行已到终态：这份记录已被本轮消费掉，认据必须清掉 —— 否则本节点后续
        # 再挂起一个子流程时，会对着这个早已跑完的旧子执行取结果
        if state is not None:
            state.childExecutionId = None
        if status == STATUS_CANCELLED:
            raise NodeExecutionError(f"子工作流执行已被取消（executionId={child_id}）")
        if status != STATUS_COMPLETED:
            raise NodeExecutionError(
                f"子工作流执行失败: {result.get('errorMessage') or '未知错误'}"
                f"（executionId={child_id}）")
        name, spec = self._workflow_tool_spec(specs, workflow_id)
        if name is None:
            raise NodeExecutionError(
                f"节点「{self.node.label}」上一轮挂起的子工作流（executionId={child_id}）"
                "已不在本节点的工具列表里，无法续跑，请重新执行")
        real_name = str((spec or {}).get("name") or "") or name
        arguments = dict(result.get("inputs") or {})
        payload = child_output_payload(result.get("outputs"), "result")
        text = _truncate(_dumps(payload), self.TOOL_RESULT_MAX_CHARS)
        call_id = f"call_resumed_{str(child_id)[:8]}"
        # 回填给模型的这组 messages 只能用注册表 key：厂商按它配对 tool_call
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": _dumps(arguments)}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
        call_log.append({"name": real_name, "kind": TOOL_KIND_WORKFLOW,
                         "arguments": arguments, "round": 0, "result": payload,
                         "error": None, "resumed": True})
        await self._emit_tool_call(call_id, real_name, TOOL_KIND_WORKFLOW, arguments, 0,
                                   resumed=True)
        await self._emit_tool_result(call_id, real_name, TOOL_KIND_WORKFLOW, 0, payload,
                                     None, resumed=True)
        if self.cfg("emitToolResult"):
            await self.emit_delta(f"{text}\n", reasoning=False)

    @staticmethod
    def _workflow_tool_spec(specs: dict, workflow_id):
        """按子执行所属的 workflow_id 找回它对应的工具 spec（多个同工作流取第一个）。"""
        try:
            wanted = int(workflow_id)
        except (TypeError, ValueError):
            return None, None
        for name, spec in specs.items():
            if (spec.get("kind") == TOOL_KIND_WORKFLOW
                    and int(spec.get("workflow_id") or 0) == wanted):
                return name, spec
        return None, None

    @classmethod
    def _workflow_tool_real_name(cls, specs: dict, workflow_id) -> str:
        """子工作流工具的真实名（开始审批时给「等待哪个子流程」看）。"""
        spec = cls._workflow_tool_spec(specs, workflow_id)[1]
        return str((spec or {}).get("name") or "")

    async def _emit_tool_call(self, call_id: str, tool_name: str, kind: str,
                              args: dict, rounds: int,
                              resumed: bool = False) -> None:
        """工具调用事件（调试过程展示）：不受节点「返回内容」开关约束。

        这类事件是「过程可见性」而不是节点输出：关掉返回内容只是不把答案推给
        客户端，不该连模型到底调了什么工具都看不到。

        :param tool_name: 工具真实名称（即登记名，见 _add_spec）。
        """
        await self.runtime.emit("node.tool_call", nodeId=self.node.id,
                                toolCallId=call_id, toolName=tool_name, toolKind=kind,
                                arguments=args, round=rounds, resumed=resumed)

    async def _emit_tool_result(self, call_id: str, tool_name: str, kind: str,
                                rounds: int, payload, error,
                                resumed: bool = False) -> None:
        await self.runtime.emit("node.tool_result", nodeId=self.node.id,
                                toolCallId=call_id, toolName=tool_name, toolKind=kind,
                                round=rounds, error=error, resumed=resumed,
                                result=_truncate(_dumps(payload),
                                                 self.TOOL_EVENT_SUMMARY_CHARS))

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("modelId"):
            issues.append(issue("LLM_NO_MODEL", "ERROR", "LLM 节点未选择模型", node))
        for binding in data.get("tools") or []:
            if not isinstance(binding, dict):
                continue
            if str(binding.get("kind") or "").upper() == TOOL_KIND_WORKFLOW \
                    and not binding.get("apiKeyId"):
                issues.append(issue("LLM_TOOL_NO_API_KEY", "ERROR",
                                    f"LLM 节点「{node.label}」插入的子工作流工具未选择执行用 API Key", node,
                                    suggestion="子工作流需要先发布并创建 API Key"))
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
            v = ctx.resolve_ref(ref)
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

    @staticmethod
    def _named_params(parameters) -> list:
        """取填了参数名的参数行。

        画布表单会把空名行原样存进图（编辑期一过滤，config 回写就会把正在输入的那行卸载掉），
        所以取用时统一跳过，避免空字段名进 JSON schema、也避免下游拿到空键。"""
        return [p for p in (parameters or [])
                if isinstance(p, dict) and str(p.get("name") or "").strip()]

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        model_id = self.require_model_id()
        client = await WorkflowModelClient.create(
            model_id, provider=self.runtime.model_provider, http=self.runtime.http_client)
        parameters = self._named_params(cfg.get("parameters"))
        if not parameters:
            raise ValueError("参数提取器未定义提取参数")

        ref = cfg.get("inputVariable")
        input_text = str(ctx.resolve_ref(ref) or "") if ref else ""
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
        if not ParameterExtractorNodeExecutor._named_params(data.get("parameters")):
            issues.append(issue("PE_NO_PARAMS", "ERROR", "参数提取器未定义参数", node))
        return issues
