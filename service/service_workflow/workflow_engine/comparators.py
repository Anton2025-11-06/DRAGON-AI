# -*- coding: utf-8 -*-
"""条件比较器（对照前端 types.ts CompareOperator / ListCompareOperator 全集实现，
算法参照 MaxKB application/flow/compare/ 纯函数风格）。

所有比较器统一签名：compare(source_value, target_value) -> bool
"""
from __future__ import annotations

import re
from typing import Any, Callable


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _num(v: Any) -> float | None:
    """数值比较的宽容转换：字符串数字可比较，否则 None（→False）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iterable(v: Any) -> list | None:
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return None


# ==================== 比较器实现 ====================

def cmp_equals(a, b) -> bool:
    if _num(a) is not None and _num(b) is not None:
        return _num(a) == _num(b)
    return _to_str(a) == _to_str(b)


def cmp_not_equals(a, b) -> bool:
    return not cmp_equals(a, b)


def cmp_contains(a, b) -> bool:
    arr = _iterable(a)
    if arr is not None:
        return any(cmp_equals(x, b) for x in arr)
    return _to_str(b) in _to_str(a)


def cmp_not_contains(a, b) -> bool:
    return not cmp_contains(a, b)


def cmp_starts_with(a, b) -> bool:
    return _to_str(a).startswith(_to_str(b))


def cmp_ends_with(a, b) -> bool:
    return _to_str(a).endswith(_to_str(b))


def _ordered(a, b):
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return na, nb
    return _to_str(a), _to_str(b)


def cmp_greater_than(a, b) -> bool:
    try:
        x, y = _ordered(a, b)
        return x > y
    except TypeError:
        return False


def cmp_greater_or_equal(a, b) -> bool:
    try:
        x, y = _ordered(a, b)
        return x >= y
    except TypeError:
        return False


def cmp_less_than(a, b) -> bool:
    try:
        x, y = _ordered(a, b)
        return x < y
    except TypeError:
        return False


def cmp_less_or_equal(a, b) -> bool:
    try:
        x, y = _ordered(a, b)
        return x <= y
    except TypeError:
        return False


def cmp_in(a, b) -> bool:
    """a ∈ b（b 是列表/逗号分隔字符串）。"""
    arr = _iterable(b)
    if arr is None and isinstance(b, str):
        arr = [s.strip() for s in b.split(",") if s.strip()]
    if arr is None:
        return cmp_equals(a, b)
    return any(cmp_equals(a, x) for x in arr)


def cmp_not_in(a, b) -> bool:
    return not cmp_in(a, b)


def cmp_is_empty(a, _b=None) -> bool:
    if a is None:
        return True
    if isinstance(a, str):
        return a.strip() == ""
    if isinstance(a, (list, tuple, dict, set)):
        return len(a) == 0
    return False


def cmp_is_not_empty(a, _b=None) -> bool:
    return not cmp_is_empty(a)


def cmp_is_null(a, _b=None) -> bool:
    return a is None


def cmp_is_not_null(a, _b=None) -> bool:
    return a is not None


def cmp_matches_regex(a, b) -> bool:
    try:
        return re.search(_to_str(b), _to_str(a)) is not None
    except re.error:
        return False


def cmp_matches(a, b) -> bool:
    """ListCompareOperator 中的 MATCHES（同正则）。"""
    return cmp_matches_regex(a, b)


# ==================== 注册表 ====================

COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "EQUALS": cmp_equals,
    "NOT_EQUALS": cmp_not_equals,
    "CONTAINS": cmp_contains,
    "NOT_CONTAINS": cmp_not_contains,
    "STARTS_WITH": cmp_starts_with,
    "ENDS_WITH": cmp_ends_with,
    "GREATER_THAN": cmp_greater_than,
    "GREATER_OR_EQUAL": cmp_greater_or_equal,
    "LESS_THAN": cmp_less_than,
    "LESS_OR_EQUAL": cmp_less_or_equal,
    "IN": cmp_in,
    "NOT_IN": cmp_not_in,
    "IS_EMPTY": cmp_is_empty,
    "IS_NOT_EMPTY": cmp_is_not_empty,
    "IS_NULL": cmp_is_null,
    "IS_NOT_NULL": cmp_is_not_null,
    "MATCHES_REGEX": cmp_matches_regex,
    "MATCHES": cmp_matches,
}


def compare(operator: str, source: Any, target: Any) -> bool:
    fn = COMPARATORS.get(operator)
    if fn is None:
        raise ValueError(f"不支持的比较运算符: {operator}")
    return fn(source, target)


def evaluate_conditions(conditions: list[dict], operator: str, ctx) -> bool:
    """评估条件列表（AND/OR 组合）。condition: {variable, operator, value, valueIsVariable?}"""
    if not conditions:
        return False
    results = []
    for cond in conditions:
        source = ctx.resolve(cond.get("variable", ""))
        if cond.get("valueIsVariable"):
            target = ctx.resolve(_to_str(cond.get("value")))
        else:
            target = cond.get("value")
        try:
            results.append(compare(cond.get("operator", "EQUALS"), source, target))
        except ValueError as e:
            raise ValueError(f"条件评估失败 [{cond.get('variable')}]: {e}") from e
    if (operator or "AND").upper() == "OR":
        return any(results)
    return all(results)


# ==================== 条件断点表达式（自由文本） ====================

# {{node.field}} / {{global.x}} 变量引用（复用 context 的引用名规则：不含大括号）
_EXPR_REF_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# 受限求值命名空间（条件断点表达式只允许纯比较/逻辑运算 + 少量安全内建）
_EXPR_BUILTINS = {
    "__builtins__": {},
    "len": len, "str": str, "int": int, "float": float,
    "abs": abs, "min": min, "max": max, "sum": sum,
}


def _normalize_js_operators(text: str) -> str:
    """前端帮助文案以 JS 语法为示例（=== / !== / && / ||），
    归一为等价 Python 运算符；true/false/null 同理。"""
    text = text.replace("===", "==").replace("!==", "!=")
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"\btrue\b", "True", text)
    text = re.sub(r"\bfalse\b", "False", text)
    text = re.sub(r"\bnull\b", "None", text)
    return text


def evaluate_expression(expr: str, ctx) -> bool:
    """条件断点表达式求值。

    - {{ref}} 引用先解析为 Python 字面量（repr），保证任意类型值（含含空格
      字符串/数字/列表）可直接参与比较，而非裸文本替换
    - 空表达式恒真（等价于无条件断点）
    - 求值失败（语法错/未知名）抛 ValueError，由调用方决定降级策略
    """
    if not expr or not expr.strip():
        return True

    def _sub(m: re.Match) -> str:
        return repr(ctx.resolve(m.group(1)))

    py = _EXPR_REF_RE.sub(_sub, expr)
    py = _normalize_js_operators(py)
    try:
        code = compile(py, "<breakpoint-condition>", "eval")
        return bool(eval(code, dict(_EXPR_BUILTINS)))  # noqa: S307
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"条件断点表达式求值失败 [{expr!r} -> {py!r}]: {e}") from e
