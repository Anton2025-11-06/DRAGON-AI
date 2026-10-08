# 🐉 DRAGON-AI — AI 中台

> **如果觉得项目不错，请帮忙点个 Star，谢谢。** ⭐
> [![Star this repo](https://img.shields.io/github/stars/Anton2025-11-06/DRAGON-AI?style=social)](https://github.com/Anton2025-11-06/DRAGON-AI)

> 作者微信：![img.png](docs/img.png)

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
| 资源级 ACL 授权（全平台一套引擎） | **七类资源**：知识库 / 文档 / 工作流 / 工作流模板 / 工具 / 技能 / MCP 连接；按**动作粒度**（view / use / edit / delete / share / upload / reparse / graph / chunk / preview / content / doc_delete / eval / chat / copy / apikey / template / history / export / test / tools / rename / replace 共 20+ 动作码）授权到 **用户 / 角色 / 部门(含下级) / 用户组 / 全员** 五类主体；判定 = ADMIN ∪ 归属人 ∪ 数据范围 ∪ 显式授权四档并集，`service_system/acl_router` 是全系统唯一读写口，`common_permission/resource_guard` 单点求值，列表下推 / 行内按钮 / 403 三处共用同一份规则，绝不允许各处再手写一份 |
| 登录鉴权 | JWT 签名 + Redis 登录态（`login:{jti}`），退出即时失效；密码加密存储、登录限流防爆破 |
| 操作日志审计 | 全链路 `x-trace-id`，敏感字段自动脱敏，按 traceId 串联一次请求的全部落库 |
| 在线用户 / ARQ 监控 | 在线会话踢出；worker 节点存活、队列积压、切片数在线调整，**workflow / ragflow / graphflow 三条流水线在下拉里各选各的** |
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



### 4. 知识库与 RAG（含检索评测）

- **三类知识库**：文档库 / 图文库 / 音视频库分型配置，库级选择默认检索参数与生成模型；库表 + ES 双索引 + Neo4j 图谱三份状态由服务层统一维护
- **文档解析与切片**：`common_file_parser` 双引擎（native：pypdf/docx/xlsx… · minerU 私有化 HTTP）+ auto 调度、8 种分块策略，切片经向量模型入 ES `rag_knowledge_chunk`
- **混合检索**：BM25 + 1024 维 dense_vector kNN 按请求动态加权融合 · 重排模型按请求选（不选则不重排）· 可叠加 LightRAG 式图谱检索一路（实体/关系投影入 `rag_kg_vector`）
- **知识图谱流水线（graphflow，独立于 ragflow）**：文档抽取实体/关系写 Neo4j，并可做图谱向量投影作为检索增强。图谱要对一篇文档的**全部**切片逐批走大模型，单篇耗时远长于解析/分块/向量化，若与解析挤在同一队列，几篇大文档就能把 rag worker 的并发槽位占满；因此拆到独立的 `graphflow_queue:split_{N}` 与 `arq_graphflow` worker，两边可各自扩缩容、互不拖慢。Redis 与后端仍复用 `arq_ragflow` 那一整段 Nacos 配置（同一实例），只队列名/worker 名独立。支持失败重跑走断点续抽（`resume` 只补没抽成的批，不重烧已完成的大模型调用）
- **资源级 ACL 授权**：知识库 / 文档只是全平台七类 ACL 资源里的两类，按动作粒度（查看 / 使用 / 编辑 / 上传 / 构建向量 / 构建图谱 / 切片管理 / 评测 / 转授 …）授权到用户 / 角色 / 部门 / 用户组 / 全员；文档行判不过时回看父库那一行的「库内文件」组（一次授权整库生效），上层路由只校验一次、底层按 `kb_id` 白名单裸查（详见「一.1 权限与运营底座」）
- **RAGAS 知识评测**：指定知识库、用户配置问答对（手工录入多条，或下载 Excel 模板批量填写后上传导入），并发生成答案并对 5 大指标（faithfulness / answer_relevancy / context_precision / context_recall / answer_correctness）+ 三段耗时（召回 / 生成 / 打分）打分，结果落库存历史，评测走 ARQ 后台异步执行
- **开放 API**：对外提供知识库检索端点（API Key 鉴权 + 限流），单次最多 100 文件



### 5. 执行引擎与基础设施

- **异步执行**：arq + Redis 分片队列，**三条流水线独立消费、互不抢占**——`workflow_queue:split_{N}` / `ragflow_queue:split_{N}` / `graphflow_queue:split_{N}`；按 `crc32(task_id) % 切片数` 稳定路由，同一 execution_id / doc_id 恒定落同一切片，多进程横向扩展。切片数存 Redis 而非 Nacos，监控页改完即时生效
- **统一存储抽象**：`StorageBackend` 本地目录 / 阿里云 OSS（官方 SDK V2 异步客户端）两种实现，按 Nacos `storage:` 段或环境变量切换；**无盘化**——后端只搬字节，文档解析在内存里做
- **配置与注册**：Nacos 一个地方管所有环境差异（数据库/Redis/存储/向量库），服务启动即注册 + 拉配置，代码里不写死任何环境地址
- **可观测**：loguru + `x-trace-id` 贯穿网关→服务→worker

---

## 二、开发进度

### ✅ 已开发（可直接使用）

| 模块 | 服务 | 内容 |
|---|---|---|
| 系统管理 | `service_system` :9001 | RBAC 全套、**资源级 ACL 授权的唯一读写口（acl_router，7 类资源共享）**、模型广场与审批、API Key、操作日志、在线用户、限流配置、ARQ 三线监控（workflow/ragflow/graphflow）、网关路由配置 |
| 登录注册 | `service_login` :9004 | 登录 / 注册 / 登出 / 刷新令牌（滑动续期）、JWT + Redis 会话 |
| 业务网关 | `service_gateway` :18000 | 服务路由转发（Nacos 发现）、JWT 鉴权、模块限流、操作日志、trace-id、API Key 鉴权翻译 |
| AI 模型网关 | `service_gateway` | `/api/model` OpenAI 兼容入口、12 类能力分发、流式透传、双层限流 |
| 工作流编排 | `service_workflow` :9003 | 画布、21 类节点、submit 契约、审批与嵌套、SSE/WS、快照恢复、超时与看门狗、记忆、工具/MCP/技能/沙箱、模板、API Key 开放；工作流/模板/工具/技能/MCP 五类资源均接入 ACL |
| 知识库 / RAG | `service_rag` :9002 | 三类库配置、文档上传与解析进度、混合检索（BM25+dense+重排+图谱增强）、Neo4j 图谱流水线（走独立的 graphflow worker）、知识库/文档两类资源按平台 ACL 鉴权、RAGAS 知识评测、开放检索 API |
| 知识检索节点 | `service_workflow` | `KNOWLEDGE_RETRIEVAL` 节点已接通（HTTP 调 `service_rag` 检索），画布节点表单可选可配 |
| 异步执行 | `arq_tasks` | 分片队列、多进程 worker（**workflow / ragflow / graphflow 三条流水线**，`-t workflow\|ragflow\|graphflow`）、健康上报、幂等投递 |
| 统一存储 | `common_storage` | 本地 / 阿里云 OSS 双实现、预签名 URL、无盘化读写 |
| 前端 | `ui-ai` :5666 | vben v5 单页应用：以上全部模块的界面 |

### 🔄 开发中（代码在跑，但不可作为可用能力）

| 模块 | 现状 |
|---|---|
| harness 智能体 | 技能中心、工具中心、MCP 中心的接口与界面在工作流服务内已可用；独立的自主规划 agent 循环未落地，端口 :9005 / :9006 与常量已预留 |

### 📋 规划中（只有空包骨架）

`service_datasets`（数据集）、`service_train`（训练）、`service_inference`（推理）、`service_eval_model`（评估）、`service_notebook`。
这一批只预留了包目录、限流桶名与网关服务别名，没有任何接口，进度表上不要当能力看。

---

## 三、部署方式

### 3.1 环境要求

| 依赖 | 版本           | 用途 |
|---|--------------|---|
| Python | 3.11+        | 后端 |
| Node.js / pnpm | 20.12+ / 10+ | 前端构建 |
| MySQL | 8.x（5.7 亦可）  | 业务数据 |
| Redis | 5+（建议 7.x）   | 登录态 / 限流 / 队列 / 配置缓存 |
| Nacos | 2.x+         | 注册中心 + 配置中心，**必须有**，所有环境差异都在里面 |
| 阿里云 OSS | 可选           | 配 `storage.type=oss` 后启用 |

### 3.2 新环境初始化（四步）

**1) 建库建表灌种子** —— 一份文件搞定：28 张业务表 + 角色 / 管理员 / 部门 / 菜单 / 按钮权限点。

```bash
# 换库名只改文件 PART 0 的两处（CREATE DATABASE + USE）
# 必须单连接执行（菜单种子用 @会话变量 记父级 id），mysql CLI 天然满足
# 重复执行安全：建表 IF NOT EXISTS，种子全 INSERT IGNORE，不会把现场改过的口令与菜单排序冲回去
mysql --default-character-set=utf8mb4 -h <host> -u root -p < sql/v2_init.sql
```

**2) 在 Nacos 建配置**，`data_id` = 服务名：`service_system` / `service_login` / `service_workflow` /
`service_gateway` / `service_rag` / `arq_workflow` / `arq_ragflow`，内容照抄 `sql/init_nacos.yaml` 改连接信息。

