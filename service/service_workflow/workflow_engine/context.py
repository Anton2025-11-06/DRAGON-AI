# -*- coding: utf-8 -*-
"""执行上下文与变量渲染（参照 MaxKB WorkflowManage 三级上下文 + reset_prompt 算法重写）。

作用域（从高到低）：
1. global  —— 全局变量（工作流级， VARIABLE_ASSIGNER 写入）
2. node    —— 节点输出上下文 {node_id: {var: value}}（含 START 输入）
3. local   —— 模板类节点渲染期注入的临时变量

变量引用语法（对齐前端 types.ts 注释）：{{nodeName.variableName}}
解析顺序：node_id 精确匹配 → node label 匹配 → global 作用域。
支持嵌套路径与下标（JSONPath 风格子集）：{{node.obj.field}} / {{node.arr[0].name}}
/ {{node.arr.0}}；路径中途是 JSON 字符串（如大模型输出的 JSON 文本）时会自动解析后继续下钻。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from service.service_workflow.workflow_engine.graph import WorkflowGraph

# {{xxx.yyy[.zzz]}} —— 允许字母/数字/下划线/中文/点号
VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_\u4e00-\u9fa5.\-\[\]0-9]+)\s*\}\}")

# 整串只有一个 {{引用}}（内部不再含花括号）：用于区分「单引用保类型」与「多引用拼文本」
_SINGLE_VAR_PATTERN = re.compile(r"\s*\{\{\s*([^{}]+?)\s*\}\}\s*")


class VariableNotFound(Exception):
    """strict 模式下变量引用无法解析"""


def file_url(value: Any) -> Optional[str]:
    """文件变量值 → 可访问 URL。

    开始节点的文件参数存的是上传接口返回的结果（{url,fileName,name,size,expiresAt...}），
    FILE_LIST 则是该对象的数组；也兼容用户直接填 URL 字符串或上游产出 url 的节点输出。
    取不到地址返回 None（由调用方决定报错还是跳过）。
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def file_urls(value: Any) -> Optional[list[str]]:
    """文件变量值 → URL 列表（非文件结构返回 None，交由调用方保持原渲染）。

    只对「对象 / 全为带 url 的对象数组」生效：普通文本数组等仍按原样渲染，不静默拼接。
    """
    items = value if isinstance(value, (list, tuple)) else [value]
    if not items:
        return None
    urls = []
    for item in items:
        if not (isinstance(item, dict) and item.get("url")):
            return None
        url = file_url(item)
        if not url:
            return None
        urls.append(url)
    return urls


