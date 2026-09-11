# -*- coding: utf-8 -*-
"""多进程启动 arq worker。

arq CLI 本身只启动单进程（一个进程 = 一个 asyncio worker），
没有 -w/--workers 这类参数（老版本 -w 是 --max-jobs，本版本已移除）。
要横向扩展必须自行拉起多个进程（等同 supervisor 配置 N 个 program）。

用法（项目根目录执行）：
    python -m arq_tasks.run_workers -n 4 -p 1    # 启动 4 个 worker 消费切片 1（split_1）队列
    python -m arq_tasks.run_workers -n 4 -p 2    # 启动 4 个 worker 消费切片 2（split_2）队列

行为：
- 切片号经子进程环境变量 SPLIT_NUMBER 传入,worker 端 WorkerSettings.queue_name
  拼成 workflow_queue:split_{N} 分片队列(生产/消费两端取相同值才会对上)
- 每个子进程实际执行 `python -m arq arq_tasks.worker_settings.WorkerSettings`
- Ctrl+C：Windows 控制台会同时向进程组发 CTRL_C_EVENT（arq 收到后优雅退出），
  本脚本再兜底 terminate/强杀未退出的进程
- 子进程 stdout/stderr 直接继承当前控制台，日志与直接跑 arq 一致
"""
import argparse
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="启动多个 arq worker 进程（arq CLI 单进程，需自行拉多进程）")
    parser.add_argument(
        "-n", type=int, required=True,
        help="worker 进程数")
    parser.add_argument(
        "-p", type=int, required=True,
        help="队列切片号,"
             "写入子进程环境变量，worker 消费 workflow_queue:split_{N} 队列）")
    args = parser.parse_args()

    if args.n < 1:
        parser.error("worker 数量必须 >= 1")
    if args.p < 1:
        parser.error("切片号必须 >= 1")
    # 切片号注入子进程环境:worker 端 WorkerSettings.queue_name 据此拼分片队列名
    os.environ["SPLIT_NUMBER"] = str(args.p)

    cmd = [sys.executable, "-m", "arq",
           "arq_tasks.worker_settings.WorkerSettings"]
    procs: list[subprocess.Popen] = []
    queue_name = f"workflow_queue:split_{args.p}"
    print(f"[run_workers] 启动 {args.n} 个 worker，消费队列 {queue_name}")
    try:
        for i in range(args.n):
            p = subprocess.Popen(cmd, cwd=_ROOT, env=os.environ.copy())
            procs.append(p)
            print(f"[run_workers] worker#{i + 1} 已启动 pid={p.pid}")
        # 任一进程退出（如 Redis 连接失败）即打印退出码；全部退出后脚本结束
        while procs:
            for p in list(procs):
                code = p.poll()
                if code is not None:
                    print(f"[run_workers] worker pid={p.pid} 已退出，退出码={code}")
                    procs.remove(p)
            if procs:
                time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        print("[run_workers] 收到 Ctrl+C，正在停止所有 worker…")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 5
        for p in procs:
            try:
                p.wait(timeout=max(0.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
        print("[run_workers] 已全部停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
