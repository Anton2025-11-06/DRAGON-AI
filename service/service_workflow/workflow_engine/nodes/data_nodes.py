# -*- coding: utf-8 -*-
"""数据节点：TEMPLATE / CODE / LIST_OPERATOR / DOC_EXTRACTOR / KNOWLEDGE_RETRIEVAL。
"""
from __future__ import annotations

import json
import operator
from functools import reduce
from typing import Any, Optional

from service.service_workflow.utils.file_utils import FileUtils
from service.service_workflow.workflow_engine import py_sandbox
from service.service_workflow.workflow_engine.context import (
    ExecutionContext, _dig, parse_json_if_embedded,
)
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, file_url, issue,
)


class TemplateNodeExecutor(BaseNodeExecutor):
    """TEMPLATE 模板转换：SIMPLE（{{var}} 替换）/ JINJA2（jinja2 渲染）。"""

    node_type = "TEMPLATE"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        template = cfg.get("template") or ""
        if not template:
            raise ValueError("模板节点内容为空")
        engine = (cfg.get("engine") or "SIMPLE").upper()

        # 「输入变量」表：变量名 -> 引用解析值（解析不到时回落默认值）。
        # 两种引擎都要用上：此前只有 JINJA2 读这张表，SIMPLE 只按执行上下文
        # 解析 {{ref}}，导致 {{name}} 这类声明变量永远替换不上。
        variables: dict[str, Any] = {}
        for v in cfg.get("variables") or []:
            name = v.get("name")
            if not name:
                continue
            reference = v.get("reference")
            value = ctx.resolve_ref(reference) if reference else None
            variables[str(name)] = value if value is not None else v.get("defaultValue")

        if engine == "JINJA2":
            from jinja2 import Environment, StrictUndefined, Undefined
            env = Environment(
                undefined=StrictUndefined if cfg.get("strictMode") else Undefined,
                autoescape=bool(cfg.get("escapeHtml")),
                trim_blocks=bool(cfg.get("trimWhitespace")),
            )
            jinja_vars = {**variables, "global": ctx.global_vars}
            try:
                rendered = env.from_string(template).render(**jinja_vars)
            except Exception as e:  # noqa: BLE001
                if cfg.get("strictMode"):
                    raise ValueError(f"模板渲染失败: {e}") from e
                rendered = self._render_simple(ctx, template, variables, cfg)
        else:
            rendered = self._render_simple(ctx, template, variables, cfg)

        output_var = cfg.get("outputVariable") or "output"
        # 只输出 outputVariable 一个键（与 REPLY 对齐，去掉冗余的 text 别名键）
        return NodeResult(output={output_var: rendered})

    @staticmethod
    def _render_simple(
        ctx: ExecutionContext, template: str, variables: dict[str, Any], cfg: dict
    ) -> Any:
        """SIMPLE 渲染：把声明的「输入变量」作为最高优先级作用域压入上下文。

        复用 ExecutionContext.render（统一处理嵌套路径/文件变量/严格模式），
        仅额外让 {{变量名}} 先命中本节点声明的变量表。
        """
        ctx.push_scope(variables)
        try:
            return ctx.render(template, strict=bool(cfg.get("strictMode")))
        finally:
            ctx.pop_scope()