class ExecutionContext:
    """单次执行的上下文容器（非线程安全；引擎在单 asyncio 任务内串行访问，
    并行分支通过引擎拷贝快照传递只读视图）。"""

    def __init__(self, graph: WorkflowGraph, inputs: Optional[dict] = None):
        self.graph = graph
        self.inputs: dict = dict(inputs or {})
        self.global_vars: dict = {}          # VARIABLE_ASSIGNER / 调试 API 写入
        self.node_outputs: dict = {}          # {node_id: {var: value}}
        self.scopes: list[dict] = []          # 局部作用域栈（TEMPLATE 渲染变量）
        self.executed: list[str] = []         # 已执行节点顺序（nodeStates.order）

    # ==================== 写入 ====================

    def set_node_output(self, node_id: str, output: dict) -> None:
        """节点完成后写输出上下文（合并语义，流式节点可多次更新）。"""
        self.node_outputs[node_id] = {**self.node_outputs.get(node_id, {}), **(output or {})}

    def push_scope(self, variables: dict) -> None:
        self.scopes.append(variables)

    def pop_scope(self) -> Optional[dict]:
        return self.scopes.pop() if self.scopes else None

    # ==================== 读取 ====================

    def get_node_output(self, node_id: str) -> dict:
        return self.node_outputs.get(node_id, {})

    # ==================== 变量解析 ====================

    def resolve(self, ref: str) -> Any:
        """解析单个变量引用（兼容 {{}} 包裹与裸引用两种写法）。找不到时返回 None（非 strict 场景）。

        支持的引用格式（对齐前端 VariableSelector 生成的完整作用域前缀）：
        - {{inputs.xxx}}：工作流输入（START 输入字段，支持嵌套路径）
        - {{nodes.{nodeId}.{var}[.path]}}：节点输出（nodeId 支持 label 别名）
        - {{global.xxx}}：全局变量（VARIABLE_ASSIGNER 写入）
        - {{nodeId.var}} / {{label.var}}：裸节点引用（历史格式/引擎直用，保持兼容）

        前端 VariableInput 存入 {{node.var}} 模板串、引擎直用与旧数据存裸引用，
        因此入口统一剥壳（REPLY/END 各自剥壳为历史特例，幂等保留）。
        """
        # 兼容 {{...}} 包裹格式：剥掉首尾大括号后按裸引用解析
        ref = (ref or "").strip()
        if ref.startswith("{{") and ref.endswith("}}"):
            ref = ref[2:-2].strip()
        # 前端生成格式：inputs./nodes. 作用域前缀
        if ref.startswith("inputs."):
            return _dig(self.inputs, ref[len("inputs."):])
        if ref.startswith("nodes."):
            rest = ref[len("nodes."):]
            node_id, _, path = rest.partition(".")
            out = self.node_outputs.get(node_id)
            if out is None:
                node = self.graph.resolve_node(node_id)
                out = self._value_of_node(node.id) if node is not None else None
            return out if (not path and out is not None) else _dig(out, path)
        node = self.graph.resolve_node(ref)
        if node is not None:
            return self._value_of_node(node.id)
        # 已写入的节点输出兜底（快照恢复/图变更场景）
        if ref in self.node_outputs:
            return self.node_outputs[ref]
        # 作用域栈（栈顶优先）：模板类节点的临时变量
        for scope in reversed(self.scopes):
            if ref in scope:
                return scope[ref]
        # global 作用域（支持 global.xxx 前缀与裸名）
        if ref.startswith("global."):
            return _dig(self.global_vars, ref[len("global."):])
        if ref in self.global_vars:
            return self.global_vars[ref]
        # 直接是输入参数名
        if ref in self.inputs:
            return self.inputs[ref]
        # 带路径的引用：xxx.yyy.zzz —— xxx 是节点，后为路径
        head, _, rest = ref.partition(".")
        if rest:
            node = self.graph.resolve_node(head)
            if node is not None:
                return _dig(self._value_of_node(node.id), rest)
            if head in self.node_outputs:
                return _dig(self.node_outputs[head], rest)
            if head in self.global_vars:
                return _dig(self.global_vars[head], rest)
        return None

    def _value_of_node(self, node_id: str) -> Any:
        out = self.node_outputs.get(node_id)
        return out

    def resolve_or_raw(self, ref: str, strict: bool = False) -> Any:
        value = self.resolve(ref)
        if value is None and strict:
            raise VariableNotFound(f"变量引用无法解析: {ref}")
        return value

    def resolve_ref(self, ref: Any) -> Any:
        """解析「引用参数」类配置项（REPLY 引用参数 / 输入变量 / 文件变量…）。

        前端这类字段的输入框是 VariableInput（自由文本 + 变量标签混排），用户可以在
        同一个字段里插入多个 {{引用}}，因此不能按单引用剥壳处理：
        - 单个 {{a}} 或裸引用 a.b：走 resolve，保留解析值的原始类型（对象/数组不转字符串）；
        - 多个引用（{{a}}{{b}}）或引用与普通文本混排：按模板渲染成拼接后的文本，
          未解析到的引用渲染为空串，整体为空时返回 None（由调用方报「无法解析」）。
        早先的实现只剥首尾花括号，多引用会拼成一个坏引用（nodes.a.out1nodes.a.out2）
        并恒定解析失败，就是本方法要解决的场景。
        """
        text = ref if isinstance(ref, str) else ("" if ref is None else str(ref))
        text = text.strip()
        if not text:
            return None
        # 不含大括号（裸引用/字面量）或整体就是一个 {{...}}：单引用，保留类型
        if "{{" not in text or _SINGLE_VAR_PATTERN.fullmatch(text):
            return self.resolve(text)
        rendered = self._render_string(text, strict=False, keep_unresolved=False)
        return rendered if rendered.strip() else None

    # ==================== 模板渲染 ====================

    def render(self, template: Any, strict: bool = False,
               keep_unresolved: bool = True) -> Any:
        """渲染模板中的 {{引用}}。递归处理 dict/list；非字符串原样返回。

        strict=True 时未解析引用抛错（TEMPLATE 节点 strictMode / 发布校验）；
        keep_unresolved=True 保留原文占位（默认，便于调试定位）；
        keep_unresolved=False 时未解析引用渲染为空串（END 汇聚多分支场景：
        未执行分支的引用不应残留在最终输出里）。
        """
        return self._render_value(template, strict, keep_unresolved)

    def _render_value(self, value: Any, strict: bool, keep_unresolved: bool = True) -> Any:
        if isinstance(value, str):
            return self._render_string(value, strict, keep_unresolved)
        if isinstance(value, dict):
            return {k: self._render_value(v, strict, keep_unresolved) for k, v in value.items()}
        if isinstance(value, list):
            return [self._render_value(v, strict, keep_unresolved) for v in value]
        return value

    def _render_string(self, text: str, strict: bool, keep_unresolved: bool = True) -> str:
        def _sub(m: re.Match) -> str:
            ref = m.group(1)
            value = self.resolve(ref)
            if value is None:
                if strict:
                    raise VariableNotFound(f"变量引用无法解析: {ref}")
                return m.group(0) if keep_unresolved else ""  # 保留原占位或渲染为空串
            if isinstance(value, (dict, list)):
                # 文件变量（上传接口返回对象/对象数组）渲染成 URL，不渲染整个对象
                urls = file_urls(value)
                if urls:
                    return "\n".join(urls)
                return json.dumps(value, ensure_ascii=False)
            return "" if value is None else str(value)

        return VAR_PATTERN.sub(_sub, text)

    def extract_refs(self, text: str) -> list[str]:
        """提取模板中的全部变量引用（依赖分析/校验用）。"""
        return [m.group(1) for m in VAR_PATTERN.finditer(text or "")]

    def unresolved_refs(self, text: str) -> list[str]:
        """模板中当前取不到值的引用（报错文案用来定位到具体哪一段没解析上）。"""
        return [r for r in self.extract_refs(text) if self.resolve(r) is None]


