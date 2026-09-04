# -*- coding: utf-8 -*-
"""
沙箱服务：用户工作区文件浏览 / 上传 / 下载 / 查看 / 演示对话
- 存储根目录：sandbox_root/{username}，首次访问自动创建并放置演示文件
- 所有相对路径均做越界校验（防 ../ 路径穿越），保证不会读写根目录之外
"""
import io
import os
import re
import time
import zipfile
from pathlib import Path
from typing import List, Optional

SANDBOX_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent / "sandbox_root"

# 可预览的文本扩展名（二进制文件提示下载查看）
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".vue", ".java", ".go", ".c", ".cpp", ".h",
    ".sh", ".bat", ".ps1", ".sql", ".log", ".ini", ".conf", ".toml",
}

# 演示用技能列表（真实技能服务后端未接入，先提供可扩展的静态清单）
DEMO_SKILLS = [
    {"id": "code-exec", "name": "代码执行", "description": "在沙箱中执行 Python/Shell 代码并返回结果"},
    {"id": "data-analyze", "name": "数据分析", "description": "读取工作区文件，进行统计与可视化分析"},
    {"id": "web-search", "name": "联网搜索", "description": "检索互联网信息并汇总答案"},
    {"id": "file-ops", "name": "文件操作", "description": "在工作区中创建、修改、整理文件"},
]

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class SandboxError(ValueError):
    pass


def _username_dir(username: str) -> Path:
    """用户工作区根目录（不存在则创建并初始化演示文件）"""
    if not _USERNAME_RE.match(username or ""):
        raise SandboxError("无效的用户标识")
    root = SANDBOX_ROOT / username
    root.mkdir(parents=True, exist_ok=True)
    _init_demo_files(root)
    return root


def _init_demo_files(root: Path) -> None:
    """首次访问时放置演示文件，便于体验沙箱桌面"""
    if root.joinpath(".initialized").exists():
        return
    try:
        root.joinpath("README.txt").write_text(
            "欢迎来到深度探索 Hernes 沙箱工作区！\n\n"
            "这里是属于你的云端工作目录：\n"
            "  - 上传文件：点击右上角「上传」按钮\n"
            "  - 下载文件/文件夹：悬停条目，点击「下载」（文件夹会打包为 zip）\n"
            "  - 双击文件夹进入，双击文本文件查看内容\n"
            "  - 在左侧对话框描述任务，AI 助手将在这里执行你的问题\n",
            encoding="utf-8")
        root.joinpath("notes").mkdir(exist_ok=True)
        root.joinpath("notes", "todo.md").write_text(
            "# Hernes 待办\n\n- [ ] 体验沙箱文件浏览\n- [ ] 上传一个数据文件试试分析\n- [ ] 向 AI 提问",
            encoding="utf-8")
        root.joinpath("sample_data.csv").write_text(
            "日期,访问量,订单数\n2026-08-25,1024,88\n2026-08-26,1180,96\n2026-08-27,1355,110\n",
            encoding="utf-8")
        root.joinpath(".initialized").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except OSError:
        pass


def resolve_path(username: str, rel_path: str = "") -> Path:
    """把相对路径解析为根内绝对路径，越界一律拒绝"""
    root = _username_dir(username).resolve()
    if not rel_path:
        return root
    target = (root / rel_path).resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise SandboxError("非法路径，不允许访问工作区之外")
    return target


def list_dir(username: str, rel_path: str = "") -> dict:
    """列出目录内容"""
    target = resolve_path(username, rel_path)
    if target.is_file():
        raise SandboxError("目标不是文件夹")
    items = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        raise SandboxError(f"读取目录失败：{e}")
    for p in entries:
        try:
            st = p.stat()
        except OSError:
            continue
        items.append({
            "name": p.name,
            "type": "dir" if p.is_dir() else "file",
            "size": st.st_size if p.is_file() else 0,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        })
    rel = rel_path.strip("/")
    parent = "" if not rel else "/".join(rel.split("/")[:-1])
    return {
        "workspace": username,
        "current_path": rel,
        "parent_path": parent,
        "items": items,
    }


