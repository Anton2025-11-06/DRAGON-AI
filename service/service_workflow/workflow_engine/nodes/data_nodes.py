# -*- coding: utf-8 -*-
"""数据节点：TEMPLATE / CODE / LIST_OPERATOR / DOC_EXTRACTOR / KNOWLEDGE_RETRIEVAL。
"""
from __future__ import annotations

import asyncio
import json
import operator
import re
from functools import reduce
from typing import Any, Callable, Optional

from service.service_workflow.workflow_engine.context import ExecutionContext, _dig
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
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

        if engine == "JINJA2":
            from jinja2 import Environment, StrictUndefined, Undefined
            env = Environment(
                undefined=StrictUndefined if cfg.get("strictMode") else Undefined,
                autoescape=bool(cfg.get("escapeHtml")),
                trim_blocks=bool(cfg.get("trimWhitespace")),
            )
            # 变量集：声明变量 + 全部节点输出扁平化
            variables: dict[str, Any] = {}
            for v in cfg.get("variables") or []:
                name = v.get("name")
                if not name:
                    continue
                value = ctx.resolve(str(v.get("reference") or "")) if v.get("reference") \
                    else v.get("defaultValue")
                variables[name] = value if value is not None else v.get("defaultValue")
            variables.setdefault("global", ctx.global_vars)
            try:
                rendered = env.from_string(template).render(**variables)
            except Exception as e:  # noqa: BLE001
                if cfg.get("strictMode"):
                    raise ValueError(f"模板渲染失败: {e}") from e
                rendered = ctx.render(template)  # 降级 SIMPLE
        else:
            rendered = ctx.render(template, strict=bool(cfg.get("strictMode")))

        output_var = cfg.get("outputVariable") or "output"
        return NodeResult(output={output_var: rendered, "text": rendered})


class CodeNodeExecutor(BaseNodeExecutor):
    """CODE 代码执行节点（对照 Dify Code 节点 / MaxKB tool-node 的 Python 函数沙箱）。

    安全模型：
    - 受限 exec：内置函数白名单（无 open/exec/eval/__import__ 等）
    - 禁 import（代码里无 import 语句权限）；超时（默认 10s，线程 join 强杀）
    - 输出限制：字符串 ≤200KB / 数组 ≤100 元素（对齐前端注释约定）
    - 沙箱函数签名：def main(**kwargs) -> dict
    二期演进：接入 DRAGON-AI 已有 sandbox_service / 隔离容器。
    """

    node_type = "CODE"

    _ALLOWED_BUILTINS: dict[str, Any] = {
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "divmod": divmod,
        "enumerate": enumerate, "filter": filter, "float": float, "format": format,
        "frozenset": frozenset, "int": int, "isinstance": isinstance, "issubclass": issubclass,
        "len": len, "list": list, "map": map, "max": max, "min": min, "next": next,
        "object": object, "range": range, "reversed": reversed, "round": round, "set": set,
        "slice": slice, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "type": type,
        "zip": zip, "True": True, "False": False, "None": None,
    }

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        language = (cfg.get("language") or "PYTHON").upper()
        if language != "PYTHON":
            raise ValueError(f"暂不支持的语言: {language}（一期仅 PYTHON；JS 走沙箱服务二期）")
        code = cfg.get("code") or ""
        if not code:
            raise ValueError("代码节点内容为空")

        inputs: dict[str, Any] = {}
        for v in cfg.get("inputs") or []:
            name = v.get("name")
            if not name:
                continue
            ref = v.get("sourceVariable")
            inputs[name] = ctx.resolve(str(ref)) if ref else None

        timeout_ms = int(cfg.get("timeout") or 10000)
        result = await asyncio.wait_for(
            asyncio.to_thread(self._exec_sandbox, code, inputs, bool(cfg.get("sandboxEnabled", True))),
            timeout=timeout_ms / 1000,
        )
        self._validate_outputs(result)
        output_var = cfg.get("outputVariable") or "output"
        if output_var and output_var not in result:
            # 注意：不能用 result[output_var] = result —— 自引用成环会导致
            # 执行响应（nodeStates）序列化时 RecursionError / Circular reference。
            # 用浅拷贝，使 {{nodeId.output}} 仍可引用整体输出，同时断开自引用。
            result[output_var] = dict(result)
        return NodeResult(output=result)

    def _exec_sandbox(self, code: str, inputs: dict, sandbox: bool) -> dict:
        import builtins
        namespace: dict[str, Any] = {"__builtins__": self._ALLOWED_BUILTINS if sandbox
                                     else builtins}
        try:
            exec(compile(code, "<workflow-code>", "exec"), namespace)  # noqa: S102
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"代码执行错误: {e}") from e
        fn = namespace.get("main")
        if not callable(fn):
            raise ValueError("代码必须定义 main 函数: def main(**kwargs) -> dict")
        try:
            result = fn(**inputs)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"main() 执行错误: {e}") from e
        if not isinstance(result, dict):
            raise ValueError(f"main() 必须返回 dict，实际: {type(result).__name__}")
        return result

    @staticmethod
    def _validate_outputs(result: dict) -> None:
        for k, v in result.items():
            if isinstance(v, str) and len(v.encode("utf-8")) > 200 * 1024:
                raise ValueError(f"输出变量 {k} 超过 200KB 限制")
            if isinstance(v, list) and len(v) > 100:
                raise ValueError(f"输出变量 {k} 数组超过 100 元素限制")

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
        arr = ctx.resolve(str(ref or ""))
        if arr is None:
            raise ValueError(f"输入数组变量无法解析: {ref}")
        if not isinstance(arr, list):
            raise ValueError(f"输入变量不是数组: {ref} ({type(arr).__name__})")

        op = (cfg.get("operationType") or "FIRST").upper()
        result = self._apply(op, arr, cfg, ctx)
        output_var = cfg.get("outputVariable") or "output"
        return NodeResult(output={output_var: result, "count": len(arr) if isinstance(arr, list) else 0})

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
                other = ctx.resolve(str(other_ref))
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
    """DOC_EXTRACTOR：文档文本提取（PDF/Word/Excel/MD/TXT 等）。

    一期：本地文件（workflow-files 上传）用 pypdf/python-docx/openpyxl 提取；
    OCR 与分页配置二期接入。fileVariable 支持引用（fileId / 本地路径 / URL）。
    """

    node_type = "DOC_EXTRACTOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        ref = self.cfg("fileVariable")
        file_ref = ctx.resolve(str(ref or ""))
        if not file_ref:
            raise ValueError("文档提取节点未指定文件变量")
        loader = getattr(self.runtime, "file_loader", None)
        if not callable(loader):
            raise ValueError("文件服务未接入（file_loader 未注册）")
        content, metadata = await loader(str(file_ref), self.cfg("supportedTypes"))
        output_var = self.cfg("outputVariable") or "content"
        output = {output_var: content, "text": content}
        if self.cfg("extractMetadata"):
            output["metadata"] = metadata
        return NodeResult(output=output)


