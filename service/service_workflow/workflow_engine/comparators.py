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


def is_true(a, b=None) -> bool:
    return a


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
    "IS_TRUE": is_true,
    "IS_FALSE": is_true
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
        source = ctx.resolve_ref(cond.get("variable", ""))
        if cond.get("valueIsVariable"):
            target = ctx.resolve_ref(_to_str(cond.get("value")))
        else:
            target = cond.get("value")
        try:
            results.append(compare(cond.get("operator", "EQUALS"), source, target))
        except ValueError as e:
            raise ValueError(f"条件评估失败 [{cond.get('variable')}]: {e}") from e
    if (operator or "AND").upper() == "OR":
        return any(results)
    return all(results)

# ==================== 条件断点表达式（已废弃） ====================
#
# 原先这里是一整套「自由文本表达式」求值（{{ref}} 占位替换 + JS 运算符归一 + eval），
# 唯一的调用方是断点条件。断点功能整体下线后（见 docs/workflow-approval-memory.md）
# 它已成死代码，连带删除：留着一个 eval 入口只会被新功能误用。
# 分支条件请走 evaluate_conditions（结构化 operator，无 eval）。