> ★ `arq_workflow` 段必须有 `redis.url`，且与 `service_workflow` 用的 Redis 同源——否则任务投了没人消费。
> ★ `arq_ragflow` 同理：它的 `redis.url` 是 rag 队列的 Redis，而 `mysql/storage/es/neo4j` 必须与 `service_rag` 同实例——
>   原件在 API 进程写进对象存储、在 worker 里读，两处不一致就是「上传成功、worker 找不到文件」。
>   本段的 `worker:` 子段是 rag worker 的并发/超时（省略则用代码默认值）。
> ★ **graphflow 不新增 data_id**：知识图谱 worker 与 ragflow 同读 `arq_ragflow` 这一段 Nacos 配置（同 Redis
>   同后端），只是队列名 `graphflow_queue` 与 worker 名 `graphflow_worker` 独立；拆队列是为了隔离并发，不是隔离存储。
> 只有工作流要传文件到对象存储时才需要 `storage:` 段，不配即本地目录。

**3) 起后端** —— 五个服务 + 三条 worker 流水线，见 3.3（本机）或 3.4（容器）。

**4) 起前端** —— `pnpm build:antd` 出静态产物交给 nginx，把 `/api` 反代到网关；用 3.4 的前端镜像就直接带上 `GATEWAY_URL`。

