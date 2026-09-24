# 🐉 DRAGON-AI — AI 中台

> **如果觉得项目不错，请帮忙点个 Star，谢谢。** ⭐
> [![Star this repo](https://img.shields.io/github/stars/Anton2025-11-06/DRAGON-AI?style=social)](https://github.com/Anton2025-11-06/DRAGON-AI)

> 作者微信：![img.png](img.png)

> 在线体验：http://47.111.117.95:12345   viewer/viewer

> 本 README 与代码同步维护：**标「已开发」的都能跑，标「开发中」「规划中」的请当作不可用**。
> 中文 / [English](README.en.md)。

---

## 一、平台能力清单

### 1. 权限与运营底座（企业级 RBAC）

| 能力 | 说明 |
|---|---|
| 用户 / 角色 / 菜单 / 按钮权限点 | 权限点即菜单（`menu_type=3`），后端 `@has_permission` 逐接口校验，ADMIN 直放 |
| 部门与数据范围 | 五级数据权限（全部 / 本部门及以下 / 本部门 / 仅本人 / 自定义部门集），部门主管可管本部门成员 |
| 登录鉴权 | JWT 签名 + Redis 登录态（`login:{jti}`），退出即时失效；密码加密存储、登录限流防爆破 |
| 操作日志审计 | 全链路 `x-trace-id`，敏感字段自动脱敏，按 traceId 串联一次请求的全部落库 |
| 在线用户 / ARQ 监控 | 在线会话踢出；worker 节点存活、队列积压、切片数在线调整 |
| 限流配置中心 | 模块桶级 QPS/日限额，Redis 原子计数，网关热加载 |



### 2. AI 模型网关与模型广场

- **OpenAI 兼容统一入口** `POST /api/model`：api-key 鉴权 → 模型一致性校验 → QPS + 日限额 → 厂商直连 → 流式 SSE / 非流式 JSON 双形态
- **12 类模型能力 × 3 家供应商**（OpenAI 兼容 / 通义千问 / 智谱）：文生文、向量、重排、图片/视频理解、OCR、文生图、文生视频、图生视频、文生音频、音频转文字等
- **模型能力位**：`supports_stream` / `supports_thinking` / `supports_function_call` 在模型管理登记，未登记的能力引擎不外发入参
- **模型广场全流程**：接入（直连/非直连、后缀拼接）→ 申请 → 审批 → API Key 发放/回收 → 一键连通性测试 → 动态上下线（Redis 缓存即时刷新）
- **模型体验**：网页端多模型对话，流式输出、思考链、工具调用结果展示



### 3. 工作流 / 智能体编排

- **可视化画布**：`{nodes, edges}` 编辑、草稿 / 发布 / 版本快照 / 回滚 / 复制、发布前严格图校验（连通性、端口、必填参数、环路）
- **21 类节点**：边界（START/END）、AI（LLM / 问题分类器 / 参数提取器）、控制流（IF_ELSE / LOOP / PARALLEL / 变量赋值 / 变量聚合 / 指定回复 / **审批**）、数据（模板 / 代码 / 列表处理 / 文档提取 / 知识检索）、外部（HTTP / 工具 / MCP 工具 / **子工作流**）
- **执行契约收敛为三个概念**：`executionId`（一次执行的全部身份）、`pendingApprovals`（当前欠的审批）、`submit`（唯一入口的唯一动作）。首跑、续跑、重跑、答审批都是同一个 `POST /workflow-executions/submit`
- **人工审批（含嵌套）**：审批节点在节点边界挂起等结论，快照落库、跨进程恢复；子工作流的审批会冒泡到主执行，`approvalToken` 一次性凭据
- **实时事件**：WebSocket（预览页同步驱动）+ SSE（第三方长连接）+ DB 回放，帧带 `pauseGeneration` 代次防串台
- **可靠性**：暂停 TTL 看门狗回收、单节点超时保护、取消、任务全局幂等（重复投递被拒）
- **多轮记忆**：同一 `executionId` 内的大模型对话历史，可选本节点/指定节点/整条工作流范围，超限支持丢弃与自动压缩
- **函数调用**：LLM 节点可挂工具 / MCP 连接 / 子工作流，参数定义执行时现读，模型自己决定调不调
- **资源中心**：技能（SKILL.zip 上传/预览/在线编辑/启停）、工具（动态 Python 函数 + 沙箱）、MCP 连接（`tools/list` 动态发现）、工作流模板（服务首启自种）
- **对外提供服务**：API Key 元数据（限流/过期/启停）+ 四条开放端点（submit / 查状态 / SSE / cancel）



### 4. 执行引擎与基础设施

- **异步执行**：arq + Redis 分片队列（`workflow_queue:split_{N}`），按 `crc32(executionId)` 稳定路由，多 worker 横向扩展
- **统一存储抽象**：`StorageBackend` 本地目录 / 阿里云 OSS（官方 SDK V2 异步客户端）两种实现，按 Nacos `storage:` 段或环境变量切换；**无盘化**——后端只搬字节，文档解析在内存里做
- **配置与注册**：Nacos 一个地方管所有环境差异（数据库/Redis/存储/向量库），服务启动即注册 + 拉配置，代码里不写死任何环境地址
- **可观测**：loguru + `x-trace-id` 贯穿网关→服务→worker

---

## 二、开发进度

### ✅ 已开发（可直接使用）

| 模块 | 服务 | 内容 |
|---|---|---|
| 系统管理 | `service_system` :9001 | RBAC 全套、模型广场与审批、API Key、操作日志、在线用户、限流配置、ARQ 监控、网关路由配置 |
| 登录注册 | `service_login` :9004 | 登录 / 注册 / 登出 / 刷新令牌（滑动续期）、JWT + Redis 会话 |
| 业务网关 | `service_gateway` :18000 | 服务路由转发（Nacos 发现）、JWT 鉴权、模块限流、操作日志、trace-id、API Key 鉴权翻译 |
| AI 模型网关 | `service_gateway` | `/api/model` OpenAI 兼容入口、12 类能力分发、流式透传、双层限流 |
| 工作流编排 | `service_workflow` :9003 | 画布、21 类节点、submit 契约、审批与嵌套、SSE/WS、快照恢复、超时与看门狗、记忆、工具/MCP/技能/沙箱、模板、API Key 开放 |
| 异步执行 | `arq_tasks` | 分片队列、多进程 worker、健康上报、幂等投递 |
| 统一存储 | `common_storage` | 本地 / 阿里云 OSS 双实现、预签名 URL、无盘化读写 |
| 前端 | `ui-ai` :5666 | vben v5 单页应用：以上全部模块的界面 |

### 🔄 开发中（代码在跑，但不可作为可用能力）

| 模块 | 现状 |
|---|---|
| 知识库 `service_rag` :9002 | 库表与 kb / 检索接口的骨架已建，但依赖的 `common_rag`（切块/关键词/RRF）与 `arq_tasks.tasks.vectorize` 尚未落地，**服务当前 import 即失败**；Milvus 客户端层已备 |
| 知识检索节点 | 工作流 `KNOWLEDGE_RETRIEVAL` 执行器已实现，表单里标灰不可选，等知识库就绪 |
| harness 智能体 | 技能中心、工具中心、MCP 中心的接口与界面在工作流服务内已可用；独立的自主规划 agent 循环未落地，端口 :9005 / :9006 与常量已预留 |

### 📋 规划中（只有空包骨架）

`service_datasets`（数据集）、`service_train`（训练）、`service_inference`（推理）、`service_eval_model`（评估）、`service_notebook`。
这一批只预留了包目录、限流桶名与网关服务别名，没有任何接口，进度表上不要当能力看。

---

## 三、部署方式

### 3.1 环境要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | 3.11+ | 后端 |
| Node.js / pnpm | 20.12+ / 10+ | 前端构建 |
| MySQL | 8.x（5.7 亦可） | 业务数据 |
| Redis | 5+（建议 7.x） | 登录态 / 限流 / 队列 / 配置缓存 |
| Nacos | 2.x | 注册中心 + 配置中心，**必须有**，所有环境差异都在里面 |
| 阿里云 OSS | 可选 | 配 `storage.type=oss` 后启用 |
| LibreOffice | 可选 | 仅工作流解析老格式 `.doc/.ppt` 需要 |

### 3.2 新环境初始化（四步）

**1) 建库建表灌种子** —— 一份文件搞定：24 张业务表 + 角色 / 管理员 / 部门 / 菜单 / 按钮权限点。

```bash
# 换库名只改文件 PART 0 的两处（CREATE DATABASE + USE）
# 必须单连接执行（菜单种子用 @会话变量 记父级 id），mysql CLI 天然满足
# 重复执行安全：建表 IF NOT EXISTS，种子全 INSERT IGNORE，不会把现场改过的口令与菜单排序冲回去
mysql --default-character-set=utf8mb4 -h <host> -u root -p < sql/v2_init.sql
```

**2) 在 Nacos 建配置**，`data_id` = 服务名：`service_system` / `service_login` / `service_workflow` /
`service_gateway` / `arq_workflow`，内容照抄 `sql/init_nacos.yaml` 改连接信息。

