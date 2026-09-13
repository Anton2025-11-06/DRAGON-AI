# -*- coding: utf-8 -*-
"""
P5 验证脚本（不依赖 MySQL，直接调用 service 层）：
1. skill：zip 解压（SKILL.md 校验 / 单层包裹剥离 / 路径穿越拦截 / 空包 / code 规范化 / 打包回读）
2. MCP：官方 SDK 本地 stdio echo server（子进程）——test_params 真实握手、
   call_tool 全链路（预置会话避免 DB）、batch_tools 真实清单
3. ToolService：run/main 入口、set 返回值序列化兜底、10s 超时守卫、异常捕获

运行：cd 项目根 && python site/_p5_verify.py
"""
import asyncio
import io
import json
import os
import shutil
import sys
import time as _time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 屏蔽 SKILL_ROOT 环境变量干扰，使用服务默认回退目录
os.environ.pop("SKILL_ROOT", None)

from service.service_workflow.services import skill_service  # noqa: E402
from service.service_workflow.services.mcp_service import (  # noqa: E402
    McpConnectionRegistry,
    McpServerService,
)
from service.service_workflow.services.tool_service import ToolService  # noqa: E402

results: list = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ==================== 1. skill zip 逻辑 ====================
def _make_zip(entries, skip_skill=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in entries:
            if skip_skill and rel == "SKILL.md":
                continue
            zf.writestr(rel, content)
    buf.seek(0)
    return buf.read()


async def test_skill():
    root = skill_service.SKILL_ROOT
    for name in ("p5-test-skill", "p5-test-skill2", "p5-test-skill3",
                 "p5-test-skill4", "p5-test-skill5"):
        shutil.rmtree(root / name, ignore_errors=True)

    # 1a 正常 zip（含 SKILL.md + 嵌套资源）
    data = _make_zip([("SKILL.md", "# Demo\n"),
                      ("scripts/run.py", "print(1)\n"),
                      ("assets/logo.json", '{"a":1}')])
    try:
        d = skill_service.SkillService._extract_zip("p5-test-skill", data)
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
        cnt = skill_service.SkillService._count_files(d)
        check("skill: 解压 SKILL.md+资源", files == ["SKILL.md", "assets/logo.json", "scripts/run.py"] and cnt == 3,
              f"files={files} count={cnt}")
    except Exception as e:  # noqa: BLE001
        check("skill: 解压 SKILL.md+资源", False, str(e))

    # 1b 单层目录包裹自动剥离
    data = _make_zip([("demo-skill/SKILL.md", "# Demo\n"), ("demo-skill/lib/tool.py", "x=1\n")])
    try:
        d = skill_service.SkillService._extract_zip("p5-test-skill2", data)
        files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
        check("skill: 单层包裹剥离", files == ["SKILL.md", "lib/tool.py"], str(files))
    except Exception as e:  # noqa: BLE001
        check("skill: 单层包裹剥离", False, str(e))

    # 1c 路径穿越（../ 与绝对路径）拦截
    for evil in ("../evil.txt", "a/../../evil.txt"):
        data = _make_zip([(evil, "x"), ("SKILL.md", "# x")])
        try:
            skill_service.SkillService._extract_zip("p5-test-skill3", data)
            check(f"skill: 拒绝路径穿越 {evil!r}", False, "未拦截")
        except ValueError as e:
            check(f"skill: 拒绝路径穿越 {evil!r}", "非法路径" in str(e), str(e))

    # 1d 缺 SKILL.md / 空包
    data = _make_zip([("a.txt", "x")])
    try:
        skill_service.SkillService._extract_zip("p5-test-skill4", data)
        check("skill: 必须含 SKILL.md", False, "未拦截")
    except ValueError as e:
        check("skill: 必须含 SKILL.md", "SKILL.md" in str(e), str(e))
    data = _make_zip([])
    try:
        skill_service.SkillService._extract_zip("p5-test-skill5", data)
        check("skill: 拒绝空包", False)
    except ValueError:
        check("skill: 拒绝空包", True)

    # 1e code 规范化（目录/后缀/非法字符/中文）
    c1 = skill_service.SkillService._normalize_code("my cool skill.zip")
    c2 = skill_service.SkillService._normalize_code("skills/数据查询.v2")
    check("skill: code 规范化", c1 == "my-cool-skill" and c2 == "数据查询.v2",
          f"{c1} / {c2}")

    # 1f zip 打包回读（下载路径）
    try:
        d = root / "p5-test-skill"
        buf = skill_service.SkillService.zip_skill(d)
        with zipfile.ZipFile(io.BytesIO(buf.read())) as zf:
            names = sorted(zf.namelist())
        check("skill: zip 打包回读", names == ["SKILL.md", "assets/logo.json", "scripts/run.py"], str(names))
    except Exception as e:  # noqa: BLE001
        check("skill: zip 打包回读", False, str(e))

    for name in ("p5-test-skill", "p5-test-skill2", "p5-test-skill3",
                 "p5-test-skill4", "p5-test-skill5"):
        shutil.rmtree(root / name, ignore_errors=True)


# ==================== 2. MCP 官方 SDK（本地 stdio echo server） ====================
ECHO_SERVER_SRC = r'''
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("echo")


@mcp.tool(description="Echo the input text back")
async def echo(text: str) -> str:
    return "echo: " + text


@mcp.tool(description="Add two integers")
async def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    mcp.run()
'''


async def test_mcp():
    server_path = ROOT / "site" / "_p5_echo_server.py"
    server_path.write_text(ECHO_SERVER_SRC, encoding="utf-8")

    # 2a test_params 真实 STDIO 握手（initialize + tools/list）
    r = await McpServerService.test_params(
        name="echo", type_="STDIO",
        command=sys.executable, args=json.dumps([str(server_path)]))
    check("mcp: test_params 真实握手(echo server)",
          r.get("success") and r.get("toolCount") >= 2,
          f"toolCount={r.get('toolCount')} {r.get('errorMessage')}")

    # 2b call_tool 全链路（预置会话避免 DB 查询）
    row = [999, "echo", "STDIO", None, sys.executable, json.dumps([str(server_path)]), None, 1, 0]
    session = await McpConnectionRegistry.get(999, row=row)
    try:
        r2 = await McpServerService.call_tool(999, "echo", {"text": "hello p5"})
        ok = r2.get("isError") is False and "echo: hello p5" in r2.get("content", "")
        check("mcp: call_tool 文本工具", ok, json.dumps(r2, ensure_ascii=False)[:140])
        r3 = await McpServerService.call_tool(999, "add", {"a": 3, "b": 4})
        ok3 = r3.get("isError") is False and "7" in r3.get("content", "")
        check("mcp: call_tool 数字工具", ok3, json.dumps(r3, ensure_ascii=False)[:140])

        # 2c batch_tools 真实清单（命中同一缓存会话，不触 DB）
        tools = await McpServerService.batch_tools([999])
        names = sorted(t["name"] for t in tools)
        check("mcp: batch_tools 真实清单", names == ["add", "echo"], str(names))
        if tools:
            check("mcp: inputSchema 返回", bool(tools[0].get("inputSchema")),
                  json.dumps(tools[0].get("inputSchema"), ensure_ascii=False)[:100])
    finally:
        McpConnectionRegistry._sessions.pop(999, None)
        await McpServerService._close_session(session)


# ==================== 3. ToolService 受限执行回归 ====================
async def test_tool():
    r = await ToolService._run_guarded("def run(a):\n    return {'v': a * 2}", {"a": 21})
    check("tool: run 入口", r["success"] and r["result"] == {"v": 42}, json.dumps(r, ensure_ascii=False))

    r = await ToolService._run_guarded("def main(x):\n    return x + 1", {"x": 1})
    check("tool: main 入口(CODE节点一致)", r["success"] and r["result"] == 2, str(r.get("result")))

    r = await ToolService._run_guarded("def run():\n    return {1, 2, 3}", {})
    ok = r["success"] and isinstance(r["result"], str) and all(s in r["result"] for s in ("1", "2", "3"))
    check("tool: set 返回值序列化兜底", ok, str(r.get("result")))

    r = await ToolService._run_guarded("def run():\n    return (1, 2)", {})
    # tuple 本身可 JSON 化（转数组），验证可直接序列化而非被 str() 兜底
    ok = r["success"] and json.dumps(r["result"]) == "[1, 2]"
    check("tool: tuple 返回值可 JSON 化", ok, repr(r.get("result")))
    
    # 受限环境禁 import（__import__ 已隔离），注入阻塞实现检验 wait_for 超时；
    # 用例结束后恢复原 _execute，避免污染后续用例
    def _slow_runner(source, parameters):
        _time.sleep(12)
        return "late"
    
    _orig_execute = ToolService._execute
    ToolService._execute = staticmethod(_slow_runner)
    try:
        r = await ToolService._run_guarded("x", {})
    finally:
        ToolService._execute = _orig_execute
    check("tool: 10s 超时守卫", r["success"] is False and "超时" in r.get("error", ""), r.get("error"))

    r = await ToolService._run_guarded("def run():\n    raise ValueError('boom')", {})
    check("tool: 异常捕获", r["success"] is False and "boom" in r.get("error", ""), r.get("error"))


async def main():
    print("== 1. 技能 zip 逻辑 ==", flush=True)
    await test_skill()
    print("== 2. MCP 官方 SDK（echo server）==", flush=True)
    await test_mcp()
    print("== 3. ToolService 受限执行回归 ==", flush=True)
    await test_tool()

    failed = [n for n, ok in results if not ok]
    print(f"\n总计 {len(results)} 项，失败 {len(failed)} 项", flush=True)
    if failed:
        print("失败项: " + ", ".join(failed), flush=True)
    return len(failed)


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(0 if code == 0 else 1)