class KnowledgeRetrievalNodeExecutor(BaseNodeExecutor):
    """KNOWLEDGE_RETRIEVAL（对照 MaxKB search-knowledge-node，改为 HTTP 调用 service_rag）。

    通过 runtime.kb_searcher 钩子注入检索能力（生产=service_rag /kb/search HTTP 客户端，
    测试=内存检索器），节点本身只做参数组装与 rerank 合并。
    """

    node_type = "KNOWLEDGE_RETRIEVAL"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        kb_ids = cfg.get("knowledgeBaseIds") or []
        if not kb_ids:
            raise ValueError("知识检索节点未选择知识库")
        query = ctx.render(str(cfg.get("queryVariable") or ""))
        if not query:
            raise ValueError("知识检索查询变量为空")

        searcher = getattr(self.runtime, "kb_searcher", None)
        if not callable(searcher):
            raise ValueError("知识库服务未接入（kb_searcher 未注册）")

        results = await searcher(
            kb_ids=[int(k) for k in kb_ids],
            query=query,
            top_k=int(cfg.get("topK") or 5),
            score_threshold=float(cfg.get("scoreThreshold") or 0.0),
            retrieval_mode=(cfg.get("retrievalMode") or "VECTOR").upper(),
            metadata_filters=cfg.get("metadataFilters") or [],
        )

        # Rerank（可选：走 WorkflowModelClient.rerank）
        rc = cfg.get("rerankConfig") or {}
        if rc.get("enabled") and rc.get("rerankModelId") and len(results) > 1:
            try:
                client = await WorkflowModelClient.create(
                    int(rc["rerankModelId"]), provider=self.runtime.model_provider)
                docs = [str(r.get("content", "")) for r in results]
                reranked = await client.rerank(query, docs, top_n=int(rc.get("topN") or 5))
                idx_map = {r.get("index"): r.get("relevance_score", 0) for r in reranked}
                for i, r in enumerate(results):
                    r["score"] = idx_map.get(i, r.get("score", 0))
                results.sort(key=lambda r: r.get("score", 0), reverse=True)
                results = results[:int(rc.get("topN") or 5)]
            except Exception:  # noqa: BLE001
                pass  # rerank 失败降级原始结果

        output_var = cfg.get("outputVariable") or "documents"
        output = {
            output_var: results,
            "text": "\n\n".join(
                f"[{i + 1}] {r.get('content', '')}" for i, r in enumerate(results)),
            "count": len(results),
        }
        if cfg.get("includeScore") is False:
            output[output_var] = [{k: v for k, v in r.items() if k != "score"} for r in results]
        return NodeResult(output=output)

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        if not data.get("knowledgeBaseIds"):
            issues.append(issue("KB_NO_SELECTED", "ERROR", "知识检索节点未选择知识库", node))
        if not data.get("queryVariable"):
            issues.append(issue("KB_NO_QUERY", "ERROR", "知识检索节点未配置查询变量", node))
        return issues


# 延迟导入避免循环
from service.service_workflow.workflow_engine.model_client import WorkflowModelClient  # noqa: E402