> ★ `arq_workflow` 段必须有 `redis.url`，且与 `service_workflow` 用的 Redis 同源——否则任务投了没人消费。
> 只有工作流要传文件到对象存储时才需要 `storage:` 段，不配即本地目录。

**3) 起后端** —— 四个服务 + worker，见 3.3（本机）或 3.4（容器）。

**4) 起前端** —— `pnpm build:antd` 出静态产物交给 nginx，把 `/api` 反代到网关；用 3.4 的前端镜像就直接带上 `GATEWAY_URL`。

初始账号 `admin / Admin@123`，**首次登录后立刻改密码**。

存量老库要跟上最新的列与权限点，用 `sql/old/` 下的增量脚本逐条执行（每个脚本头部写清了改哪张表）；
`sql/old/new_init_.sql` 与 `sql/old/workflow_schema.sql` 已被 `v2_init.sql` 取代，只作为来源参考保留。

### 3.3 本机开发启动

```bash
pip install -r requirements.txt

python -m service.service_login.login          # :9004
python -m service.service_system.system        # :9001
python -m service.service_workflow.workflow    # :9003
python -m service.service_gateway.gateway      # :18000
python -m arq_tasks.run_workers -n 4 -p 1      # arq worker：4 进程消费切片 1

cd ui-ai && pnpm install && pnpm dev:antd      # :5666（vite 已配 /api → 127.0.0.1:18000 代理）
```

