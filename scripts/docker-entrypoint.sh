#!/usr/bin/env bash
# 容器唯一入口：同一个镜像，起哪个进程由环境变量决定。
#
#   docker run -e SERVICE_NAME=service_workflow ...   # 起工作流服务
#   docker run -e SERVICE_NAME=arq_workflow ...        # 起 arq worker
#
# 这里只做分发，真正的启动参数分别在同目录的 run_service.sh / run_arq_workflow.sh 里，
# 本机裸跑和生产容器跑的是同一份脚本。
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:?必须注入 SERVICE_NAME（service_login|service_system|service_gateway|service_workflow|service_rag|arq_workflow）}"

case "$SERVICE_NAME" in
  arq_workflow)
    exec "$(dirname "$0")/run_arq_workflow.sh"
    ;;
  service_*)
    exec "$(dirname "$0")/run_service.sh" "$SERVICE_NAME"
    ;;
  *)
    echo "[entrypoint] 未知 SERVICE_NAME=$SERVICE_NAME" >&2
    exit 2
    ;;
esac
