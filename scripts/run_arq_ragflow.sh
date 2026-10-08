#!/usr/bin/env bash
# 启动 arq ragflow worker（知识库解析/分块/向量化/图谱/媒体理解的异步消费进程）。
#
# 与 run_arq_workflow.sh 是同一个入口（arq_tasks.run_workers）的两种 -t 取值：
# 队列、WorkerSettings、并发各自独立，两条流水线互不偷任务（SPEC §4.1）。
# 纯 CPU 运行，不读任何 GPU 相关变量（SPEC §4.2、§12-4）。
#
# 读这些环境变量：
#   NACOS_ADDR / NACOS_NAME / NACOS_PASSWD / NACOS_NS_ID
#       worker 启动前要按 data_id=arq_ragflow 从 Nacos 拉 Redis 地址与后端配置，
#       必须和生产端（service_rag 的投递）同源，否则任务投了没人消费
#   ARQ_WORKERS     worker 进程数，默认 2
#       rag 任务的并发主要在 WorkerSettingsRag.max_jobs（进程内并发，取 Nacos 配置），
#       进程数只用来横向扩机器核数；解析与分块是 CPU 活，进程数远超核数只会互相抢
#   SPLIT_NUMBER    本容器消费的队列切片号，默认 1
#       队列名 = rag_queue:split_${SPLIT_NUMBER}；扩容多起几个容器各给一个不同切片号，
#       切片总数由监控页写入 Redis key rag_queue:split_number（生产端按它轮询投递）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARQ_WORKERS="${ARQ_WORKERS:-2}"
SPLIT_NUMBER="${SPLIT_NUMBER:-1}"

echo "[run_arq_ragflow] ${ARQ_WORKERS} 个 worker，消费 rag_queue:split_${SPLIT_NUMBER}"

python -m arq_tasks.run_workers -t ragflow -n "$ARQ_WORKERS" -p "$SPLIT_NUMBER" &
CHILD=$!

# docker stop 发的是 TERM，而 run_workers 的优雅退出分支挂在 SIGINT（等价 Ctrl+C）上；
# 这里把 TERM 转成 INT 传下去，让正在解析的文档走完当前阶段再退出（状态机停在原阶段，
# 重启后由页面重试或重新投递接续，不会出现「进度到 60% 但状态永远在跑」）。
trap 'kill -INT "$CHILD" 2>/dev/null || true' INT TERM

wait "$CHILD"