### 3.4 Docker 部署

镜像只有一个后端镜像 + 一个前端镜像，**起哪个进程由注入的环境变量决定**：

```bash
# 构建（上下文都是仓库根目录）
docker build -f docker/Dockerfile     -t dragon-ai-backend:latest .
docker build -f docker/Dockerfile.ui  -t dragon-ai-ui:latest      .

# 单跑一个服务：SERVICE_NAME 决定容器里起哪个进程
docker run -d --name wf --init -p 9003:9003 \
  -e SERVICE_NAME=service_workflow \
  -e NACOS_ADDR=10.0.0.9:8848 -e NACOS_NAME=nacos -e NACOS_PASSWD=xxx \
  -e service_workflow_port=9003 -e WORKERS=4 \
  dragon-ai-backend:latest

# 起 arq worker：换 SERVICE_NAME 即可复用同一个镜像
docker run -d --name arq --init \
  -e SERVICE_NAME=arq_workflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=4 -e SPLIT_NUMBER=1 \
  dragon-ai-backend:latest

# 起前端：GATEWAY_URL 告诉 nginx 把 /api 转发到哪
docker run -d --name ui --init -p 8080:80 \
  -e GATEWAY_URL=http://10.0.0.9:18000 \
  dragon-ai-ui:latest
```

一键起全部：

```bash
cp docker/.env.example docker/.env   # 填 NACOS_ADDR
docker compose -f docker/docker-compose.yml up -d --build
# 浏览器开 http://<host>:8080
```

| 变量 | 作用 | 默认 |
|---|---|---|
| `SERVICE_NAME` | 容器里起哪个进程：`service_login`/`service_system`/`service_gateway`/`service_workflow`/`service_rag`/`arq_workflow` | 无（必填） |
| `NACOS_ADDR` `NACOS_NAME` `NACOS_PASSWD` `NACOS_NS_ID` | 注册中心与配置中心 | 无 |
| `<服务名>_port` | 覆盖监听端口（小写服务名，代码里读的就是这个格式） | 各服务默认端口 |
| `WORKERS` | 微服务 gunicorn worker 数 | 4 |
| `ARQ_WORKERS` / `SPLIT_NUMBER` | worker 进程数 / 消费的队列切片号 | 4 / 1 |
| `GATEWAY_URL` / `LISTEN_PORT` | 前端容器：网关地址 / 监听端口 | `http://127.0.0.1:18000` / 80 |

**启动脚本统一在 `scripts/`**，容器里和裸机跑的是同一份：

