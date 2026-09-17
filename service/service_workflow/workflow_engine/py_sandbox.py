# -*- coding: utf-8 -*-
"""Python 受限执行沙箱（CODE 节点 / 动态函数工具 / 工具节点的唯一实现）。

放在 workflow_engine 根目录（不在 nodes 包内）：nodes 侧的执行器与 services 侧的
工具服务都要用它，而 nodes/__init__.py 会连带 import 全部节点模块，从 services 里
import 节点模块容易绕成循环依赖。

口径统一（原散落在 CodeNodeExecutor 与 ToolService 两处、行为不一致）：
- 白名单 builtins + 受控 __import__（危险模块黑名单拦截），禁裸 exec/eval/open
- 入口方法：main 优先，其次 run（动态函数工具历史入口），再回退代码中最后定义的顶层
  函数（不支持顶层 return）
- 参数：{name, type, required, sourceType(REFERENCE|CONSTANT), sourceVariable, value}
  CONSTANT 走 coerce_constant 转型，REFERENCE 走调用方注入的解析函数取值
- 执行：线程池 + asyncio 超时（进程内 exec 无法强隔离，二期接隔离容器）
- 输出：validate_output 递归限制字符串 ≤200KB / 数组 ≤100 元素
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# 输出体积上限（超出视为节点失败，避免把上百 KB 结果写进上下文与执行记录）
MAX_OUTPUT_STR_BYTES = 200 * 1024
MAX_OUTPUT_LIST_ITEMS = 100

DEFAULT_TIMEOUT_MS = 30000

thread_pool = ThreadPoolExecutor(max_workers=200)

ALLOWED_BUILTINS: dict[str, Any] = {
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
BLOCKED_IMPORTS: frozenset[str] = frozenset({
    # 系统 / 进程 / IO
    "os", "sys", "shutil", "pathlib", "glob", "tempfile", "subprocess",
    "multiprocessing", "threading", "_thread", "ctypes", "signal",
    "resource", "pwd", "grp", "platform", "gc", "tracemalloc",
    "faulthandler", "pty", "termios", "tty", "winreg", "msvcrt",
    # 网络
    "urllib", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "ssl", "asyncio",
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


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """受控 import：黑名单模块拦截，其余模块放行。"""
    base = name.split(".")[0]
    if base in BLOCKED_IMPORTS:
        raise ImportError(f"模块 {base} 被沙箱禁止导入")
    return __import__(name, globals, locals, fromlist, level)


def build_builtins() -> dict:
    """构造 exec 命名空间用的 __builtins__：白名单 + 受控 __import__——始终开启，无开关。"""
    allow = dict(ALLOWED_BUILTINS)
    allow["__import__"] = safe_import
    return allow


def top_level_function_names(code: str) -> list[str]:
    """提取代码中全部顶层函数名（按定义顺序），供入口方法回退查找。"""
    try:
        tree = ast.parse(code, "<py-sandbox>")
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def compile_check(code: str) -> None:
    """入库前的编译校验：语法错误抛 ValueError（带行号），不执行任何用户代码。"""
    if not code or not code.strip():
        raise ValueError("代码内容不能为空")
    try:
        compile(code, "<py-sandbox>", "exec")
    except SyntaxError as e:
        raise ValueError(f"源码语法错误: 第{e.lineno}行 {e.msg}") from e


def coerce_constant(value: Any, type_: Any) -> Any:
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


def validate_output(value: Any) -> None:
    """递归校验返回值：字符串 ≤200KB / 数组 ≤100 元素。"""

    def _check(v: Any) -> None:
        if isinstance(v, str) and len(v.encode("utf-8")) > MAX_OUTPUT_STR_BYTES:
            raise ValueError(f"输出 result 超过 {MAX_OUTPUT_STR_BYTES // 1024}KB 限制")
        if isinstance(v, list):
            if len(v) > MAX_OUTPUT_LIST_ITEMS:
                raise ValueError(f"输出 result 数组超过 {MAX_OUTPUT_LIST_ITEMS} 元素限制")
            for item in v:
                _check(item)
        elif isinstance(v, dict):
            for item in v.values():
                _check(item)

    _check(value)


def resolve_kwargs(inputs: list, resolve_ref: Callable[[str], Any]) -> dict[str, Any]:
    """参数定义 → 调用入参 dict。

    inputs 每项：name / type / required / sourceType（REFERENCE 引用参数 | CONSTANT 自定义值）
    - CONSTANT：coerce_constant 按声明类型转换
    - REFERENCE：经 resolve_ref（一般是 ExecutionContext.resolve）取上游变量
    - 必填参数取到 None 视为配置/数据问题，直接报错而不是静默传 None
    未填参数名的行是画布上「添加参数」后的待填状态，跳过即可（后端兜底，前端不丢行）。
    """
    kwargs: dict[str, Any] = {}
    for item in inputs or []:
        name = item.get("name")
        if not name:
            continue
        source_type = (item.get("sourceType") or "REFERENCE").upper()
        if source_type == "CONSTANT":
            value = coerce_constant(item.get("value"), item.get("type"))
        else:
            ref = item.get("sourceVariable")
            value = resolve_ref(str(ref)) if ref else None
        if value is None and item.get("required"):
            raise ValueError(f"缺少必填参数: {name}")
        kwargs[name] = value
    return kwargs


def resolve_entry(namespace: dict, entry: str, top_funcs: list[str]):
    """入口函数查找：指定方法名 → main → run → 最后定义的顶层函数。

    run 是动态函数工具的历史入口名，排在 main 之后：同一段代码登记成工具或
    直接放进代码节点，找到的入口必须一致，否则「代码节点能跑」不成立。
    """
    wanted = (entry or "main").strip() or "main"
    fn = namespace.get(wanted)
    if callable(fn):
        return fn, wanted
    if wanted == "main":
        fn = namespace.get("run")
        if callable(fn):
            return fn, "run"
        # 没有 main/run：参照 MaxKB 取最后定义的顶层函数，
        # 使 def process(...) 这类代码无需填写方法名也能直接运行
        fallback = next((f for f in reversed(top_funcs) if not f.startswith("_")), None)
        if fallback and callable(namespace.get(fallback)):
            return namespace.get(fallback), fallback
    return None, wanted


def run_sandbox(code: str, kwargs: dict, entry: str = "main") -> Any:
    """同步执行（调用方负责放线程池 + 超时）：受限命名空间内跑用户函数，返回其返回值。"""
    namespace: dict[str, Any] = {"__builtins__": build_builtins()}
    top_funcs = top_level_function_names(code)
    try:
        exec(compile(code, "<py-sandbox>", "exec"), namespace)  # noqa: S102
    except SyntaxError as e:  # noqa: BLE001
        raise ValueError(
            f"代码语法错误: {e}（书写规范：import + def 方法，返回值在入口方法内 return）"
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"代码执行错误: {e}") from e
    fn, entry_name = resolve_entry(namespace, entry, top_funcs)
    if fn is None:
        raise ValueError(f"未找到方法 {entry_name}()，请检查代码定义（默认 main，或最后定义的顶层函数）")
    try:
        return fn(**kwargs)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{entry_name}() 执行错误: {e}") from e



async def run_code(code: str, kwargs: dict, *, entry: str = "main",
                   timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Any:
    """异步入口：线程池执行 + 超时守卫，返回用户函数返回值（未做体积校验）。"""
    return await asyncio.wait_for(
        asyncio.get_running_loop().run_in_executor(thread_pool, run_sandbox, code, kwargs, entry),
        timeout=max(float(timeout_ms or DEFAULT_TIMEOUT_MS), 1.0) / 1000,
    )
