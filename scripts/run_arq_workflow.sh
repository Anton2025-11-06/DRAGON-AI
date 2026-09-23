#!/usr/bin/env bash
# 启动 arq worker（工作流的异步执行进程）。
#
# 读这些环境变量：
#   NACOS_ADDR / NACOS_NAME / NACOS_PASSWD / NACOS_NS_ID
#       worker 启动前要按 data_id=arq_workflow 从 Nacos 拉 Redis 地址，
#       必须和生产端（service_workflow）同源，否则任务投了没人消费
#   ARQ_WORKERS     worker 进程数，默认 4（arq CLI 本身单进程，多进程由 run_workers 拉起）
#   SPLIT_NUMBER    本容器消费的队列切片号，默认 1
#       队列名 = workflow_queue:split_${SPLIT_NUMBER}；要横向扩切片就多起几个容器，
#       每个给一个不同的 SPLIT_NUMBER，切片总数由监控页写入 Redis key workflow_queue:split_number
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARQ_WORKERS="${ARQ_WORKERS:-4}"
SPLIT_NUMBER="${SPLIT_NUMBER:-1}"

echo "[run_arq_workflow] ${ARQ_WORKERS} 个 worker，消费 workflow_queue:split_${SPLIT_NUMBER}"

python -m arq_tasks.run_workers -n "$ARQ_WORKERS" -p "$SPLIT_NUMBER" &
CHILD=$!

# docker stop 发的是 TERM，而 run_workers 的优雅退出分支挂在 SIGINT（等价 Ctrl+C）上；
# 这里把 TERM 转成 INT 传下去，让在跑的任务走完收尾再退出。
trap 'kill -INT "$CHILD" 2>/dev/null || true' INT TERM

wait "$CHILD"