| 脚本 | 说明 |
|---|---|
| `scripts/docker-entrypoint.sh` | 容器唯一入口，按 `SERVICE_NAME` 分发 |
| `scripts/run_service.sh` | gunicorn + `uvicorn.workers.UvicornWorker`，每服务默认 4 worker；生产参数：`--backlog 4096`、`--timeout 120`、`--graceful-timeout 30`、`--max-requests 50000(+jitter)`、`--worker-tmp-dir /dev/shm`、`--proxy-headers --forwarded-allow-ips '*'`、日志走 stdout |
| `scripts/run_arq_workflow.sh` | 起 N 个 arq worker，并把 `docker stop` 的 TERM 转成 INT 走优雅退出分支 |
| `scripts/run_ui.sh` | 把 `GATEWAY_URL` 渲染进 nginx 站点配置，前台起 nginx |

### 3.5 K8s

`k8s/` 目录预留，尚未编写清单。

---

## 四、技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI（全异步）· SQLAlchemy 2.0 + aiomysql · Pydantic v2 |
| 进程与部署 | gunicorn + UvicornWorker · Docker · Nacos（注册/配置中心） |
| 数据与中间件 | MySQL 8.x · Redis（登录态/限流/缓存/arq 队列）· 阿里云 OSS · Milvus（客户端层已备） |
| 异步任务 | arq 0.28，Redis 分片队列，多进程横向扩展 |
| 通信 | httpx 全局连接池（HTTP/2）· WebSocket · SSE（sse-starlette） |
| 模型接入 | 每类能力一个 `openai_impl` / `dashscope_impl` / `zhipu_impl`，统一由 `common_model.entry` 按（能力, 供应商）分发 |
| 智能体 | MCP 官方 Python SDK · 沙箱进程隔离（psutil）· 代码节点走同一沙箱；自主规划框架 deepagents 仅声明了依赖，循环未落地 |
| 向量与检索 | pymilvus 客户端层 · rank-bm25 关键词检索（知识库未接入，文档解析已在工作流侧复用） |
| 可观测 | loguru + 自研 `x-trace-id` 链路（OpenTelemetry 依赖已声明，尚未接入代码） |
| 前端 | Vue 3.5 · TypeScript · Vite · vben v5（pnpm + turbo monorepo）· ant-design-vue · Vue Flow 画布 · CodeMirror/Monaco |
| 鉴权 | JWT + Redis 会话 · API Key 双通道 · RBAC 权限点 |

---

## 五、目录结构

```
├── common/                       # 公共层：所有服务共享，不放业务逻辑
│   ├── common_app/               #   create_app 统一引导工厂（lifespan + 中间件 + 异常）
│   ├── common_nacos/             #   Nacos 注册发现与配置拉取
│   ├── common_middleware/        #   RequestLog / TokenCheck / RateLimit / OperateLog / 异常处理
│   ├── common_permission/        #   @has_permission 权限点校验
│   ├── common_model/             #   12 类模型能力 × 3 家供应商的调用实现与 entry 分发
│   ├── common_storage/           #   StorageBackend：本地 / 阿里云 OSS，只搬字节
│   ├── common_mysql|redis|milvus|httpx|arq/  # 连接池与队列封装
│   ├── common_entity/            #   统一响应体 ApiResponse / RBAC 实体
│   ├── common_log|utils|exception|constants|threadpool/
├── service/                      # 业务微服务，一服务一端口
│   ├── service_gateway/          #   :18000 路由转发 + 鉴权 + 限流 + AI 模型网关
│   ├── service_system/           #   :9001  RBAC + 模型广场 + 运营监控
│   ├── service_login/            #   :9004  登录注册
│   ├── service_workflow/         #   :9003  编排域
│   │   ├── execution/            #     执行域：轮次请求 / 暂停状态 / 审批投影 / 执行树 / submit 解析
│   │   ├── workflow_engine/      #     图执行引擎（nodes / 上下文 / 校验 / 快照）
│   │   ├── routers|services|models|schemas|utils/
│   ├── service_rag/              #   :9002  知识库（开发中）
│   └── service_datasets|train|inference|eval_model|notebook/   # 规划中，空包
├── arq_tasks/                    # worker 配置 + 工作流执行任务 + 多进程启动脚本
├── ui-ai/                        # 前端 vben v5 monorepo（apps/web-antd 是实际使用的 app）
├── scripts/                      # 启动脚本：容器与裸机共用同一份
├── docker/                       # Dockerfile（后端）· Dockerfile.ui（前端）· compose · nginx 模板
├── sql/                          # v2_init.sql（新环境唯一入口）· init_nacos.yaml（配置样例）· old/（历史增量脚本）
├── docs/                         # 设计文档与截图
└── k8s/                          # 预留
```

