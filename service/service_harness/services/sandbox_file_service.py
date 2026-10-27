# -*- coding: utf-8 -*-
"""harness 沙箱文件代理：把每用户的 OpenSandbox 工作区代理成文件浏览器四口。

与旧 demo（service_workflow 的 sandbox_service，本地目录版）保持同一套响应
形状（files / file / upload / download），前端切换只换 URL 不换结构。

执行通道（agentscope 官方文档确认的口径，v2.0.9）：
- ``workspace.get_backend()`` 拿到绑定沙箱的执行后端；
- ``Bash(backend=...)`` 在沙箱里执行 shell——镜像契约必含 python3 / tar /
  base64（agentscope 文件工具自身就靠它们），元信息用 python3 单行输出
  JSON、路径经环境变量传入（杜绝 shell 注入）；
- 上传走 base64-over-exec 分块（绕开 Linux 单命令行参数 ~128KiB 的上限）；
  下载直接走 backend.read_file 取原始字节——Bash 工具回传会截断大段 stdout，
  二进制的 base64 被切成非 4 倍长度就报 Incorrect padding（大文件必现），
  read_file 是后端专用文件 API、不经 shell、整份返回；目录先在沙箱内 tar.gz
  落临时文件再 read_file。

workspace 生命周期（每请求临时 attach，作用域结束即释放）：
- workspace 由 workspace_factory.workspace_scope 经临时 manager 分配：
  assign_workspace_id（PER_USER 幂等，与 arq 轮次指向同一只沙箱）+ get_workspace
  （内部已 initialize，沙箱还在时走 reattach+resume 快路径）；
- 本服务不做进程级缓存；四口各自 `async with _sandbox_entry(...)`，作用域退出由
  manager.__aexit__ close_all（pause 本轮沙箱、文件系统保留，下次请求 reattach）；
- PER_USER 下 session_id 只是 manager 定位入参、不参与 workspace 归属，
  文件浏览无会话上下文时留空即可。
"""
from __future__ import annotations

import base64
import posixpath
import secrets
import shlex
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from common.common_log.log_init import log


# ==================== 业务异常（router 转 400） ====================


class HarnessSandboxError(Exception):
    """沙箱文件操作的预期内失败（路径非法 / 后端未配置 / 命令失败）。"""


# ==================== 大小限制 ====================

READ_TEXT_MAX = 200 * 1024  # 文本预览截断（字节）
UPLOAD_MAX = 20 * 1024 * 1024  # 上传上限（分块传输，超过明显变慢）
DOWNLOAD_MAX = 100 * 1024 * 1024  # 下载上限（backend.read_file 单次整份回传）
_UPLOAD_CHUNK = 64 * 1024  # 单条命令携带的 base64 字符数（< 128KiB）

# 工作区绝对根目录（OpenSandbox 镜像契约，沙箱内固定为 /workspace）。
# 历史上曾试过读 workspace.workdir，但那个属性有时是宿主机路径、有时直接读不到，
# 一旦与沙箱内实际目录对不上，命令里的 `cd <workdir>` 会静默失败、后续 python 无输出（
# 曾表现为 list_dir 返 “无输出”），直接把这里锁为固定常量最稳。
WORKSPACE_ROOT = "/workspace"


# ==================== workspace 连接 ====================


@asynccontextmanager
async def _sandbox_entry(user_id: int, session_id=None) -> AsyncGenerator[dict]:
    """临时 attach 当前用户沙箱，包成一个 async with 作用域（与对话轮次同一套模型）。

    走 workspace_factory.workspace_scope：进入时获取 manager、assign_workspace_id
    （PER_USER 幂等，与 arq 轮次指向同一 workspace）+ get_workspace（已 initialize），
    退出自动 close_all 释放本轮沙箱。session_id 仅 manager 定位入参，PER_USER
    下不影响归属，文件浏览无会话上下文时留空按固定值处理。
    """
    from agentscope.tool import Bash

    from service.service_harness.agent.workspace_factory import (
        workspace_scope, workspace_id_of,
    )

    async with workspace_scope({"user_id": user_id}, session_id) as workspace:
        backend = workspace.get_backend()
        raw_workdir = str(getattr(workspace, "workdir", "") or "")
        # 锁定 WORKSPACE_ROOT，不再拿 workspace.workdir 当错-与前端契约对齐；
        # 两者不一致时只当情报留痕，不作为错误。
        if raw_workdir and raw_workdir.rstrip("/") != WORKSPACE_ROOT:
            log.warning(
                "harness sandbox: workspace.workdir={} 与固定契约 {} 不一致，仍用 {}",
                raw_workdir, WORKSPACE_ROOT, WORKSPACE_ROOT)
        # 用 manager 分配的 workspace_id（回填在 workspace 对象上），取不到才回落
        # 旧的 harness-{user_id} 手工约定，仅避免写空 id。
        ws_id = str(getattr(workspace, "workspace_id", "") or "") or workspace_id_of(user_id)
        yield {
            "workspace_id": ws_id,
            "workspace": workspace,
            "backend": backend,
            "bash": Bash(backend=backend),
            "workdir": WORKSPACE_ROOT,
        }


