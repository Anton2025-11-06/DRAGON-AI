# -*- coding: utf-8 -*-
"""控制流节点：START / END / IF_ELSE / VARIABLE_ASSIGNER / VARIABLE_AGGREGOR。

配置结构对齐前端 types.ts：
- StartNodeConfig  {fields: InputField[]}
- EndNodeConfig    {outputs: OutputField[], answerTemplate, streaming, outputMode}
- IfElseNodeConfig {branches: ConditionBranch[]}
- VariableAssignerConfig  {assignments: Assignment[]}
- VariableAggregatorConfig {groups: AggregationGroup[]}
"""
from __future__ import annotations

import json
import re
from typing import Optional

from service.service_workflow.workflow_engine.comparators import evaluate_conditions
from service.service_workflow.workflow_engine.context import ExecutionContext, _dig
from service.service_workflow.workflow_engine.nodes.base import (
    BaseNodeExecutor, NodeResult, issue,
)


class StartNodeExecutor(BaseNodeExecutor):
    """START：校验输入字段 → 写入上下文（下游通过 {{startNodeId.字段名}} 引用）。"""

    node_type = "START"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        fields = self.cfg("fields") or []
        output = {}
        for f in fields:
            name = f.get("name")
            if not name:
                continue
            # 空值（None/空串）视为未填写，落入字段默认值；
            # 注意不能用 ctx.inputs.get(name, default)：key 存在（如 {query: ""}）时
            # .get 会直接返回空串，默认值永远不会生效
            value = ctx.inputs.get(name)
            if value is None or value == "":
                value = f.get("defaultValue")
            if value is None and f.get("required"):
                raise ValueError(f"缺少必填输入参数: {name}（{f.get('label') or name}）")
            output[name] = value
        # 兜底：未定义字段也透传（调试时直接填任意输入）
        for k, v in ctx.inputs.items():
            output.setdefault(k, v)
        return NodeResult(output=output)

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        fields = (node.data or {}).get("fields") or []
        if not fields:
            issues.append(issue("START_NO_FIELDS", "WARNING", "START 节点未定义输入字段", node,
                                suggestion="至少定义一个输入字段，否则输入参数无法被下游引用"))
        names = [f.get("name") for f in fields if f.get("name")]
        if len(names) != len(set(names)):
            issues.append(issue("START_DUP_FIELD", "ERROR", "START 节点输入字段名重复", node))
        return issues


class EndNodeExecutor(BaseNodeExecutor):
    """END：渲染输出变量（支持 {{引用}} 与字面量）→ 工作流 outputs。"""

    node_type = "END"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        outputs = {}
        for f in self.cfg("outputs") or []:
            name = f.get("name")
            if not name:
                continue
            value = f.get("value")
            if isinstance(value, str) and "{{" in value:
                # 纯单一引用 {{ref}} 保留原始类型（dict/list/数字），避免被字符串化；
                # 仅当包含模板文本时才走 render 字符串插值。
                # 引用名内不允许出现大括号，否则 {{a}}{{b}} 会被回溯误判成单一引用
                m = re.fullmatch(r"\s*\{\{\s*([^{}]+?)\s*\}\}\s*", value)
                if m:
                    outputs[name] = ctx.resolve(m.group(1))
                else:
                    # 多引用拼接：未执行分支的引用渲染为空串，不残留占位符
                    outputs[name] = ctx.render(value, keep_unresolved=False)
            else:
                outputs[name] = value
        # 回答模板（outputMode=TEMPLATE）
        template = self.cfg("answerTemplate")
        if template:
            outputs["answer"] = ctx.render(template, keep_unresolved=False)
        return NodeResult(output=outputs)

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        if not (node.data or {}).get("outputs"):
            issues.append(issue("END_NO_OUTPUTS", "WARNING", "END 节点未定义输出变量", node))
        return issues


class IfElseNodeExecutor(BaseNodeExecutor):
    """IF_ELSE：按分支顺序评估条件，第一个满足的分支 → branch_id 路由。

    分支端口 = branch:{branch.id}；ELSE 分支无条件命中；
    无任何分支命中且存在 ELSE → ELSE；都没有 → 终止该路径。
    """

    node_type = "IF_ELSE"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        branches = self.cfg("branches") or []
        matched: Optional[dict] = None
        for br in branches:
            btype = (br.get("type") or "IF").upper()
            if btype == "ELSE":
                matched = br
                continue
            conditions = br.get("conditions") or []
            if not conditions and btype == "IF":
                continue
            try:
                ok = evaluate_conditions(conditions, br.get("operator") or "AND", ctx)
            except ValueError as e:
                raise ValueError(f"分支「{br.get('label')}」条件评估失败: {e}") from e
            if ok:
                matched = br
                break
        if matched is None:
            return NodeResult(output={"branch": None}, branch_id=None)
        return NodeResult(
            output={"branch": matched.get("id"), "branchLabel": matched.get("label")},
            branch_id=str(matched.get("id", "")),
        )

    @staticmethod
    def validate_node(node, graph) -> list:
        issues = []
        branches = (node.data or {}).get("branches") or []
        has_if = any((b.get("type") or "IF").upper() in ("IF", "ELIF") for b in branches)
        if branches and not has_if:
            issues.append(issue("IF_NO_CONDITION", "ERROR", "IF_ELSE 节点缺少 IF 条件分支", node))
        has_else = any((b.get("type") or "").upper() == "ELSE" for b in branches)
        if not has_else:
            issues.append(issue("IF_NO_ELSE", "SUGGESTION", "建议添加 ELSE 分支兜底", node))
        ids = [str(b.get("id")) for b in branches if b.get("id")]
        if len(ids) != len(set(ids)):
            issues.append(issue("IF_DUP_BRANCH", "ERROR", "分支 ID 重复", node))
        return issues