---

## 六、系统架构

```
┌───────────────────────────────────────────────────────────────────┐
│  前端 ui-ai  Vue3 + vben v5（dev :5666 / 生产 nginx，见 Dockerfile.ui）│
│  只发 /api/**，同源代理（vite dev proxy 或 nginx 反代），浏览器不跨域   │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  service_gateway :18000   —— 唯一对外入口                            │
│  路由规范 /api/{service_name}/{path} → Nacos 服务发现 → 转发           │
│  JWT 鉴权 · 模块限流 · 操作日志 · x-trace-id · API Key 鉴权翻译          │
│  /api/model：OpenAI 兼容模型入口（api-key → 限流 → 厂商直连 → SSE）      │
└──────┬──────────────┬───────────────┬───────────────┬─────────────┘
       ▼              ▼               ▼               ▼
  system :9001    login :9004    workflow :9003    rag :9002(开发中)
  RBAC/模型广场    JWT+会话       画布/submit/审批    知识库
  审计/监控/限流                 工具/MCP/技能       ↓（未接入）
                                     │
                                     ▼ 异步执行（投 Redis 分片队列）
                          ┌────────────────────────┐
                          │ arq worker × N × 切片   │  execute_workflow
                          │ 快照落库 / 跨进程恢复     │
                          └────────────────────────┘
┌───────────────────────────────────────────────────────────────────┐
│ 基础设施  Nacos（注册 + 全部业务配置）· MySQL · Redis · 阿里云 OSS（可选）│
└───────────────────────────────────────────────────────────────────┘
```

### 几条贯穿全局的设计约定

1. **一个 `create_app` 引导所有服务**（`common_app.bootstrap`）：Nacos 注册、Redis/MySQL/存储/arq 连接池、中间件栈、异常处理全在 lifespan 里，服务自己只声明路由和开关。依赖初始化失败会回滚 Nacos 注册，不留下不可用实例。
2. **环境差异只存在于 Nacos**：代码里不写死任何连接串；`data_id` 就是服务名，端口用 `<服务名>_port` 环境变量覆盖。
3. **权限点即菜单**：`tb_menu` 里 `menu_type=3` 的行就是权限点，一份数据同时驱动前端按钮显隐与后端 `@has_permission`；`sql/v2_init.sql` 的种子按后端实际校验的清单生成，两边不会脱节。
4. **每个事实只有一个 owner、一份副本**（执行域契约，见 `docs/workflow-execution-contract.md`）：对外只有 `executionId` / `pendingApprovals` / `submit` 三个概念，内部坐标（子执行 id、暂停范围、代次）只在排障接口出现。
5. **不做旧口径保留**：被取代的端点、字段、注释直接删除，不留 `@deprecated` 薄壳，避免新旧双轨。
6. **存储只搬字节**：业务侧只有 `upload/download/delete/exists` 四个方法，换后端不改代码；不提供「取本地路径」的能力，因此文档解析也在内存做。
7. **统一出入参格式**：响应一律 `ApiResponse{code, message, data}`，异常经全局处理器归一化，模型调用失败按上游状态码透传。

---

## 七、路线图

1. 知识库链路落地：`common_rag`（切块 / 关键词 / RRF）+ 向量化任务 + Milvus 建表，点亮 `KNOWLEDGE_RETRIEVAL` 节点
2. harness 智能体：在技能 / 工具 / MCP 三个资源中心之上做自主规划循环（`:9005` / `:9006`）
3. 数据集 / 训练 / 推理 / 评估 / Notebook 模块落地
4. K8s 清单与 Helm chart

## 八、相关文档

| 文档 | 内容 |
|---|---|
| `docs/workflow-execution-contract.md` | 工作流执行域的对外契约与内部设计（**加任何对外字段必须先改这份**） |
| `docs/workflow-approval-memory.md` | 审批与记忆的状态机、需求评审结论与实现补记 |
| `docs/workflow-design.md` | 编排模块设计 |
| `sql/v2_init.sql` | 新环境数据库初始化唯一入口 |
| `sql/init_nacos.yaml` | Nacos 配置样例（含 storage 段每个字段的含义） |

---

<p align="center">如果这个项目对你有帮助，欢迎点个 Star ⭐ · Issues 与 PR 都欢迎</p>
