#!/bin/sh
# 前端容器入口：把 docker run 注入的网关地址渲染进 nginx 配置，再前台起 nginx。
#
# 读这些环境变量：
#   GATEWAY_URL   后端网关地址，默认 http://127.0.0.1:18000
#   LISTEN_PORT   容器内监听端口，默认 80
set -eu

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:18000}"
LISTEN_PORT="${LISTEN_PORT:-80}"
TEMPLATE="/etc/nginx/ui.conf.template"
OUTPUT="/etc/nginx/conf.d/default.conf"

# 用 sed 而不是 envsubst：alpine 基础镜像不带 gettext，且 nginx 配置里全是
# $uri / $host 这类 nginx 自己的变量，envsubst 会连它们一起替换掉。
sed -e "s|__GATEWAY_URL__|${GATEWAY_URL}|g" \
    -e "s|__LISTEN_PORT__|${LISTEN_PORT}|g" \
    "${TEMPLATE}" > "${OUTPUT}"

echo "[run_ui] listen=${LISTEN_PORT} gateway=${GATEWAY_URL}"

nginx -t
exec nginx -g 'daemon off;'