class ReplyNodeExecutor(BaseNodeExecutor):
    """REPLY 指定回复：引用参数值或自定义文本（二选一），输出为回复文本。

    对照 Dify Answer / Coze 回复节点：执行时把内容作为回复文本写入节点输出
    （{outputVariable: text}），并写入全局 __reply__，
    引擎 _collect_outputs 按执行顺序将 REPLY 节点输出合并进最终 outputs，
    使回复内容随 workflow.completed 直接返回给客户端。
    """

    node_type = "REPLY"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        reply_type = (cfg.get("replyType") or "TEXT").upper()
        if reply_type == "VARIABLE":
            # 引用参数：直接回复参数值（纯字符串保留原文，对象/数组 JSON 序列化）
            ref = str(cfg.get("variableRef") or "").strip()
            if not ref:
                raise ValueError(f"节点「{self.node.label}」未选择引用参数")
            # 该字段是 VariableInput：可以只放一个引用，也可以插入多个引用/混排文本，
            # 统一交给 resolve_ref（单引用保留类型，多引用渲染成拼接文本）
            value = ctx.resolve_ref(ref)
            if value is None:
                raise ValueError(
                    f"节点「{self.node.label}」引用的参数无法解析: "
                    f"{self._unresolved_detail(ctx, ref)}")
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        else:
            # 自定义文本：支持 {{节点.变量}} 模板渲染，未解析引用渲染为空串
            text = cfg.get("text") or ""
            if not text:
                raise ValueError(f"节点「{self.node.label}」回复内容为空")
            text = ctx.render(text, keep_unresolved=False)

        output_var = cfg.get("outputVariable") or "output"
        # 全局标记最近一次回复（引擎收集最终 outputs 时按执行顺序覆盖）
        ctx.global_vars["__reply__"] = text
        # 只输出 outputVariable 一个键，避免 output/text 双键重复暴露
        return NodeResult(output={output_var: text})

    def _unresolved_detail(self, ctx: ExecutionContext, ref: str) -> str:
        """把「引用的参数无法解析」定位到具体引用，并说明上游为何还没值。

        引用参数可以是多个 {{...}}（画布上是 VariableInput，可混排文本），只回显
        整串模板没法定位到到底是那一段取不上，故逐个列出解不出值的引用；引用的是
        审批节点时额外说清「结论没提交前本轮没有输出」。
        """
        bad = ctx.unresolved_refs(ref)
        if not bad:
            return ref
        states = getattr(self.runtime, "node_states", None) or {}
        parts = []
        for one in bad:
            node_id = one.split(".", 2)[1] if one.startswith("nodes.") else one
            status = getattr(states.get(node_id), "status", "")
            if status == "AWAITING":
                tail = "（该节点正在等人工审批，结论提交前本轮没有输出）"
            elif status in ("PENDING", "RUNNING"):
                tail = f"（该节点尚未执行完成，当前 {status}）"
            else:
                tail = ""
            parts.append(f"{{{{{one}}}}}{tail}")
        return "、".join(parts)

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        reply_type = (data.get("replyType") or "TEXT").upper()
        if reply_type == "VARIABLE":
            if not str(data.get("variableRef") or "").strip():
                issues.append(issue("REPLY_NO_VARIABLE", "ERROR", "回复节点未选择引用参数", node))
        elif not str(data.get("text") or "").strip():
            issues.append(issue("REPLY_NO_TEXT", "ERROR", "回复节点文本内容为空", node))
        return issues


class CodeNodeExecutor(BaseNodeExecutor):
    """CODE 代码执行节点（Python 受限沙箱，参照 MaxKB ToolExecutor 设计）。

    能力：
    - 仅支持 Python；代码块统一书写规范：import + def 方法，执行器自动识别入口：
      main() 优先，无 main 时回退到代码中最后定义的顶层函数
      （不支持顶层 return 写法，返回值一律在入口方法内 return）
    - 支持 import 导包（受限 builtins 白名单 + 危险模块黑名单拦截）；
      禁裸 exec/eval/open；超时（默认 10s，线程内执行 + asyncio 超时）
    - 参数 inputs 配置项：参数名 name / 类型 type / 是否必填 required /
      来源 sourceType（REFERENCE 引用变量经 ctx.resolve 解析、CONSTANT 自定义值经 coerce_constant 转换）
    - 节点输出统一为 {"result": <方法返回值>}，下游用 {{nodeId.result}} 引用
    - 输出限制：字符串 ≤200KB / 数组 ≤100 元素（递归校验）
    沙箱实现与「动态函数工具」共用一份（workflow_engine/py_sandbox.py），
    两边能力口径必须一致：能在工作流代码节点里跑的代码，登记成工具后同样能跑。
    注：始终在受限命名空间内执行（无沙箱开关，用户不需要感知）；
    进程内 exec 无法强隔离，二期演进接入隔离容器。
    """

    node_type = "CODE"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        code = cfg.get("code") or ""
        if not code:
            raise ValueError("代码节点内容为空")
        # 入口函数自动识别：main 优先，无 main 回退最后定义的顶层函数；
        # 兼容旧数据：配置过 entryFunction 时仍优先按该方法名查找
        entry = (cfg.get("entryFunction") or "main").strip() or "main"
        # 解析参数：参数名 / 类型 / 是否必填 / 来源（REFERENCE 引用参数 | CONSTANT 自定义值）
        kwargs = py_sandbox.resolve_kwargs(cfg.get("inputs") or [], ctx.resolve_ref)
        timeout_ms = int(cfg.get("timeout") or py_sandbox.DEFAULT_TIMEOUT_MS)
        result = await py_sandbox.run_code(code, kwargs, entry=entry, timeout_ms=timeout_ms)
        py_sandbox.validate_output(result)
        # 节点输出统一为 {"result": <方法返回值>}，下游任意节点用 {{nodeId.result}} 接收
        return NodeResult(output={"result": result})

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        if not (node.data or {}).get("code"):
            issues.append(issue("CODE_EMPTY", "ERROR", "代码节点内容为空", node))
        return issues