class VariableAssignerNodeExecutor(BaseNodeExecutor):
    """VARIABLE_ASSIGNER：写全局变量。

    EXPRESSION 走安全表达式子集：变量引用（含 JSON 点路径/数组下标取值，如
    {{nodes.xx.output}}.key / {{ref}}[0].name / nodes.xx.output.key / value.key / value[0]）、
    len/str/int/float(value) 包装、value.upper/lower/strip/title() 字符串操作。
    """

    node_type = "VARIABLE_ASSIGNER"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        output = {}
        for a in self.cfg("assignments") or []:
            name = a.get("variableName")
            if not name:
                continue
            atype = (a.get("type") or "LITERAL").upper()
            value = a.get("value")
            if atype == "VARIABLE":
                value = ctx.resolve(str(value or ""))
            elif atype == "EXPRESSION":
                value = self._safe_transform(ctx, value, a.get("transformExpression"))
            if isinstance(value, str):
                value = self._coerce(value, a.get("variableType"))
            if a.get("overwrite", True) or name not in ctx.global_vars:
                ctx.global_vars[name] = value
            output[name] = value
        return NodeResult(output=output)

    def _safe_transform(self, ctx: ExecutionContext, value, expression: Optional[str]):
        base = ctx.resolve(str(value or "")) if isinstance(value, str) and "{{" not in str(value) \
            else ctx.render(value)
        if not expression:
            return base
        expr = expression.strip()
        # ① 引用表达式（含 JSON 点路径/数组下标取值）：
        #    {{nodes.xx.output.key}} / {{nodes.xx.output}}.key / {{ref}}[0].name
        m = re.fullmatch(r"\{\{\s*([^{}]+?)\s*\}\}([\w.\u4e00-\u9fa5\[\]]*)", expr)
        if m:
            resolved = ctx.resolve(m.group(1))
            path = m.group(2)
            if path.startswith("."):
                path = path[1:]
            return _dig(resolved, path) if path and resolved is not None else resolved
        # ② 白名单包装：len(value) / str(value) / int(value) / float(value)
        m = re.fullmatch(r"(len|str|int|float)\(\s*value\s*\)", expr)
        if m:
            try:
                return {"len": len, "str": str, "int": int, "float": float}[m.group(1)](base)
            except (TypeError, ValueError):
                return base
        # ③ value 操作：字符串方法或 JSON 路径/数组下标提取
        if expr.startswith("value.") or expr.startswith("value["):
            op = expr[len("value"):]  # '.upper()' / '.metadata.name' / '[0].name'
            m = re.fullmatch(r"\.(upper|lower|strip|title)\(\)", op)
            if m and isinstance(base, str):
                return getattr(base, m.group(1))()
            # 方法形态（带括号）未命中 → 明确报错，避免误入路径解析
            if op.endswith("()"):
                raise ValueError(
                    f"不支持的表达式: {expression}（value 字符串方法仅支持 upper/lower/strip/title）")
            # 路径提取：value.metadata.name / value[0].name（base 为 dict/list，或 render 后的 JSON 字符串）
            path = op[1:] if op.startswith(".") else op
            target = base
            if isinstance(target, str):
                try:
                    target = json.loads(target)
                except (ValueError, json.JSONDecodeError):
                    target = None
            if isinstance(target, (dict, list)):
                return _dig(target, path)
            raise ValueError(
                f"不支持的表达式: {expression}（value 不是对象，无法按路径 {path} 取值）")
        # ④ 裸引用（含点路径）：nodes.xx.output.key / inputs.xx / global.xx / n_start.doc
        resolved = ctx.resolve(expr)
        if resolved is not None:
            return resolved
        raise ValueError(
            f"不支持的表达式: {expression}（支持 {{节点引用}}(可带 .key / [index] 路径)、"
            f"len/str/int/float(value)、value.upper/lower/strip()、value.点路径 / [index]）")

    @staticmethod
    def _coerce(value: str, vtype: Optional[str]):
        if not isinstance(value, str) or not vtype:
            return value
        try:
            if vtype == "number":
                return float(value) if "." in value else int(value)
            if vtype == "boolean":
                return value.lower() in ("1", "true", "yes")
            if vtype == "array":
                return json.loads(value)
            if vtype == "object":
                return json.loads(value)
        except (ValueError, json.JSONDecodeError):
            return value
        return value


class VariableAggregatorNodeExecutor(BaseNodeExecutor):
    """VARIABLE_AGGREGATOR：合并多分支变量（策略：FIRST_NON_NULL/LAST_NON_NULL/MERGE_TO_ARRAY/MERGE_OBJECTS）。"""

    node_type = "VARIABLE_AGGREGATOR"

    async def execute(self, ctx: ExecutionContext) -> NodeResult:
        output = {}
        for g in self.cfg("groups") or []:
            var_name = g.get("outputVariable")
            sources = g.get("sourceVariables") or []
            strategy = (g.get("strategy") or "FIRST_NON_NULL").upper()
            values = []
            for ref in sources:
                v = ctx.resolve(str(ref or "")) if ref else None
                if v is not None:
                    values.append(v)
            if strategy == "FIRST_NON_NULL":
                output[var_name] = values[0] if values else None
            elif strategy == "LAST_NON_NULL":
                output[var_name] = values[-1] if values else None
            elif strategy == "MERGE_TO_ARRAY":
                output[var_name] = values
            elif strategy == "MERGE_OBJECTS":
                merged = {}
                for v in values:
                    if isinstance(v, dict):
                        merged.update(v)
                output[var_name] = merged
            else:
                output[var_name] = values[0] if values else None
        return NodeResult(output=output)
