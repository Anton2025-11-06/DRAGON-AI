#!/usr/bin/env bash
# 启动一个后端微服务：gunicorn 管进程与优雅退出，worker 用 uvicorn 跑 FastAPI(ASGI)。
#
# 用法：run_service.sh <service_name>
#   service_name 与 common/common_constants/constant.py 里的常量一致，
#   同时也是 Nacos 的 data_id 和注册名。
#
# 读这些环境变量（都由 docker run -e 注入）：
#   NACOS_ADDR / NACOS_NAME / NACOS_PASSWD / NACOS_NS_ID
#       注册中心与配置中心，缺一个都起不来（所有配置都从 Nacos 拉，见 sql/init_nacos.yaml）
#   <service_name>_port   监听端口，覆盖默认值。注意是小写服务名，bootstrap.py 读的就是这个格式
#   WORKERS               worker 数，默认 4
#   LOG_LEVEL             日志级别，默认 info
set -euo pipefail

SERVICE="${1:?用法: run_service.sh <service_name>}"

# 项目根：gunicorn 要能 import 到 common / service 两个顶层包
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$SERVICE" in
  service_login)    MODULE=service.service_login.login;      DEFAULT_PORT=9004 ;;
  service_system)   MODULE=service.service_system.system;    DEFAULT_PORT=9001 ;;
  service_rag)      MODULE=service.service_rag.rag;          DEFAULT_PORT=9002 ;;
  service_workflow) MODULE=service.service_workflow.workflow; DEFAULT_PORT=9003 ;;
  service_gateway)  MODULE=service.service_gateway.gateway;  DEFAULT_PORT=18000 ;;
  *)
    echo "[run_service] 未知服务: $SERVICE（可选 service_login|service_system|service_rag|service_workflow|service_gateway）" >&2
    exit 2
    ;;
esac

# 端口变量名 = 服务名 + _port（create_app 里 os.environ.get(service_name + "_port", 默认值)）
PORT_VAR="${SERVICE}_port"
PORT="${!PORT_VAR:-$DEFAULT_PORT}"
WORKERS="${WORKERS:-4}"
LOG_LEVEL="${LOG_LEVEL:-info}"

echo "[run_service] $SERVICE -> 0.0.0.0:$PORT, workers=$WORKERS"

# 每个 worker 各自跑一遍 lifespan，也就是各自向 Nacos 注册一次；
# 同一 ip:port 重复注册是幂等的，只是多几份心跳，不必为此改成 preload。
exec gunicorn "${MODULE}:app" \
  --chdir "$ROOT" \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "$WORKERS" \
  --bind "0.0.0.0:${PORT}" \
  --backlog 4096 \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 30 \
  --max-requests 50000 \
  --max-requests-jitter 5000 \
  --forwarded-allow-ips '*' \
  --access-logfile - \
  --error-logfile - \
  --log-level "$LOG_LEVEL" \
  --capture-output