初始账号 `admin / Admin@123`，**首次登录后立刻改密码**。

存量老库要跟上最新的列与权限点，按版本跑根目录下的增量脚本（`sql/v2_2_kb_upgrade.sql` 知识库升级、`sql/v2_3_rag_eval_upgrade.sql` 知识评测）；
更早的历史增量在 `sql/old/` 下逐条执行（每个脚本头部写清了改哪张表）；`sql/old/new_init_.sql` 与 `sql/old/workflow_schema.sql` 已被 `v2_init.sql` 取代，只作为来源参考保留。

### 3.3 本机开发启动

```bash
pip install -r requirements.txt

python -m service.service_login.login          # :9004
python -m service.service_system.system        # :9001
python -m service.service_workflow.workflow    # :9003
python -m service.service_rag.rag              # :9002（知识库：需 ES/Neo4j）
python -m service.service_gateway.gateway      # :18000

# arq worker：-t 必填，三条流水线各自一个进程组（队列完全隔离，互不偷任务）
python -m arq_tasks.run_workers -t workflow   -n 4 -p 1    # 工作流：4 进程消费 workflow_queue:split_1
python -m arq_tasks.run_workers -t ragflow    -n 2 -p 1    # 知识库解析：2 进程消费 ragflow_queue:split_1
python -m arq_tasks.run_workers -t graphflow  -n 2 -p 1    # 知识图谱：2 进程消费 graphflow_queue:split_1（比解析更慢，单独拆队）

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

# 起知识库摄取 worker（解析/分块/向量化，纯 CPU）
docker run -d --name arq-rag --init \
  -e SERVICE_NAME=arq_ragflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=2 -e SPLIT_NUMBER=1 \
  dragon-ai-backend:latest

# 起知识图谱构建 worker（graphflow 队列，逐批走大模型、比摄取更耗时）
docker run -d --name arq-graph --init \
  -e SERVICE_NAME=arq_graphflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=2 -e SPLIT_NUMBER=1 \
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
| `SERVICE_NAME` | 容器里起哪个进程：`service_login`/`service_system`/`service_gateway`/`service_workflow`/`service_rag`/`arq_workflow`/`arq_ragflow`/`arq_graphflow` | 无（必填） |
| `NACOS_ADDR` `NACOS_NAME` `NACOS_PASSWD` `NACOS_NS_ID` | 注册中心与配置中心 | 无 |
| `<服务名>_port` | 覆盖监听端口（小写服务名，代码里读的就是这个格式） | 各服务默认端口 |
| `WORKERS` | 微服务 gunicorn worker 数 | 4 |
| `ARQ_WORKERS` / `SPLIT_NUMBER` | worker 进程数 / 消费的队列切片号 | 4 / 1 |
| `RAG_ARQ_WORKERS` / `RAG_SPLIT_NUMBER` | compose 里给 `arq_ragflow` 容器覆盖上面两个变量（解析与工作流分开扩容） | 2 / 1 |
| `GRAPH_ARQ_WORKERS` / `GRAPH_SPLIT_NUMBER` | compose 里给 `arq_graphflow` 容器同样覆盖（图谱与解析各自伸缩） | 2 / 1 |
| `GATEWAY_URL` / `LISTEN_PORT` | 前端容器：网关地址 / 监听端口 | `http://127.0.0.1:18000` / 80 |