class ListOperatorNodeExecutor(BaseNodeExecutor):
    """LIST_OPERATOR：12 种数组操作（FILTER/SORT/SLICE/EXTRACT/UNIQUE/LIMIT/CONCAT/
    FIRST/LAST/COUNT/REVERSE/FLATTEN）。"""

    node_type = "LIST_OPERATOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        ref = cfg.get("inputVariable")
        # 引用本身支持 JSONPath 风格子路径/下标再次提取（{{大模型.output.data[0]}}）；
        # 解析出来的值是大模型常见的 JSON 文本时先解开，再判断是否数组。
        # 不做类型预检（前端已放开“只能选数组”的限制），取不到数组时直接报错提示写法。
        arr = parse_json_if_embedded(ctx.resolve_ref(ref))
        if arr is None:
            raise ValueError(f"输入数组变量无法解析: {ref or '(空)'}")
        if isinstance(arr, tuple):
            arr = list(arr)
        if not isinstance(arr, list):
            raise ValueError(
                f"输入变量不是数组: {ref}（实际类型 {type(arr).__name__}）——"
                f"可在引用后追加字段路径或下标取到数组，如 "
                f"{{{{...输出.data}}}} / {{{{...输出[0].list}}}}")

        op = (cfg.get("operationType") or "FIRST").upper()
        result = self._apply(op, arr, cfg, ctx)
        output_var = cfg.get("outputVariable") or "output"
        return NodeResult(output={output_var: result, "count": len(arr)})

    def _apply(self, op: str, arr: list, cfg: dict, ctx: ExecutionContext) -> Any:
        from service.service_workflow.workflow_engine.comparators import compare
        if op == "FIRST":
            return arr[0] if arr else None
        if op == "LAST":
            return arr[-1] if arr else None
        if op == "COUNT":
            return len(arr)
        if op == "REVERSE":
            return list(reversed(arr))
        if op == "FLATTEN":
            out = []
            for item in arr:
                if isinstance(item, list):
                    out.extend(item)
                else:
                    out.append(item)
            return out
        if op == "FILTER":
            fc = cfg.get("filterConfig") or {}
            conds = fc.get("conditions") or []
            logic = (fc.get("operator") or "AND").upper()
            def _match(item):
                results = [compare(c.get("operator", "EQUALS"),
                                   _dig(item, c.get("field", "")), c.get("value"))
                           for c in conds]
                return any(results) if logic == "OR" else all(results)
            return [x for x in arr if _match(x)]
        if op == "SORT":
            sc = cfg.get("sortConfig") or {}
            field, direction = sc.get("field"), sc.get("direction", "ASC")
            def _key(item):
                v = _dig(item, field) if field else item
                return (str(v).lower() if sc.get("ignoreCase") and isinstance(v, str) else v)
            return sorted(arr, key=_key, reverse=(direction == "DESC"))
        if op == "SLICE":
            sl = cfg.get("sliceConfig") or {}
            return arr[slice(sl.get("start", 0), sl.get("end"), sl.get("step"))]
        if op == "EXTRACT":
            ec = cfg.get("extractConfig") or {}
            fields = ec.get("fields") or []
            out = []
            for item in arr:
                if len(fields) == 1 and ec.get("flatten"):
                    out.append(_dig(item, fields[0]))
                else:
                    out.append({f: _dig(item, f) for f in fields})
            return out
        if op == "UNIQUE":
            uc = cfg.get("uniqueConfig") or {}
            field, keep = uc.get("field"), (uc.get("keepStrategy") or "FIRST").upper()
            seen: dict[Any, Any] = {}
            for item in arr:
                key = _dig(item, field) if field else item
                if keep == "FIRST":
                    seen.setdefault(key, item)
                else:
                    seen[key] = item
            return list(seen.values())
        if op == "LIMIT":
            lc = cfg.get("limitConfig") or {}
            return arr[int(lc.get("offset", 0)):][:int(lc.get("count", len(arr)))]
        if op == "CONCAT":
            cc = cfg.get("concatConfig") or {}
            merged = list(arr)
            for other_ref in cc.get("otherArrays") or []:
                other = parse_json_if_embedded(ctx.resolve_ref(other_ref))
                if isinstance(other, list):
                    merged.extend(other)
            if cc.get("removeDuplicates"):
                seen = set()
                out = []
                for x in merged:
                    key = json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)
                    if key not in seen:
                        seen.add(key)
                        out.append(x)
                return out
            return merged
        raise ValueError(f"不支持的列表操作: {op}")


