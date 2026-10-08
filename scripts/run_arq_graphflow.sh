#!/usr/bin/env bash
# 启动 arq graphflow worker（文档级知识图谱构建的独立异步消费进程）。
#
# 为什么单独一条流水线（而不是并进 run_arq_ragflow.sh）：
#   图谱抽取要对一篇文档的全部切片逐批走大模型，单篇耗时远长于解析/分块/向量化。
#   和摄取挤在同一队列时，几篇大文档的图谱就能把 rag worker 的并发槽位占满，后面的
#   文档摄取只能干等。拆成 graphflow 后队列与 worker 各自独立（graphflow_queue:split_{N}），
#   图谱积压不再拖慢文档摄取，两边可各自扩缩容（同一个 run_workers 入口靠 -t 分派）。
#
# 读这些环境变量：
#   NACOS_ADDR / NACOS_NAME / NACOS_PASSWD / NACOS_NS_ID
#       worker 启动前按 data_id=arq_ragflow 从 Nacos 拉 Redis 与后端配置（graph 复用 rag 那一段），
#       必须和生产端（service_rag 的投递）同源，否则任务投了没人消费
#   ARQ_WORKERS     worker 进程数，默认 2
#   SPLIT_NUMBER    本容器消费的队列切片号，默认 1
#       队列名 = graphflow_queue:split_${SPLIT_NUMBER}；扩容多起几个容器各给一个不同切片号，
#       切片总数由监控页写入 Redis key graphflow_queue:split_number（生产端按它轮询投递）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARQ_WORKERS="${ARQ_WORKERS:-2}"
SPLIT_NUMBER="${SPLIT_NUMBER:-1}"

echo "[run_arq_graphflow] ${ARQ_WORKERS} 个 worker，消费 graphflow_queue:split_${SPLIT_NUMBER}"

python -m arq_tasks.run_workers -t graphflow -n "$ARQ_WORKERS" -p "$SPLIT_NUMBER" &
CHILD=$!

# docker stop 发的是 TERM，而 run_workers 的优雅退出分支挂在 SIGINT（等价 Ctrl+C）上；
# 这里把 TERM 转成 INT 传下去，让正在抽取的图谱走完当前批次再退出（graph_state 停在构建中，
# 重启后由页面重新发起接续，不会出现「Neo4j 只写了一半子图但状态永远在跑」）。
trap 'kill -INT "$CHILD" 2>/dev/null || true' INT TERM

wait "$CHILD"