async def _run(entry: dict, command: str) -> str:
    """在沙箱里执行一条命令并取 stdout 文本（失败抛业务异常）。

    Bash.call 是 async generator function，await entry["bash"]() 返回的是
    AsyncGenerator[ToolChunk, None] 对象本身，而非 ToolChunk——必须用
    async for 消费才能拿到真正的 chunk；否则 getattr(chunk, "state") 永远为
    None、_chunk_text 永远为空字符串（命令失败静默不报错、成功也没输出）。
    Bash.call 每次只 yield 一个 chunk（is_last=True），取最后一个即可。
    """
    try:
        gen = await entry["bash"](command=command)  # AsyncGenerator[ToolChunk, None]
        chunk = None
        async for c in gen:
            chunk = c  # 正常只进一次循环，保留兼容多 chunk 的写法
    except HarnessSandboxError:
        raise
    except Exception as e:  # noqa: BLE001  agentscope 内部异常 / 参数名不符
        log.warning("harness sandbox exec failed: {}", e)
        raise HarnessSandboxError(f"沙箱执行通道异常：{e}") from e
    if chunk is None:
        # generator 未 yield 任何 chunk（理论上不应出现，防御性处理）
        return ""
    state = getattr(chunk, "state", None)
    state_name = str(getattr(state, "name", state) or "")
    text = _chunk_text(chunk)
    if "ERROR" in state_name.upper():
        raise HarnessSandboxError(f"沙箱命令失败：{(text or state_name)[:300]}")
    return text


