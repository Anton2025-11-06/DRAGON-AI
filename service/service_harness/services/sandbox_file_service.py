# -*- coding: utf-8 -*-
"""harness 沙箱文件代理：把每用户的 OpenSandbox 工作区代理成文件浏览器四口。

与旧 demo（service_workflow 的 sandbox_service，本地目录版）保持同一套响应
形状（files / file / upload / download），前端切换只换 URL 不换结构。

执行通道（agentscope 官方文档确认的口径，v2.0.9）：
- ``workspace.get_backend()`` 拿到绑定沙箱的执行后端；
- ``Bash(backend=...)`` 在沙箱里执行 shell——镜像契约必含 python3 / tar /
  base64（agentscope 文件工具自身就靠它们），元信息用 python3 单行输出
  JSON、路径经环境变量传入（杜绝 shell 注入）；
- 上传/下载走 base64-over-exec（与 agentscope 快照机制同款通道），分块
  绕开 Linux 单命令行参数 ~128KiB 的上限。

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

from common.common_log.log_init import log

# ==================== 业务异常（router 转 400） ====================


class HarnessSandboxError(Exception):
    """沙箱文件操作的预期内失败（路径非法 / 后端未配置 / 命令失败）。"""


# ==================== 大小限制 ====================

READ_TEXT_MAX = 200 * 1024          # 文本预览截断（字节）
UPLOAD_MAX = 20 * 1024 * 1024       # 上传上限（分块传输，超过明显变慢）
DOWNLOAD_MAX = 100 * 1024 * 1024     # 下载上限（单次 base64 回传）
_UPLOAD_CHUNK = 64 * 1024           # 单条命令携带的 base64 字符数（< 128KiB）


# ==================== workspace 连接 ====================


@asynccontextmanager
async def _sandbox_entry(user_id: int, session_id=None):
    """临时 attach 当前用户沙箱，包成一个 async with 作用域（与对话轮次同一套模型）。

    走 workspace_factory.workspace_scope：进入时建临时 manager、assign_workspace_id
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
        workdir = str(getattr(workspace, "workdir", "") or "")
        # 用 manager 分配的 workspace_id（回填在 workspace 对象上），取不到才回落
        # 旧的 harness-{user_id} 手工约定，仅避免写空 id。
        ws_id = str(getattr(workspace, "workspace_id", "") or "") or workspace_id_of(user_id)
        if not workdir:
            raise HarnessSandboxError(
                f"沙箱 workdir 为空，无法执行文件操作（workspace_id={ws_id}）")
        yield {
            "workspace_id": ws_id,
            "workspace": workspace,
            "bash": Bash(backend=backend),
            "workdir": workdir,
        }


async def _run(entry: dict, command: str) -> str:
    """在沙箱里执行一条命令并取 stdout 文本（失败抛业务异常）。

    Bash 工具失败（非零 exit / 内部异常）时 state 会带 ERROR，错误文本通常
    在 chunk 里——统一包装成 HarnessSandboxError，router 侧转 400。
    """
    try:
        chunk = await entry["bash"](command=command)
    except HarnessSandboxError:
        raise
    except Exception as e:  # noqa: BLE001  agentscope 内部异常 / 参数名不符
        log.warning("harness sandbox exec failed: {}", e)
        raise HarnessSandboxError(f"沙箱执行通道异常：{e}") from e
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
    """前端路径 → workdir 相对路径；逃逸（../、绝对路径、反斜杠）即拒。"""
    rel = (path or "").strip().replace("\\", "/").strip("/")
    if not rel:
        return ""
    norm = posixpath.normpath(rel)
    if norm in (".", ""):
        return ""
    if norm == ".." or norm.startswith("../") or norm.startswith("/"):
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
        out = await _run(
            entry,
            f"cd {_q(entry['workdir'])} && HP={_q(rel)} python3 -c {_q(_LS_SCRIPT)}",
        )
        import json

        try:
            items = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as e:
            log.info(e)
            raise HarnessSandboxError("沙箱目录读取失败") from None
        if isinstance(items, dict) and items.get("error") == "not_dir":
            raise HarnessSandboxError("目录不存在")
        return {
            "workspace": entry["workspace_id"],
            "current_path": rel,
            "parent_path": posixpath.dirname(rel),
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
    """下载单文件：base64 回传（size 检查在先）。在已连接的 entry 作用域内调用。

    返回 (字节, 文件名, 是否目录)——目录场景由 _pack_dir 走 zip。
    """
    if await _stat_size(entry, rel) > DOWNLOAD_MAX:
        raise HarnessSandboxError(f"文件超过 {DOWNLOAD_MAX // 1024 // 1024}MB 下载限制")
    out = await _run(entry,
                     f"cd {_q(entry['workdir'])} && base64 {_q(rel)} 2>/dev/null "
                     f"|| echo ERR")
    if not out or out.strip() == "ERR":
        raise HarnessSandboxError("文件不存在")
    data = base64.b64decode("".join(out.split()))
    if not data:
        raise HarnessSandboxError("文件不存在或为空")
    return data, posixpath.basename(rel), False


async def _pack_dir(entry: dict, rel: str) -> tuple[bytes, str, bool]:
    """下载目录：tar.gz + base64 回传（响应头按 zip 呈现给浏览器）。在已连接 entry 内调用。"""
    full = await _run(entry,
                      f"cd {_q(entry['workdir'])} && tar cz {_q(rel)} 2>/dev/null "
                      f"| base64")
    body = "".join(full.split())
    if not body:
        raise HarnessSandboxError("目录不存在或为空")
    data = base64.b64decode(body)
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



