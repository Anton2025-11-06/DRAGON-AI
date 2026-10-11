#!/usr/bin/env bash
# 启动 arq harness worker（深度探索智能体对话轮次的消费进程）。
#
# 读这些环境变量：
#   NACOS_ADDR / NACOS_NAME / NACOS_PASSWD / NACOS_NS_ID
#       worker 启动前要按 data_id=arq_harness 从 Nacos 拉 Redis 地址，
#       必须和生产端（service_harness）同源，否则任务投了没人消费；
#       arq_harness 段未配置时两侧都会回落 arq_workflow 段（同一 Redis）
#   ARQ_WORKERS     worker 进程数，默认 2（每任务占一条模型流 + 一个沙箱会话，
#                   并发上限由 worker_settings_harness.max_jobs 另行约束）
#   SPLIT_NUMBER    本容器消费的队列切片号，默认 1
#       队列名 = harness_queue:split_${SPLIT_NUMBER}；要横向扩切片就多起几个容器，
#       每个给一个不同的 SPLIT_NUMBER，切片总数由监控页写入 Redis key
#       harness_queue:split_number
# -t harness 是必填参数：同一个 run_workers 入口也用来拉 workflow / ragflow
# 流水线（见 run_arq_workflow.sh / run_arq_ragflow.sh），靠 -t 分派到各自的
# WorkerSettings，多条流水线的队列与并发互不相干。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARQ_WORKERS="${ARQ_WORKERS:-2}"
SPLIT_NUMBER="${SPLIT_NUMBER:-1}"

echo "[run_arq_harness] ${ARQ_WORKERS} 个 worker，消费 harness_queue:split_${SPLIT_NUMBER}"

python -m arq_tasks.run_workers -t harness -n "$ARQ_WORKERS" -p "$SPLIT_NUMBER" &
CHILD=$!

# docker stop 发的是 TERM，而 run_workers 的优雅退出分支挂在 SIGINT（等价 Ctrl+C）上；
# 这里把 TERM 转成 INT 传下去，让在跑的任务走完收尾再退出。
trap 'kill -INT "$CHILD" 2>/dev/null || true' INT TERM

wait "$CHILD"