def _chunk_text(chunk) -> str:
    """把 ToolChunk 的 content blocks 拼成纯文本（非文本块跳过）。"""
    parts: list[str] = []
    for block in getattr(chunk, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


# ==================== 路径安全（全部相对 workdir） ====================


def _safe_rel(path: str) -> str:
    """前端路径 → workdir 相对路径；兼容两种写法，逃逸即拒。

    - 新式绝对：以 WORKSPACE_ROOT (/workspace) 为前缀，例 /workspace 或 /workspace/sub。
      前端面包屑、上传、下载都按这一支传。
    - 旧式相对：''/'sub'/'sub/file'，直接当 workdir 下相对。
    带开斜杠、但不在 /workspace 下的（如 /etc/passwd）一律拒 为“非法路径”；相对写法
    里的 ../ 同样拒。
    """
    raw = (path or "").strip().replace("\\", "/")
    if raw.startswith("/"):
        if raw == WORKSPACE_ROOT or raw.startswith(WORKSPACE_ROOT + "/"):
            raw = raw[len(WORKSPACE_ROOT):]
        else:
            raise HarnessSandboxError("非法路径")
    rel = raw.strip("/")
    if not rel:
        return ""
    norm = posixpath.normpath(rel)
    if norm in (".", ""):
        return ""
    if norm == ".." or norm.startswith("../"):
        raise HarnessSandboxError("非法路径")
    return norm


def _join_rel(dir_path: str, filename: str) -> str:
    """上传目标 = 目录 + 净化后的文件名（basename 防穿越）。"""
    name = posixpath.basename((filename or "").replace("\\", "/"))
    if not name or name in (".", ".."):
        raise HarnessSandboxError("非法文件名")
    rel = _safe_rel(dir_path)
    return f"{rel}/{name}" if rel else name


def _q(value: str) -> str:
    """shell 单参数安全引号（workdir/相对路径/脚本统一走这里）。"""
    return shlex.quote(value)


# ==================== 沙箱内 python 辅助脚本（python3 -c） ====================

# 列目录：输出 [{name,type,size,mtime}] 的 JSON；目录不存在输出 {"error":..}
_LS_SCRIPT = """import os,json,time
base=os.environ['HP']
if not os.path.isdir(base):
    print('{"error":"not_dir"}')
else:
    items=[]
    for n in sorted(os.listdir(base)):
        p=os.path.join(base,n)
        try:
            st=os.stat(p)
        except OSError:
            continue
        items.append({'name':n,'type':'dir' if os.path.isdir(p) else 'file','size':st.st_size,'mtime':time.strftime('%Y-%m-%d %H:%M',time.localtime(st.st_mtime))})
    print(json.dumps(items))"""

# 读文件：NUL 预检分二进制/文本；文本截断打标；二进制只回标记不回内容
# （f-string 内插限额；脚本内的 %d/%s 是沙箱里 python 自己的格式化，不参与外层）
_READ_SCRIPT = f"""import os
p=os.environ['HP']
if not os.path.isfile(p):
    print('ERR:not_file')
else:
    d=open(p,'rb').read()
    if b'\\x00' in d[:4096]:
        print('BIN:%d' % len(d))
    else:
        t=d[:{READ_TEXT_MAX}].decode('utf-8','replace')
        print('TXT:'+t+('\\n[文件较大，已截断]' if len(d)>{READ_TEXT_MAX} else ''))"""


# ==================== 四口实现 ====================


async def list_dir(login_user: dict, path: str = "", session_id=None) -> dict:
    """目录列表（响应形状与旧沙箱口一致，前端零改动）。"""
    async with _sandbox_entry(int(login_user.get("user_id") or 0), session_id) as entry:
        rel = _safe_rel(path)
        # 必须走 _LS_SCRIPT（python3 -c 输出 JSON），不能用原始 ls：
        # 原始 ls 输出纯文件名，json.loads 解析必然失败。
        # _safe_rel 同时防住路径穿越（ls {path} 旧写法直接拼接，有 shell 注入风险）。
        # rel='' 时代表根目录 /workspace；os.path.isdir('') 恒 False 会误报 not_dir，
        # 故 cd 进去后用 '.' 代替空串，让 _LS_SCRIPT 正确列出当前目录。
        hp = rel or "."
        out = await _run(
            entry,
            f"cd {_q(entry['workdir'])} && HP={_q(hp)} python3 -c {_q(_LS_SCRIPT)}",
        )
        import json

        # 沙箱脚本恒有输出；空 stdout 说明执行通道没回料（reattach/传输抖动），
        # 取最后一行非空内容再解析，失败时把原始输出与路径带进日志便于定位。
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if not lines:
            log.warning("harness sandbox list_dir 无输出: workspace={} path={}",
                        entry.get("workspace_id"), path)
            raise HarnessSandboxError("沙箱目录读取失败：无输出") from None
        try:
            items = json.loads(lines[-1])
        except ValueError as e:
            log.warning("harness sandbox list_dir 解析失败: {} raw={}", e, out[:200])
            raise HarnessSandboxError("沙箱目录读取失败") from None
        if isinstance(items, dict) and items.get("error") == "not_dir":
            raise HarnessSandboxError("目录不存在")
        # 前端面包屑统一按工作区绝对路径展示，这里把 rel 拼回 /workspace 前缀；
        # 到根时 parent_path 也指 /workspace 自身（前端自己会按 atRoot 判断是否 disable 回退）。
        cur_abs = WORKSPACE_ROOT if not rel else f"{WORKSPACE_ROOT}/{rel}"
        parent_rel = posixpath.dirname(rel)
        parent_abs = WORKSPACE_ROOT if not parent_rel else f"{WORKSPACE_ROOT}/{parent_rel}"
        return {
            "workspace": entry["workspace_id"],
            "current_path": cur_abs,
            "parent_path": parent_abs,
            "items": items,
        }


async def read_text_file(login_user: dict, path: str, session_id=None) -> dict:
    """查看文件：文本截断预览；二进制只回标记（内容走下载口）。"""
    async with _sandbox_entry(int(login_user.get("user_id") or 0), session_id) as entry:
        rel = _safe_rel(path)
        out = await _run(
            entry,
            f"cd {_q(entry['workdir'])} && HP={_q(rel)} python3 -c {_q(_READ_SCRIPT)}",
        )
        body = out[4:] if out[:4] in ("TXT:", "BIN:") else ""
        if out.startswith("ERR:"):
            raise HarnessSandboxError("文件不存在")
        if out.startswith("BIN:"):
            return {"path": rel, "name": posixpath.basename(rel),
                    "size": int(body or 0), "truncated": False,
                    "content": "（二进制文件，请下载后查看）", "binary": True}
        return {"path": rel, "name": posixpath.basename(rel),
                "size": len(body.encode("utf-8", "replace")),
                "truncated": "[文件较大，已截断]" in body,
                "content": body, "binary": False}


async def save_file(login_user: dict, dir_path: str, filename: str,
                    content: bytes, session_id=None) -> dict:
    """上传：base64 分块写临时文件再解码落位（绕开单参数 128KiB 上限）。"""
    if len(content) > UPLOAD_MAX:
        raise HarnessSandboxError(f"文件超过 {UPLOAD_MAX // 1024 // 1024}MB 限制")
    async with _sandbox_entry(int(login_user.get("user_id") or 0), session_id) as entry:
        target = _join_rel(dir_path, filename)
        parent = posixpath.dirname(target)
        b64 = base64.b64encode(content).decode("ascii")
        tmp = f".harness_up_{secrets.token_hex(8)}"
        cd = f"cd {_q(entry['workdir'])}"
        try:
            for i in range(0, len(b64), _UPLOAD_CHUNK):
                part = b64[i:i + _UPLOAD_CHUNK]
                await _run(entry, f"{cd} && printf '%s' {part} >> {tmp}")
            mkdir = f"mkdir -p {_q(parent)}" if parent else "true"
            await _run(entry, f"{cd} && {mkdir} && base64 -d {tmp} > {_q(target)}")
        finally:
            # 幂等清理：成功失败都删临时文件（失败只记日志不掩盖原异常）
            try:
                await _run(entry, f"{cd} && rm -f {tmp}")
            except HarnessSandboxError as e:
                log.warning("harness sandbox temp cleanup failed: {}", e)
        return {"name": posixpath.basename(target), "size": len(content)}


async def _stat_size(entry: dict, rel: str) -> int:
    """文件字节数（下载前的限额检查；目录返回 0）。"""
    out = await _run(
        entry, f"cd {_q(entry['workdir'])} && wc -c < {_q(rel)} 2>/dev/null || echo 0")
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


async def _read_bytes(entry: dict, rel: str) -> tuple[bytes, str, bool]:
    """下载单文件：走 backend.read_file 直接取原始字节（size 检查在先）。

    不经 Bash 工具回传 base64——那个通道会把大段 stdout 截断，二进制被切
    成非 4 倍长度就报 base64 "Incorrect padding"（大文件必现）。read_file 是
    后端专用文件 API，整份返回、不经 shell。在已连接的 entry 作用域内调用。

    返回 (字节, 文件名, 是否目录)——目录场景由 _pack_dir 走 zip。
    """
    size = await _stat_size(entry, rel)
    if size > DOWNLOAD_MAX:
        raise HarnessSandboxError(f"文件超过 {DOWNLOAD_MAX // 1024 // 1024}MB 下载限制")
    abs_path = entry["backend"].join_path(entry["workdir"], rel)
    try:
        data = await entry["backend"].read_file(abs_path)
    except Exception as e:  # noqa: BLE001  read_file 文件缺失/后端异常
        log.warning("harness sandbox read_file failed: path={} {}", abs_path, e)
        raise HarnessSandboxError("文件不存在或读取失败") from e
    if not data:
        raise HarnessSandboxError("文件不存在或为空")
    return data, posixpath.basename(rel), False


async def _pack_dir(entry: dict, rel: str) -> tuple[bytes, str, bool]:
    """下载目录：沙箱内 tar.gz 落 /tmp 临时文件，再经 backend.read_file 取字节。

    同样避开 Bash 回传 base64 的大 stdout 截断坑（见 _read_bytes）。响应头按
    zip 呈现给浏览器。在已连接 entry 内调用。
    """
    backend = entry["backend"]
    tmp = f"/tmp/harness-dl-{secrets.token_hex(8)}.tar.gz"
    try:
        await _run(entry,
                   f"tar czf {_q(tmp)} -C {_q(entry['workdir'])} {_q(rel)} 2>/dev/null")
        data = await backend.read_file(tmp)
    except HarnessSandboxError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("harness sandbox pack dir failed: rel={} {}", rel, e)
        raise HarnessSandboxError("目录不存在或打包失败") from e
    finally:
        try:
            await backend.delete_path(tmp)
        except Exception as e:  # noqa: BLE001
            log.warning("harness sandbox temp cleanup failed: {}", e)
    if not data:
        raise HarnessSandboxError("目录不存在或为空")
    if len(data) > DOWNLOAD_MAX:
        raise HarnessSandboxError(f"目录超过 {DOWNLOAD_MAX // 1024 // 1024}MB 下载限制")
    return data, f"{posixpath.basename(rel)}.zip", True


async def download(login_user: dict, path: str, session_id=None) -> tuple[bytes, str, bool]:
    """下载分流：文件回原样字节，目录打包 tar.gz（文件名带 .zip）。

    判型与读取/打包全在同一 _sandbox_entry 作用域内一次完成（避免重复
    attach/reattach）；判型在沙箱内一条 test 完成（D=目录/F=文件/N=不存在）。
    返回 (字节, 下载名, 是否目录)。
    """
    async with _sandbox_entry(int(login_user.get("user_id") or 0), session_id) as entry:
        rel = _safe_rel(path)
        if not rel:
            raise HarnessSandboxError("非法路径")
        kind = await _run(
            entry,
            f"cd {_q(entry['workdir'])} && (test -d {_q(rel)} && echo D "
            f"|| (test -f {_q(rel)} && echo F || echo N))",
        )
        marker = (kind.strip().splitlines() or ["N"])[-1][-1:]
        if marker == "D":
            return await _pack_dir(entry, rel)
        if marker == "F":
            return await _read_bytes(entry, rel)
        raise HarnessSandboxError("文件或目录不存在")