**启动脚本统一在 `scripts/`**，容器里和裸机跑的是同一份：

| 脚本 | 说明 |
|---|---|
| `scripts/docker-entrypoint.sh` | 容器唯一入口，按 `SERVICE_NAME` 分发 |
| `scripts/run_service.sh` | gunicorn + `uvicorn.workers.UvicornWorker`，每服务默认 4 worker；生产参数：`--backlog 4096`、`--timeout 120`、`--graceful-timeout 30`、`--max-requests 50000(+jitter)`、`--worker-tmp-dir /dev/shm`、`--proxy-headers --forwarded-allow-ips '*'`、日志走 stdout |
| `scripts/run_arq_workflow.sh` | 起 N 个工作流 arq worker（`-t workflow`），并把 `docker stop` 的 TERM 转成 INT 走优雅退出分支 |
| `scripts/run_arq_ragflow.sh` | 同上但 `-t ragflow`：知识库摄取 worker，进程数默认 2（并发大头在 Nacos 的 `arq_ragflow.worker.max_jobs`） |
| `scripts/run_arq_graphflow.sh` | 同上但 `-t graphflow`：知识图谱构建 worker，独立队列。Redis 与后端仍读 `arq_ragflow` 那一整段 Nacos 配置，只队列/worker 名独立；单篇 job_timeout 4 小时（比解析长），失败不重试，重跑时默认断点续抽 |
| `scripts/run_ui.sh` | 把 `GATEWAY_URL` 渲染进 nginx 站点配置，前台起 nginx |

### 3.5 K8s

`k8s/` 目录预留，尚未编写清单。

---

## 四、技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI（全异步）· SQLAlchemy 2.0 + aiomysql · Pydantic v2 |
| 进程与部署 | gunicorn + UvicornWorker · Docker · Nacos（注册/配置中心） |
| 数据与中间件 | MySQL 8.x · Redis（登录态/限流/缓存/进度的 arq 三队列）· 阿里云 OSS · Elasticsearch 8.x · Neo4j 5.x |
| 异步任务 | arq 0.28，Redis 分片队列；workflow / ragflow / graphflow **三条流水线完全隔离消费**（`-t workflow\|ragflow\|graphflow`），多进程横向扩展；图谱比解析更耗时，拆开后互不抢占槽位，graph 与 rag 共用同一份 Nacos 配置 |
| 通信 | httpx 全局连接池（HTTP/2）· WebSocket · SSE（sse-starlette） |
| 模型接入 | 每类能力一个 `openai_impl` / `dashscope_impl` / `zhipu_impl`，统一由 `common_model.entry` 按（能力, 供应商）分发 |
| 智能体 | MCP 官方 Python SDK · 沙箱进程隔离（psutil）· 代码节点走同一沙箱；自主规划框架 deepagents 仅声明了依赖，循环未落地 |
| 向量与检索 | ES 双索引 `rag_knowledge_chunk`（切片）+ `rag_kg_vector`（图谱实体/关系投影）：BM25 + 1024 维 dense_vector kNN 按请求动态加权融合 · 重排模型按请求选（不选就不重排）· 可叠加 LightRAG 式图谱检索一路 · 文本/图片/音视频共用一个向量空间 |
| 文档解析 | `common_file_parser`：native（pypdf/docx/xlsx…）· minerU（私有化 HTTP）双引擎 + auto 调度，8 种分块策略（单块上限 20000 字） |
| 知识评测 | RAGAS（SingleTurnSample + EvaluationDataset）5 大指标，自写 langchain-core 适配器（`RagasChatModel`/`RagasEmbeddings`）将本仓模型服务接入 ragas 裁判，评测走 ragflow 队列并发与后台异步 |
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
│   ├── common_permission/        #   @has_permission 权限点校验 + resource_guard 资源级 ACL（七类资源共享）
│   ├── common_model/             #   12 类模型能力 × 3 家供应商的调用实现与 entry 分发
│   ├── common_file_parser/       #   文档解析：双引擎 + 8 分块策略 + 预处理/媒体描述增强
│   ├── common_storage/           #   StorageBackend：本地 / 阿里云 OSS，只搬字节
│   ├── common_mysql|redis|es|neo4j|httpx|arq/  # 连接池与队列封装（es/neo4j 为全局异步单例）
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
│   ├── service_rag/              #   :9002  知识库：三类库配置/文档/解析进度/混合检索/图谱/评测/开放 API
│   │   ├── services/             #     kb·doc·task·parse·graph·retrieval·rag_eval·ragas_adapter·kg_search/kg_vector·rag_settings
│   │   ├── routers/              #     knowledge-bases / documents / retrieve / graph / eval / open
│   └── service_datasets|train|inference|eval_model|notebook/   # 规划中，空包
├── arq_tasks/                    # 三条流水线的 worker 配置 + 任务函数 + 多进程启动入口（run_workers -t workflow|ragflow|graphflow）
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
  system :9001    login :9004    workflow :9003    rag :9002
  RBAC/模型广场    JWT+会话       画布/submit/审批    知识库(三类库)
  审计/ACL/限流                  工具/MCP/技能       配置/文档/检索/图谱/评测/开放API
       │                             │                   │
       │                             ▼ 异步执行（Redis 分片队列，三条独立流水线）
       │        ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
       │        │ arq workflow      │  │ arq ragflow       │  │ arq graphflow     │
       │        │ worker 快照恢复  │  │ worker 解析向量化│  │ worker 图谱抽取  │
       │        └──────────────────┘  └─────────┬─────────┘  └─────────┬─────────┘
       │                                         ▼                       ▼
       │                            ES rag_knowledge_chunk · Neo4j · OSS
