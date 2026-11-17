#!/usr/bin/env bash
# 一键重启中台：两个 compose 全 down → 删掉本项目旧镜像 → 后端 up → 前端 up。
#
# 为什么要删镜像再 up：compose 只在「本地没有这个 tag」或显式 --build 时才重建镜像。
# 改了业务代码但没动 Dockerfile 时，直接 up 会复用旧镜像，跑的还是老代码——
# 这是「重启了却没生效」的根因。先 rmi 把 tag 摘掉，up 必然按当前源码重新 build。
#
# 两条顺序是硬约束，不是偏好：
#   1. 先 down 再 rmi：容器还在时删镜像会报 "image is being used by running container"；
#   2. 后端 up 且 gateway 真的跑起来之后才 up 前端：nginx 只在启动时解析一次
#      GATEWAY_URL，两个 compose 文件之间没有 depends_on，gateway 容器还不存在时
#      前端会以 "host not found in upstream" 直接退出（见 docker-compose.ui.yml 注释）。
#      所以下面显式等 gateway 进入 running 才继续。
#
# 用法（必须在 docker/ 里跑，compose 的 build context 是 ..）：
#   cd docker && ./restart.sh              # 变量读同目录 .env
#   TAG=v1.2 ./restart.sh                  # 换镜像 tag
#   KEEP_IMAGES=1 ./restart.sh             # 只想重启、不重新 build（跳过第 2 步）
#   PRUNE_DANGLING=1 ./restart.sh          # 顺手清掉 <none> 悬空镜像回收磁盘
#   GATEWAY_WAIT=180 ./restart.sh          # 后端首次 build 慢时放宽等待
#
# 注意：全程不加 down -v —— opensandbox-data 那个卷里是沙箱工作区的用户文件，
# 跨会话保留是设计；带 -v 会把它们连锅端掉。

set -euo pipefail

cd "$(dirname "$0")"

BACKEND_FILE=docker-compose.backend.yml
UI_FILE=docker-compose.ui.yml
KEEP_IMAGES=${KEEP_IMAGES:-0}
PRUNE_DANGLING=${PRUNE_DANGLING:-0}
GATEWAY_WAIT=${GATEWAY_WAIT:-180}