class DocExtractorNodeExecutor(BaseNodeExecutor):
    """DOC_EXTRACTOR：文档文本提取（PDF/Word/Excel/PPT/Markdown/HTML 等 11 种格式）。

    入参是文件 URL（不再支持本地路径）：引用开始节点的 FILE/FILE_LIST 参数或直接贴 URL，
    值形式统一由 file_url() 归一（上传结果对象/对象数组/字符串都能取到 url）。
    按扩展名用 Python 生态库内存解析：pypdf / python-docx / openpyxl / xlrd /
    python-pptx / 内置文本读取；老格式 .doc/.ppt 走 LibreOffice headless 转换
    （未安装时报清晰错误）。不做 OCR 与分页。
    """

    node_type = "DOC_EXTRACTOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        ref = str(self.cfg("fileVariable") or "")
        # 引用优先：前端 {{inputs.doc}} / 裸 n_start.doc 都兼容（resolve 入口统一剥壳）；
        # 解析不到时回退字面量（用户直接填了上传返回的 URL）
        url = file_url(ctx.resolve_ref(ref)) or file_url(ref)
        if not url:
            raise ValueError(f"文档提取节点未取到文件 URL（变量需为上传接口返回的文件）: {ref or '(空)'}")
        content, metadata = await FileUtils.extract_from_url(
            url, supported_types=self.cfg("supportedTypes"),
            http_client=self.runtime.http_client)
        output_var = self.cfg("outputVariable") or "content"
        # 只输出 outputVariable 一个键（与 REPLY/TEMPLATE 对齐，去掉冗余的 text 别名键）
        output = {output_var: content}
        if self.cfg("extractMetadata"):
            output["metadata"] = metadata
        return NodeResult(output=output)