def read_text_file(username: str, rel_path: str) -> dict:
    """读取文本文件内容（限制 200KB，超出截断提示）"""
    target = resolve_path(username, rel_path)
    if not target.is_file():
        raise SandboxError("文件不存在")
    ext = target.suffix.lower()
    if ext not in TEXT_EXTS:
        raise SandboxError(f"{ext or '二进制'} 文件暂不支持在线预览，请下载后查看")
    data = target.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SandboxError("文件不是 UTF-8 文本，暂不支持在线预览")
    truncated = False
    if len(text) > 200 * 1024:
        text = text[: 200 * 1024]
        truncated = True
    return {
        "path": rel_path,
        "name": target.name,
        "size": len(data),
        "truncated": truncated,
        "content": text,
    }


def save_file(username: str, rel_path: str, filename: str, content: bytes) -> dict:
    """保存上传文件（路径保持，防穿越）"""
    filename = os.path.basename(filename or "upload.bin")
    if not filename:
        raise SandboxError("文件名不能为空")
    target_dir = resolve_path(username, rel_path)
    if not target_dir.is_dir():
        raise SandboxError("目标目录不存在")
    target = (target_dir / filename).resolve()
    if not str(target).startswith(str(target_dir.resolve()) + os.sep):
        raise SandboxError("非法文件名")
    target.write_bytes(content)
    return {
        "path": str(Path(rel_path) / filename) if rel_path else filename,
        "name": filename,
        "size": len(content),
    }


def zip_dir(source_dir: Path, rel_prefix: str = "") -> io.BytesIO:
    """把目录打包为 zip（内存），中文名使用 UTF-8"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(source_dir.rglob("*")):
            if p.is_file() and p.name != ".initialized":
                arc = os.path.join(rel_prefix, p.relative_to(source_dir).as_posix())
                zf.write(p, arc)
    buf.seek(0)
    return buf


def _skill_names(skill_ids: Optional[List[str]]) -> List[str]:
    """技能 id → 展示名称"""
    name_map = {s["id"]: s["name"] for s in DEMO_SKILLS}
    return [name_map.get(i, str(i)) for i in (skill_ids or [])]


def chat_reply(message: str, skills: Optional[List[str]], tools: Optional[List[str]],
               kbs: Optional[List[str]]) -> dict:
    """演示对话：回显挂载能力并模拟沙箱任务递交（云端沙箱执行器待接入）"""
    skill_names = _skill_names(skills)
    tool_names = tools or []
    kb_names = kbs or []
    lines = [
        f"已收到你的任务：**{message[:200]}**",
        "",
        "沙箱云端执行器正在部署中，当前为演示模式，已为你挂载：",
    ]
    if skill_names:
        lines.append(f"- 技能：{'、'.join(skill_names)}")
    else:
        lines.append("- 技能：未挂载")
    if tool_names:
        lines.append(f"- 工具：{'、'.join(tool_names)}")
    else:
        lines.append("- 工具：未挂载（可点击右侧「+ 工具」从 MCP 连接中选择）")
    if kb_names:
        lines.append(f"- 知识库：{'、'.join(kb_names)}")
    else:
        lines.append("- 知识库：未挂载（可点击「+ 知识库」选择平台知识库）")
    lines += [
        "",
        "> 正式环境中，任务将提交到云端沙箱运行时：自动创建隔离环境、执行你的问题、",
        "> 并把结果文件写入右侧工作区，你可以随时浏览、下载。",
    ]
    return {
        "reply": "\n".join(lines),
        "sandbox": "demo",
        "skills": skill_names,
        "tools": tool_names,
        "kbs": kb_names,
    }


def demo_skills() -> List[dict]:
    return DEMO_SKILLS