def parse_json_if_embedded(value: Any) -> Any:
    """字符串值形如 JSON 对象/数组时解析成结构，否则原样返回。

    大模型等文本节点的输出是字符串，结构化输出/JSON 回复很常见；下游按
    {{大模型.output.data[0].name}} 再次提取时必须先把它当 JSON 解开，
    否则点路径/下标永远取不到值。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001  非严格 JSON（单引号/尾逗号等）按原文处理
        return value


def _dig(value: Any, path: str) -> Any:
    """按路径取嵌套值（JSONPath 风格子集），支持点分 key 与数组下标
    （方括号/点下标/负下标）：a.b[0].c / a.b.0.c / a[-1] / [0] / items[2].name。

    中途遇到 JSON 字符串会先解析再继续下钻（见 parse_json_if_embedded）。
    """
    if value is None:
        return None
    tokens = [t for t in re.split(r"[.\[\]]+", (path or "").strip()) if t]
    if not tokens:
        return value
    for part in tokens:
        value = parse_json_if_embedded(value)
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
        if value is None:
            return None
    return value


def split_path(path: str) -> list:
    """路径切成 token 序列（`_dig`/`set_dig` 共用，切法必须一致否则读写不闭环）。"""
    return [t for t in re.split(r"[.\[\]]+", (path or "").strip()) if t]


def set_dig(root: Any, path: str, value: Any) -> tuple:
    """按路径写嵌套值（`_dig` 的写侧）：返回 (写入后的容器, 是否写成功)。

    审批人只改大对象里的某个字段时走这里：不然得把整段 JSON 重录入，既易错
    又把没改的字段一起覆盖掉。三条约定：
    - 空路径 = 直接替换整值（等价于旧的回写口径）；
    - 中途是 JSON 字符串（大模型结构化输出很常见）先解开、改完再序列化回去：
      读侧本来就是「按需解开」，写侧跟同一口径才不会把结构化输出压成普通文本；
    - 路径不存在/中间撞标量/数组越界 → 放弃回写并返回 (原值, False)，
      宁可不改也不凭空造一层结构（清单里的行本来就是从真实存在的值取出来的）。
    """
    tokens = split_path(path)
    if not tokens:
        return value, True
    return _set_tokens(root, tokens, value)


def _set_tokens(node: Any, tokens: list, value: Any) -> tuple[Any, bool]:
    head, rest = tokens[0], tokens[1:]
    original = node
    node = parse_json_if_embedded(node)
    was_json = node is not original and isinstance(node, (dict, list))
    key: Any = head
    if isinstance(node, dict):
        if head not in node:
            return original, False
    elif isinstance(node, list):
        try:
            key = int(head)
        except ValueError:
            return original, False
        if not -len(node) <= key < len(node):
            return original, False
    else:
        return original, False
    if rest:
        child, ok = _set_tokens(node[key], rest, value)
        if not ok:
            return original, False
        node[key] = child
    else:
        node[key] = value
    return (json.dumps(node, ensure_ascii=False) if was_json else node), True
