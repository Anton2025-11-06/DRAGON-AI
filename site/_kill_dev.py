import os
import re
import subprocess

# 找到监听 5999 端口的进程并结束（仅前端 dev server，不影响 IDE）
try:
    out = subprocess.check_output("netstat -ano -p tcp", shell=True, text=True, errors="ignore")
except Exception as e:  # noqa: BLE001
    print("netstat failed:", e)
    raise SystemExit(0)

pids = set()
for line in out.splitlines():
    if ":5999" in line and "LISTENING" in line.upper():
        m = re.search(r"(\d+)\s*$", line.strip())
        if m:
            pids.add(m.group(1))

print("listening pids on 5999:", pids)
for pid in pids:
    try:
        subprocess.call(f"taskkill /PID {pid} /T /F", shell=True)
        print("killed", pid)
    except Exception as e:  # noqa: BLE001
        print("kill failed", pid, e)