log() { printf '\033[1;36m[restart]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[restart] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 前置检查 ----

# docker compose（v2 子命令）与 docker-compose（v1 独立二进制）都兜一下
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "既没有 docker compose 也没有 docker-compose，无法继续"
fi

docker info >/dev/null 2>&1 || die "docker daemon 连不上（服务没起 / 当前用户不在 docker 组）"

for f in "$BACKEND_FILE" "$UI_FILE"; do
  [[ -f $f ]] || die "缺 compose 文件：$(pwd)/$f"
done

# NACOS_ADDR 在 backend.yml 里是 ${NACOS_ADDR:?...} 必填，不预先查的话要等到
# compose 解析到一半才炸，前面的 down 和 rmi 都白做了（镜像没了还得重新 build）。
if [[ -z ${NACOS_ADDR:-} && ! -f .env ]]; then
  die "既没导出 NACOS_ADDR，也没有 docker/.env：先 cp .env.example .env 填好 Nacos 地址"
fi

# 两个 compose 都把 dragon-ai 声明成 external: true（文件顶上那行
# ## docker network create dragon-ai 就是这条前置）。现在全 down 不影响网络，
# 但如果是换机/清过 docker 的宿主，网络不在会让 up 直接失败——而那已经是
# 「容器全停了、镜像也删了」之后，等于把平台丢在空地上。这里顺手补建（幂等）。
docker network inspect dragon-ai >/dev/null 2>&1 || {
  log "外部网络 dragon-ai 不存在，补建一个"
  docker network create dragon-ai >/dev/null
}

# ------------------------------------------------------------------- 1/4 ----
log "1/4 停止并删除容器（两个 compose，先前端后后端）"

# 不带 --remove-orphans：backend.yml 里 opensandbox 服务是注释掉的，
# 它留下的容器正属"孤儿"，会被这个参数顺手删掉——这里只删当前文件里定义的服务。
"${COMPOSE[@]}" -f "$UI_FILE" down
"${COMPOSE[@]}" -f "$BACKEND_FILE" down

# ------------------------------------------------------------------- 2/4 ----
if [[ $KEEP_IMAGES == 1 ]]; then
  log "2/4 按要求跳过删镜像（KEEP_IMAGES=1），这次不会重新 build"
else
  log "2/4 删除本项目旧镜像"
  # 只删 dragon-ai- 前缀，而不是 `docker images | grep ai`：后者会把宿主上任何
  # 名字里带 ai 的无关镜像一起带走（别的项目、沙箱用的基础镜像……），那些镜像
  # 重新拉一次可能比 build 还慢，而且删失败时会连累后面的 up。
  mapfile -t old_images < <(
    docker image ls --format '{{.Repository}}:{{.Tag}}' |
      grep -E '^dragon-ai-(backend|ui):' || true
  )

  # ${#数组[@]} 先判再进循环：bash 4.2（CentOS 7 自带）在 set -u 下展开空数组会报
  # unbound variable，直接 for 循环会在“本来就没镜像”的机器上把脚本打断
  if [[ ${#old_images[@]} -gt 0 ]]; then
    for img in "${old_images[@]}"; do
      # 不加 -f（也不因为删不掉就退出）：还占着这镜像的多半是 harness 用 docker.sock
      # 建的沙箱兄弟容器，不归这两个 compose 管；硬删或就此退出都会把平台丢在半路。
      # 留着旧镜像也不影响结果——下面 up --build 会用当前源码重打这个 tag。
      if docker rmi "$img" >/dev/null 2>&1; then
        log "  已删除 $img"
      else
        log "  ⚠ $img 还有容器在用，没删掉（继续，稍后 --build 会重打这个 tag）"
        log "    确认是谁占着：docker ps -a --filter ancestor=$img"
      fi
    done
  else
    log "  没有 dragon-ai-* 镜像，本来就不用删"
  fi

  if [[ $PRUNE_DANGLING == 1 ]]; then
    log "  清理悬空镜像（<none>，没被任何容器或 tag 引用）"
    docker image prune -f >/dev/null
  fi
fi

# ------------------------------------------------------------------- 3/4 ----
log "3/4 起后端（六个服务 + 四条 arq 流水线，首次 build 比较久）"
# --build 是双保险：镜像刚被摘掉，compose 本来也会自己 build
"${COMPOSE[@]}" -f "$BACKEND_FILE" up -d --build

log "    等 gateway 进入 running（最多 ${GATEWAY_WAIT}s），前端要用它做上游"
gateway_ready=0
for ((i = 1; i <= GATEWAY_WAIT; i++)); do
  cid=$("${COMPOSE[@]}" -f "$BACKEND_FILE" ps -q gateway 2>/dev/null || true)
  if [[ -n $cid ]] &&
    [[ $(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false) == "true" ]]; then
    log "    gateway 已运行（等了 ${i}s）"
    gateway_ready=1
    break
  fi
  sleep 1
done

if [[ $gateway_ready != 1 ]]; then
  # 到这里前端还没起：与其让 ui 容器以 host not found 崩一次，不如停在这里报清楚
  die "gateway 在 ${GATEWAY_WAIT}s 内没起来，前端暂不起。先查后端：${COMPOSE[*]} -f $BACKEND_FILE logs --tail=100 gateway"
fi

# ------------------------------------------------------------------- 4/4 ----
log "4/4 起前端"
"${COMPOSE[@]}" -f "$UI_FILE" up -d --build

# -------------------------------------------------------------------- 收尾 ----
log "当前状态："
"${COMPOSE[@]}" -f "$BACKEND_FILE" ps
"${COMPOSE[@]}" -f "$UI_FILE" ps

# .env 里的端口（compose 已加载过同名 .env，这里只是给用户看访问地址）
ui_port=${UI_PORT:-8080}
gw_port=${GATEWAY_PORT:-18000}
log "完成：前端 http://<宿主IP>:${ui_port}/  ，网关 http://<宿主IP>:${gw_port}/api"
log "看日志：${COMPOSE[*]} -f $BACKEND_FILE logs -f <服务名>   （gateway/login/system/workflow/rag/harness/arq_*）"
