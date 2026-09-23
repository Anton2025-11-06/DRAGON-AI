# 后端镜像：五个微服务 + arq worker 共用一个镜像，起哪个进程由 SERVICE_NAME 决定。
#
# 构建（上下文是仓库根目录，.dockerignore 已排除 ui-ai 与本机状态）：
#   docker build -f docker/Dockerfile -t dragon-ai-backend:latest .
#
# 运行（注入的全是环境变量，镜像不带任何环境配置）：
#   docker run -d -e SERVICE_NAME=service_workflow -e NACOS_ADDR=10.0.0.9:8848 \
#              -p 9003:9003 dragon-ai-backend:latest
#   docker run -d -e SERVICE_NAME=arq_workflow -e NACOS_ADDR=10.0.0.9:8848 dragon-ai-backend:latest

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 只补时区数据：依赖全都有 manylinux wheel（grpcio / pymilvus / psutil / pillow 等），
# 不需要装编译工具链
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 依赖单独一层：改代码不会导致重装依赖
# 工作流的老格式文档解析（.doc/.ppt）还需要系统级 LibreOffice，只在用到该节点的环境装：
#   RUN apt-get update && apt-get install -y --no-install-recommends libreoffice && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# 整个项目进镜像（含 scripts/ 与 sql/）
COPY . .

# Windows 仓库检出的脚本可能带 CRLF，bash 会当成 "/bin/bash^M: bad interpreter"
RUN sed -i 's/\r$//' scripts/*.sh && chmod +x scripts/*.sh

# service_system 9001 / service_rag 9002 / service_workflow 9003 / service_login 9004 / gateway 18000
EXPOSE 9001 9002 9003 9004 18000

# 必须注入：SERVICE_NAME=service_login|service_system|service_gateway|service_workflow|service_rag|arq_workflow
# 常用配套变量：NACOS_ADDR NACOS_NAME NACOS_PASSWD NACOS_NS_ID WORKERS ARQ_WORKERS SPLIT_NUMBER
# 不写默认值：漏了 SERVICE_NAME 应该启动失败，而不是静默跑起一个谁都没想到的服务
CMD ["/app/scripts/docker-entrypoint.sh"]
