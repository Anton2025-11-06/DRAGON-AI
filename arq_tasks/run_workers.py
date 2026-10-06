# -*- coding: utf-8 -*-
"""多进程启动 arq worker（按 -t 分派 workflow / ragflow / graphflow 三条流水线）。

三条流水线各用自己的 WorkerSettings 与队列（workflow_queue:split_{N} /
rag_queue:split_{N} / graphflow_queue:split_{N}），同一个切片号在各自队列里是独立分片，
部署可以分开拉（图谱比文档解析更耗时，单独成队后可各自扩缩容、互不抢占 worker）。
"""
import argparse
import os
import subprocess
import sys
import time

from common.common_arq.queue import (
    PIPELINE_GRAPH,
    PIPELINE_RAG,
    PIPELINE_WORKFLOW,
    queue_name_of,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -t 取值 → （arq 流水线标识, WorkerSettings 完整路径）。CLI 只认得到这几份名字，
# 新增流水线就必须在这里登记，不给它拼字符串的自由（拼错是启动才发现）
_PIPELINES = {
    "workflow": (PIPELINE_WORKFLOW, "arq_tasks.worker_settings.WorkerSettings"),
    "ragflow": (PIPELINE_RAG, "arq_tasks.worker_settings_rag.WorkerSettingsRag"),
    "graphflow": (PIPELINE_GRAPH, "arq_tasks.worker_settings_graph.WorkerSettingsGraph"),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="启动多个worker 进程（arq CLI 单进程，需自行拉多进程）")
    parser.add_argument(
        "-t", type=str, required=True, choices=["ragflow", "workflow", "graphflow"],
        help="业务类型：ragflow|workflow|graphflow")
    parser.add_argument(
        "-n", type=int, required=True,
        help="worker 进程数")
    parser.add_argument(
        "-p", type=int, required=True,
        help="队列切片号,写入子进程环境变量，worker 消费 {业务队列}:split_{N} 队列）")
    args = parser.parse_args()

    if args.n < 1:
        parser.error("worker 数量必须 >= 1")
    if args.p < 1:
        parser.error("切片号必须 >= 1")
    pipeline, settings_path = _PIPELINES[args.t]
    # 切片号注入子进程环境:worker 端 WorkerSettings.queue_name 据此拼分片队列名
    os.environ["SPLIT_NUMBER"] = str(args.p)

    cmd = [sys.executable, "-m", "arq", settings_path]
    procs: list[subprocess.Popen] = []
    queue_name = queue_name_of(args.p, pipeline)
    print(f"[run_workers] 启动 {args.n} 个 {args.t} worker，消费队列 {queue_name}")
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