class KnowledgeRetrievalNodeExecutor(BaseNodeExecutor):
    """KNOWLEDGE_RETRIEVAL：进程内直调 service_rag 的检索门面（与知识检索页同一颗召回）。

    为什么不走 runtime 钩子：与 TOOL/MCP_TOOL 同一条口径（见 external_nodes 模块头），
    执行器直接调 service 层。原先挂在 runtime.kb_searcher 上的那套接线全仓无人注册，
    节点一执行必抛「知识库服务未接入」；多一层没有实现者的间接只会把「没接上」
    拖到运行时才暴露。函数内延迟 import 避开 engine←nodes←services 循环。

    重排不再在节点里自己做：服务层按本次选的 rerank_model_id 已经重排并截断，
    节点再排一次等于同一批候选过两道尺子，分数与顺序都对不上页面上的结果。

    检索身份取**工作流归属人**（Workflow.created_by）而不是 runtime.user_id：
    后者是「谁点的运行」，API Key/定时触发时是 0，用它的结果是一条权限正常的流程
    跑出空召回。归属人才是编辑期用 ACTION_USE 挑出这批库的那个人。
    """

    node_type = "KNOWLEDGE_RETRIEVAL"

    # 输出行的裁剪口径：默认与检索页同构（RetrieveHit.dump 的那套键），两个开关只服务
    # 「下游只要正文」的场景——整段元数据拼进 prompt 时 token 翻倍却没人读。
    # 判定一律「显式 false 才剪」：缺键的旧画布保持信息最全的那一种形态。
    _SCORE_FIELDS = ("score", "vectorScore", "keywordScore")
    _META_FIELDS = ("kbId", "kbName", "docId", "docName", "chunkId", "chunkIndex",
                    "chunkType", "titlePath", "pageNum", "sheetName", "mediaUrl",
                    "mediaUrls", "mediaDuration", "blockId", "recall", "graphEntity",
                    "graphSummary", "graphTriples")

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        from common.common_permission.login_payload import load_login_user
        # 延迟 import：检索链路（ES/Neo4j/模型配置）只在装了 rag 后端的 worker 里可用
        from service.service_rag.services.retrieval_service import RagRetrievalService
        from service.service_workflow.services.workflow_execution_service import (
            WorkflowExecutionService,
        )

        cfg = self.config
        kb_ids = [int(k) for k in (cfg.get("knowledgeBaseIds") or []) if k]
        if not kb_ids:
            raise ValueError("知识检索节点未选择知识库")
        query = str(ctx.render(str(cfg.get("queryVariable") or "")) or "").strip()
        if not query:
            raise ValueError("知识检索查询变量为空")

        # rerankConfig 缺键=不覆盖库配置（旧图）；存在则完全按面板上的开关走：
        # 关了就不重排，开了没选模型就回到库上登记的重排模型（与字段提示一致）
        rc = cfg.get("rerankConfig")
        rerank_model_id = 0
        rerank: Optional[bool] = None
        if isinstance(rc, dict):
            rerank = bool(rc.get("enabled"))
            rerank_model_id = int(rc.get("rerankModelId") or 0)

        rows, warnings = await RagRetrievalService.search_detail(
            await load_login_user(await self._owner_id(WorkflowExecutionService)),
            kb_ids=kb_ids, query=query,
            # topK/阈值留空一律传 0/None，回到「用知识库自己的配置」，
            # 而不是在节点里硬编一个和页面不同的默认值
            top_k=int(cfg.get("topK") or 0),
            score_threshold=_opt_float(cfg.get("scoreThreshold"), "相似度阈值"),
            # 空串经 parse_mode 归一成 None = 按库类型自动定；FULLTEXT 写法由别名表接住
            retrieval_mode=str(cfg.get("retrievalMode") or ""),
            vector_weight=_opt_float(cfg.get("vectorWeight"), "向量权重"),
            chunk_type=cfg.get("chunkType") or None,
            metadata_filters=cfg.get("metadataFilters") or [],
            rerank=rerank, rerank_model_id=rerank_model_id,
            with_graph=bool(cfg.get("withGraph")),
            chat_model_id=int(cfg.get("graphChatModelId") or 0),
            graph_depth=cfg.get("graphDepth"), graph_scale=cfg.get("graphScale"),
            graph_source_chunks=bool(cfg.get("graphSourceChunks")))

        trimmed = [self._trim(row, cfg) for row in rows]
        output_var = cfg.get("outputVariable") or "documents"
        # warnings 必须外露：节点拿到空结果时，这是唯一能区分「库里没内容」与
        # 「这个库你没权限」「阈值得太高」的线索（只写日志等于用户永远看不到）
        return NodeResult(output={
            output_var: trimmed,
            "text": "\n\n".join(
                f"[{i + 1}] {r.get('content', '')}" for i, r in enumerate(trimmed)),
            "count": len(trimmed),
            "warnings": warnings,
        })

    async def _owner_id(self, execution_service) -> int:
        """本次检索要用谁的身份：工作流归属人，拿不到时退回执行行的 user_id。"""
        owner = 0
        execution_id = getattr(self.runtime, "execution_id", None)
        if execution_id:
            brief = await execution_service.workflow_of_execution(str(execution_id))
            owner = int(brief[1]) if brief else 0
        uid = owner or int(getattr(self.runtime, "user_id", 0) or 0)
        if not uid:
            raise ValueError("知识检索节点拿不到检索身份：工作流没有归属人，本次执行"
                             "也不是用户发起的（API Key/定时触发不带用户，"
                             "而检索必须按某个人的知识库权限鉴权）")
        return uid

    @staticmethod
    def _trim(row: dict, cfg: dict) -> dict:
        drop: set[str] = set()
        if cfg.get("includeScore") is False:
            drop.update(KnowledgeRetrievalNodeExecutor._SCORE_FIELDS)
        if cfg.get("includeMetadata") is False:
            drop.update(KnowledgeRetrievalNodeExecutor._META_FIELDS)
        return {k: v for k, v in row.items() if k not in drop}

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("knowledgeBaseIds"):
            issues.append(issue("KB_NO_SELECTED", "ERROR", "知识检索节点未选择知识库", node))
        if not str(data.get("queryVariable") or "").strip():
            issues.append(issue("KB_NO_QUERY", "ERROR", "知识检索节点未配置查询变量", node))
        # 开了图谱增强就必须选对话模型：这一路要先让大模型从问题里抽出实体和关系，
        # 没模型时执行只会得到一句 warning + 纯切片结果（与检索页同一口径，提前拦住）
        if data.get("withGraph") and not int(data.get("graphChatModelId") or 0):
            issues.append(issue(
                "KB_GRAPH_NO_MODEL", "ERROR", "知识检索节点开启图谱增强但未选择对话模型", node,
                suggestion="图谱那一路靠对话模型从查询里抽实体和关系，请在本节点选择一个对话模型"))
        rc = data.get("rerankConfig")
        if isinstance(rc, dict) and rc.get("enabled") and not int(rc.get("rerankModelId") or 0):
            issues.append(issue(
                "KB_RERANK_NO_MODEL", "WARNING", "知识检索节点启用了重排序但未选择重排序模型", node,
                suggestion="不选模型时本节点沿用知识库上的重排配置；库上也没配就等于没重排"))
        return issues


def _opt_float(value: Any, label: str) -> Optional[float]:
    """可选数值参数：留空=None（用知识库配置），填了解不开就要说清楚是哪一项。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"知识检索节点的{label}不是合法数值: {value}") from None