┌───────────────────────────────────────────────────────────────────┐
│ 基础设施  Nacos（注册 + 全部业务配置）· MySQL · Redis · 阿里云 OSS · ES · Neo4j │
└───────────────────────────────────────────────────────────────────┘
```

### 几条贯穿全局的设计约定

1. **一个 `create_app` 引导所有服务**（`common_app.bootstrap`）：Nacos 注册、Redis/MySQL/存储/arq/ES/Neo4j 连接池、中间件栈、异常处理全在 lifespan 里，服务自己只声明路由和开关（需要装配业务参数再加一个 `on_ready` 钩子）。依赖初始化失败会回滚 Nacos 注册，不留下不可用实例。
2. **上层鉴权、底层裸查**：功能权限点只在路由入口校验一次，行级可见与可操作全部由 `resource_guard` 的 ACL 判定（**全平台七类资源共享一份引擎**：知识库 / 文档 / 工作流 / 工作流模板 / 工具 / 技能 / MCP 连接，判定 = ADMIN ∪ 归属人 ∪ 数据范围 ∪ 显式授权）；ES/Neo4j/存储只按上层给定的 `kb_id` / 资源 id 白名单裸查，自己不做任何权限判断。
3. **环境差异只存在于 Nacos**：代码里不写死任何连接串；`data_id` 就是服务名，端口用 `<服务名>_port` 环境变量覆盖。
4. **权限点即菜单**：`tb_menu` 里 `menu_type=3` 的行就是权限点，一份数据同时驱动前端按钮显隐与后端 `@has_permission`；`sql/v2_init.sql` 的种子按后端实际校验的清单生成，两边不会脱节。
5. **每个事实只有一个 owner、一份副本**（执行域契约，见 `docs/workflow-execution-contract.md`）：对外只有 `executionId` / `pendingApprovals` / `submit` 三个概念，内部坐标（子执行 id、暂停范围、代次）只在排障接口出现。
6. **不做旧口径保留**：被取代的端点、字段、注释直接删除，不留 `@deprecated` 薄壳，避免新旧双轨。
7. **存储只搬字节**：业务侧只有 `upload/download/delete/exists` 四个方法，换后端不改代码；不提供「取本地路径」的能力，因此文档解析也在内存做。
8. **统一出入参格式**：响应一律 `ApiResponse{code, message, data}`，异常经全局处理器归一化，模型调用失败按上游状态码透传。

---

## 七、路线图

1. 知识库：图谱向量投影（`rag_kg_vector`）与检索融合策略持续调优；音视频库分型展示深化；graphflow worker 支持多切片横向扩容
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
| `sql/init_nacos.yaml` | Nacos 配置样例（含 storage 段每个字段的含义、三条 arq 流水线的同源关系，graphflow 复用 arq_ragflow 段） |
| `docs/RAG知识库模块开发SPEC.md` | 知识库（RAG）模块需求与设计口径（三类库、ragflow/graphflow 双流水线拆分、上层 ACL 鉴权下 ES/Neo4j 裸查） |

---

<p align="center">如果这个项目对你有帮助，欢迎点个 Star ⭐ · Issues 与 PR 都欢迎</p>
