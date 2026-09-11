# -*- coding: utf-8 -*-
"""数据节点：TEMPLATE / CODE / LIST_OPERATOR / DOC_EXTRACTOR / KNOWLEDGE_RETRIEVAL。
"""
from __future__ import annotations

import ast
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
        # 只输出 outputVariable 一个键（与 REPLY 对齐，去掉冗余的 text 别名键）
        return NodeResult(output={output_var: rendered})


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
            # 前端 VariableInput 存入 {{node.var}} 模板格式，剥壳后解析
            value = ctx.resolve(ref.strip("{} "))
            if value is None:
                raise ValueError(f"节点「{self.node.label}」引用的参数无法解析: {ref}")
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

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        data = node.data or {}
        reply_type = (data.get("replyType") or "TEXT").upper()
        if reply_type == "VARIABLE":
            if not str(data.get("variableRef") or "").strip().strip("{}").strip():
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
      禁裸 exec/eval/open；超时（默认 10s，线程 join 强杀）
    - 参数 inputs 配置项：参数名 name / 类型 type / 是否必填 required /
      来源 sourceType（REFERENCE 引用变量经 ctx.resolve 解析、CONSTANT 自定义值经 _coerce_constant 转换）
    - 节点输出统一为 {"result": <方法返回值>}，下游用 {{nodeId.result}} 引用
    - 输出限制：字符串 ≤200KB / 数组 ≤100 元素（递归校验）
    注：始终在受限命名空间内执行（无沙箱开关，用户不需要感知）；
    进程内 exec 无法强隔离，二期演进接入隔离容器。
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

    # 危险模块黑名单：可执行系统命令/读写文件/网络/进程/序列化反序列化/反射的模块一律拦截；
    # 允许 json/math/re/random/collections/functools/itertools 等纯数据处理类标准库。
    # 注意：进程内 exec 无法做到强隔离，二期接入沙箱服务/隔离容器后放开。
    _BLOCKED_IMPORTS: frozenset[str] = frozenset({
        # 系统 / 进程 / IO
        "os", "sys", "shutil", "pathlib", "glob", "tempfile", "subprocess",
        "multiprocessing", "threading", "_thread", "ctypes", "signal",
        "resource", "pwd", "grp", "platform", "gc", "tracemalloc",
        "faulthandler", "pty", "termios", "tty", "winreg", "msvcrt",
        # 网络
        "socket", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib",
        "telnetlib", "ssl", "asyncio", "aiohttp", "requests",
        # 序列化 / 反射 / 动态执行
        "pickle", "marshal", "shelve", "importlib", "builtins", "runpy",
        "zipimport", "site", "sysconfig", "code", "codeop", "pdb", "bdb",
        "cProfile", "profile", "traceback", "inspect", "dis",
        # 数据库 / 归档 / 标记语言
        "sqlite3", "dbm", "gdbm", "tarfile", "zipfile", "bz2", "lzma",
        "xml", "email",
        # 进程管理 / pip 相关 / GUI
        "venv", "ensurepip", "distutils", "pip", "tkinter",
    })

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        cfg = self.config
        code = cfg.get("code") or ""
        if not code:
            raise ValueError("代码节点内容为空")
        # 入口函数自动识别：main 优先，无 main 回退最后定义的顶层函数；
        # 兼容旧数据：配置过 entryFunction 时仍优先按该方法名查找
        entry = (cfg.get("entryFunction") or "main").strip() or "main"

        # 解析参数：参数名 / 类型 / 是否必填 / 来源（REFERENCE 引用参数 | CONSTANT 自定义值）
        kwargs: dict[str, Any] = {}
        for v in cfg.get("inputs") or []:
            name = v.get("name")
            if not name:
                continue
            source_type = (v.get("sourceType") or "REFERENCE").upper()
            if source_type == "CONSTANT":
                value = self._coerce_constant(v.get("value"), v.get("type"))
            else:
                ref = v.get("sourceVariable")
                value = ctx.resolve(str(ref)) if ref else None
            if value is None and v.get("required"):
                raise ValueError(f"缺少必填参数: {name}")
            kwargs[name] = value

        timeout_ms = int(cfg.get("timeout") or 10000)
        result = await asyncio.wait_for(
            asyncio.to_thread(self._exec_sandbox, code, kwargs, entry),
            timeout=timeout_ms / 1000,
        )
        self._validate_output(result)
        # 节点输出统一为 {"result": <方法返回值>}，下游任意节点用 {{nodeId.result}} 接收
        return NodeResult(output={"result": result})

    def _exec_sandbox(self, code: str, kwargs: dict, entry: str) -> Any:
        import builtins
        # 统一书写规范：import + def 方法，入口方法内 return 返回值
        namespace: dict[str, Any] = {
            "__builtins__": self._build_builtins(builtins),
        }
        # 代码中定义的顶层函数名（按定义顺序），未指定方法名时回退取最后一个
        top_funcs = self._top_level_function_names(code)
        try:
            exec(compile(code, "<workflow-code>", "exec"), namespace)  # noqa: S102
        except SyntaxError as e:  # noqa: BLE001
            raise ValueError(
                f"代码语法错误: {e}（书写规范：import + def 方法，返回值在入口方法内 return）"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"代码执行错误: {e}") from e
        fn = namespace.get(entry)
        if not callable(fn) and entry == "main":
            # 没有 main：参照 MaxKB 取最后定义的顶层函数，
            # 使 def process(...) 这类代码无需填写方法名也能直接运行
            fallback = next((f for f in reversed(top_funcs) if not f.startswith("_")), None)
            if fallback:
                fn = namespace.get(fallback)
        if not callable(fn):
            raise ValueError(f"未找到方法 {entry}()，请检查代码定义（默认 main，或最后定义的顶层函数）")
        try:
            return fn(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"{entry}() 执行错误: {e}") from e

    @staticmethod
    def _top_level_function_names(code: str) -> list[str]:
        """提取代码中全部顶层函数名（含辅助函数），供入口方法回退查找。"""
        try:
            tree = ast.parse(code, "<workflow-code>")
        except SyntaxError:
            return []
        return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]

    @staticmethod
    def _build_builtins(builtins_module) -> dict:
        """构造 exec 命名空间用的 __builtins__：白名单 + 受控 __import__
        （支持 import 导包但拦截危险模块）——始终开启，无开关。"""
        allow = dict(CodeNodeExecutor._ALLOWED_BUILTINS)
        allow["__import__"] = CodeNodeExecutor._safe_import
        return allow

    @staticmethod
    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        """受控 import：黑名单模块拦截，其余模块放行。"""
        base = name.split(".")[0]
        if base in CodeNodeExecutor._BLOCKED_IMPORTS:
            raise ImportError(f"模块 {base} 被沙箱禁止导入")
        return __import__(name, globals, locals, fromlist, level)

    @staticmethod
    def _coerce_constant(value: Any, type_: Any) -> Any:
        """自定义值转换（字符串 → 目标类型）：
        number 转 int/float；boolean 转 True/False；array/object 按 JSON 解析；
        解析失败或本身已是目标类型则原样透传。"""
        if value is None or isinstance(value, bool):
            return value
        t = str(type_ or "string").lower()
        if t == "number":
            if isinstance(value, (int, float)):
                return value
            s = str(value).strip()
            try:
                return int(s) if re.fullmatch(r"[+-]?\d+", s) else float(s)
            except ValueError:
                return value
        if t == "boolean":
            return str(value).strip().lower() in ("true", "1", "yes", "y", "on")
        if t in ("array", "object") and isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return value
        return value

    @staticmethod
    def _validate_output(value: Any) -> None:
        """递归校验返回值：字符串 ≤200KB / 数组 ≤100 元素。"""
        def _check(v: Any) -> None:
            if isinstance(v, str) and len(v.encode("utf-8")) > 200 * 1024:
                raise ValueError("输出 result 超过 200KB 限制")
            if isinstance(v, list):
                if len(v) > 100:
                    raise ValueError("输出 result 数组超过 100 元素限制")
                for item in v:
                    _check(item)
            elif isinstance(v, dict):
                for item in v.values():
                    _check(item)
        _check(value)

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
    """DOC_EXTRACTOR：文档文本提取（PDF/Word/Excel/PPT/Markdown/HTML 等 11 种格式）。

    按扩展名用 Python 生态库解析：pypdf / python-docx / openpyxl / xlrd /
    python-pptx / 内置文本读取；老格式 .doc/.ppt 走 LibreOffice headless 转换
    （未安装时报清晰错误）。不做 OCR 与分页。fileVariable 支持引用
    （fileId / 本地路径），兼容前端 {{inputs.xx}} 或裸 n_start.xx 写法。
    """

    node_type = "DOC_EXTRACTOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        ref = str(self.cfg("fileVariable") or "")
        # 引用优先：前端 {{inputs.doc}} / 裸 n_start.doc 都兼容（resolve 入口统一剥壳）；
        # 解析不到时回退字面量原样使用（用户可直接填 fileId / 本地路径）
        file_ref = ctx.resolve(ref)
        if file_ref is None:
            file_ref = ref
        # 容错：START 文件字段可能存上传返回对象 {fileId,...} 或数组（FILE_LIST），提取 fileId
        if isinstance(file_ref, dict):
            file_ref = file_ref.get("fileId") or file_ref.get("id") or file_ref.get("url")
        elif isinstance(file_ref, list):
            file_ref = file_ref[0] if file_ref else None
            if isinstance(file_ref, dict):
                file_ref = file_ref.get("fileId") or file_ref.get("id") or file_ref.get("url")
        if not file_ref:
            raise ValueError(f"文档提取节点未指定文件变量: {ref or '(空)'}")
        loader = getattr(self.runtime, "file_loader", None)
        if not callable(loader):
            raise ValueError("文件服务未接入（file_loader 未注册）")
        content, metadata = await loader(str(file_ref), self.cfg("supportedTypes"))
        output_var = self.cfg("outputVariable") or "content"
        # 只输出 outputVariable 一个键（与 REPLY/TEMPLATE 对齐，去掉冗余的 text 别名键）
        output = {output_var: content}
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
