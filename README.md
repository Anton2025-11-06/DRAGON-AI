# 🐉 DRAGON-AI — 可私有部署的多租户 AI 中台

> 把 **模型网关、知识库 RAG、知识图谱、工作流编排、深度探索智能体（Harness）与企业级 RBAC** 收进同一个工作区，数据、模型与权限都由你自己掌握。

> 在线体验：`120.77.58.245:12345`（viewer / viewer） · 中文 / [English](README.en.md)

> 本 README 与代码同步维护：**标「已开发」的都能跑，标「规划中」的请当作不可用**。

---

## 一、核心能力

| 模块 | 解决的问题 | 代表能力 |
|---|---|---|
| 权限与运营底座 | 多人环境下的能力、权限与运行治理 | RBAC、资源级 ACL、审计日志、限流、在线用户与 ARQ 监控 |
| 模型网关与模型广场 | 统一接入、分发、治理多供应商模型 | OpenAI 兼容入口、12 类能力 × 3 供应商、申请审批、API Key |
| 工作流编排 | 把复杂任务拆成可视化流程 | 21 类节点、submit 契约、人工审批、SSE/WS、记忆、函数调用 |
| 知识库与 RAG | 为回答提供精准检索内容 | 三类库、混合检索、图谱增强、RAGAS 评测、开放 API |
| 深度探索（Harness） | 让智能体真正执行任务并交付成果 | AgentScope ReAct + 每用户云沙箱、持久化工作区、在线预览与产物交付 |
| 执行引擎与基础设施 | 高并发、可横向扩展的运行底座 | arq 四流水线、统一存储（本地/NAS/OSS）、Nacos、trace-id |

---

## 二、能力概览

### 1. 权限与运营底座（企业级 RBAC）
- **权限点即菜单**（`menu_type=3`）：一份数据同时驱动前端按钮显隐与后端 `@has_permission`，ADMIN 直放；五级数据权限（全部 / 本部门及以下 / 本部门 / 仅本人 / 自定义部门集）。
- **资源级 ACL，全平台一套引擎**：知识库 / 文档 / 工作流 / 模板 / 工具 / 技能 / MCP 连接七类资源，按动作粒度授权到 用户 / 角色 / 部门（含下级）/ 用户组 / 全员；判定 = `ADMIN ∪ 归属人 ∪ 数据范围 ∪ 显式授权`。`acl_router` 是唯一读写口、`resource_guard` 单点求值，列表下推 / 行内按钮 / 403 三处共用同一份规则，绝不再手写第二份。
- JWT + Redis 登录态即时失效；全链路 `x-trace-id`、敏感字段自动脱敏；模块桶级限流热加载；在线会话踢出与 ARQ 四流水线监控。

### 2. AI 模型网关与模型广场
- **OpenAI 兼容统一入口** `POST /api/model`：api-key 鉴权 → 模型一致性校验 → QPS + 日限额 → 厂商直连 → 流式 SSE / 非流式 JSON。
- **12 类模型能力 × 3 家供应商**（OpenAI 兼容 / 通义千问 / 智谱）：文生文、向量、重排、图文/视频理解、OCR、文生图、文/图生视频、文生音频、音频转文字等。
- 能力位 `supports_stream / thinking / function_call` 在模型管理登记，未登记不外发入参；模型广场全流程：接入 → 申请 → 审批 → API Key → 连通性测试 → 动态上下线；网页端多模型对话体验。

### 3. 工作流 / 智能体编排
- 可视化画布 `{nodes, edges}`：草稿 / 发布 / 版本快照 / 回滚 / 复制，发布前严格图校验（连通性、端口、必填、环路）。
- **21 类节点**：边界、AI（LLM / 分类器 / 参数提取器）、控制流（IF / 循环 / 并行 / 赋值 / 聚合 / 回复 / **审批**）、数据（模板 / 代码 / 列表 / 文档提取 / 知识检索）、外部（HTTP / 工具 / MCP / **子工作流**）。
- **执行契约只有三个概念**：`executionId` / `pendingApprovals` / `submit`——首跑、续跑、重跑、答审批都是同一个 `submit`；人工审批含嵌套冒泡、`approvalToken` 一次性凭据。
- WebSocket + SSE + DB 回放、暂停 TTL 看门狗、单节点超时、任务全局幂等、多轮记忆（可丢弃/自动压缩）、函数调用（LLM 可挂工具 / MCP / 子工作流）。

### 4. 知识库与 RAG（含检索评测）
- **三类知识库**：文档库 / 图文库 / 音视频库分型配置；库表 + ES 双索引 + Neo4j 图谱三份状态由服务层统一维护。
- 解析切片：`common_file_parser` 双引擎（native：pypdf/docx/xlsx… · minerU 私有化 HTTP）+ auto 调度、8 种分块策略，切片经向量模型入 ES `rag_knowledge_chunk`。
- 混合检索：BM25 + 1024 维 dense_vector kNN 按请求动态加权 · 重排模型按请求选 · 可叠加 LightRAG 式图谱检索一路（实体/关系投影入 `rag_kg_vector`）。
- **图谱流水线（graphflow，独立于 ragflow）**：逐批走大模型抽实体/关系入 Neo4j，单篇远慢于解析，拆到独立队列与 `arq_graphflow` worker 避免占满 rag 并发槽位；失败重跑默认断点续抽（只补没抽成的批，不重烧已完成调用）。
- RAGAS 评测 5 大指标 + 三段耗时（召回 / 生成 / 打分），后台异步；开放检索 API（API Key + 限流，单次 ≤100 文件）。

### 5. 深度探索（Harness 智能体）
- **AgentScope ReAct 自主智能体**：对话轮次投递到专属 `harness_queue` 异步消费，每轮占一条模型流 + 一个沙箱会话，与 workflow / ragflow / graphflow 完全隔离。
- **每用户独立云沙箱 + 持久化工作区**（OpenSandbox 自托管）：`workspace_id` 幂等复用、自建镜像自带网关 venv、首建跳过分钟级引导；工作区按用户挂**持久卷**（宿主机 NAS 目录映射到容器 `/workspace`）——**沙箱被服务端按最大存活时间销毁/重建后产出物仍在**；缓存句柄陈旧时向控制面探活、非 running 则逐出重建，保证句柄确实可用。
- **流式时间线对话**：思考 / 工具调用 / 回复按帧到达顺序交错呈现、逐段可折叠；SSE 帧协议 `turn.started / text.delta / thinking.delta / tool.started / tool.completed / turn.artifacts / turn.completed|interrupted|failed`。
- **人在环 AskUser**：执行中可打断向用户要参数/确认后从断点恢复，也支持手动中断，消息行终态为准、幂等收尾；AgentState 存 Redis，API 与 worker 跨进程交接。
- **文件上传优化**：上传经后端专用 `write_file` 直写原始字节（不经 shell、不受单命令行参数上限、无 base64 分块/解码开销）；下载经 `read_file` 整份读原始字节（避开命令通道截断大 stdout）；大文件走对象存储 / OSS。
- **最终输出物自动渲染**：智能体收尾用 `present_output` 显式登记交付物 → `turn.artifacts` 帧随消息落库 → 对话内渲染成可点击**文件卡片**，右侧工作区**多格式在线预览**（图片 / PDF / 音视频 / Markdown / 代码 / CSV / HTML / 文本）；本会话该轮首个产物到达时自动展开预览。
- 挂载资源：技能（SKILL.zip 种入沙箱 `skills/` 分区）、工具、MCP 连接、知识库检索——与选择器同源 ACL 校验，每轮可携带模型与挂载配置。

### 6. 执行引擎与基础设施
- arq + Redis 分片队列，**workflow / ragflow / graphflow / harness 四条流水线独立消费、互不抢占**；`crc32(task_id) % 切片数` 稳定路由，多进程横向扩展；切片数存 Redis 而非 Nacos，监控页改完即时生效。
- **统一存储抽象** `StorageBackend`：本地目录 / 阿里云 OSS（SDK V2 异步）按 Nacos `storage:` 段或环境变量切换，沙箱持久卷可由宿主机 NAS 挂载；**无盘化**——后端只搬字节，文档解析在内存做。
- Nacos 一个地方管所有环境差异，启动即注册 + 拉配置，代码不写死任何地址；loguru + `x-trace-id` 贯穿网关 → 服务 → worker。

---

## 三、快速开始

### 环境要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | 3.11+ | 后端 |
| Node.js / pnpm | 20.12+ / 10+ | 前端构建 |
| MySQL | 8.x | 业务数据 |
| Redis | 5+（建议 7.x） | 登录态 / 限流 / 队列 |
| Nacos | 2.x+ | 注册 + 配置中心，**必须有** |
| 阿里云 OSS / NAS | 可选 | 对象存储 / 沙箱持久卷 |
| OpenSandbox Server | 自托管（可选） | 深度探索沙箱；不开该功能可不部署 |

### 新环境初始化（四步）

1. **建库灌种子** —— 一份文件搞定 28 张业务表 + 角色 / 管理员 / 部门 / 菜单 / 权限点，重复执行安全（建表 `IF NOT EXISTS`、种子 `INSERT IGNORE`）：
   ```bash
   mysql --default-character-set=utf8mb4 -h <host> -u root -p < sql/v2_init.sql
   ```
2. **建 Nacos 配置** —— `data_id` = 服务名（`service_system/login/workflow/rag/gateway/harness` 与 `arq_workflow/arq_ragflow`），内容照抄 `sql/init_nacos.yaml` 改连接信息。要点：`arq_*` 的 `redis.url` 必须与对应服务的生产者同源，否则任务投了没人消费；graphflow 不新增 `data_id`，与 ragflow 同读 `arq_ragflow` 段、只队列名独立；深度探索需 `service_harness` 段（opensandbox / agentscope / skills）。
3. **起后端** —— 六个服务 + 四条 worker 流水线，见「本机启动」或「Docker」。
4. **起前端** —— `pnpm build:antd` 出静态产物交 nginx，把 `/api` 反代到网关。

初始账号 `admin / Admin@123`，**首次登录后立即改密码**。存量老库按版本跑 `sql/` 下的增量脚本跟上最新列与权限点。

### 本机启动

```bash
pip install -r requirements.txt

python -m service.service_gateway.gateway      # :18000
python -m service.service_system.system        # :9001
python -m service.service_login.login          # :9004
python -m service.service_workflow.workflow    # :9003
python -m service.service_rag.rag              # :9002（需 ES + Neo4j）
python -m service.service_harness.harness      # :9005（需 OpenSandbox 沙箱服务）

# arq worker：-t 必填，四条流水线各起一个进程组（队列完全隔离）
python -m arq_tasks.run_workers -t workflow  -n 4 -p 1
python -m arq_tasks.run_workers -t ragflow   -n 2 -p 1
python -m arq_tasks.run_workers -t graphflow -n 2 -p 1
python -m arq_tasks.run_workers -t harness   -n 2 -p 1

cd ui-ai && pnpm install && pnpm dev:antd      # :5666（已配 /api → 127.0.0.1:18000）
```

### Docker

只有一个后端镜像 + 一个前端镜像，**容器起哪个进程由注入的 `SERVICE_NAME` 决定**。一键起全部（前后端拆两个 compose，先起后端、前端错峰再 build，避免一次 `--build` 同时编译前后端打爆内存）：

```bash
cp docker/.env.example docker/.env   # 填 NACOS_ADDR
docker compose -f docker/docker-compose.backend.yml up -d --build   # 后端：6 服务 + 4 worker（+ 可选 OpenSandbox）
docker compose -f docker/docker-compose.ui.yml up -d --build        # 前端：等后端起来后单独 build/up
# 浏览器开 http://<host>:8080（UI_PORT）
```

compose 已覆盖全部服务与四条流水线；逐容器 `docker run` 示例、环境变量（`SERVICE_NAME` / `NACOS_ADDR` / `<服务名>_port` / `ARQ_WORKERS` / `SPLIT_NUMBER` 等）与启动脚本细节见 `docker/`、`scripts/` 与「相关文档」。`k8s/` 目录预留，尚未编写清单。

---

## 四、技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 · FastAPI（全异步）· SQLAlchemy 2.0 + aiomysql · Pydantic v2 |
| 进程与部署 | gunicorn + UvicornWorker · Docker · Nacos（注册 / 配置中心） |
| 数据与中间件 | MySQL 8.x · Redis · 阿里云 OSS · Elasticsearch 8.x · Neo4j 5.x |
| 异步任务 | arq 0.28 分片队列，workflow / ragflow / graphflow / harness 四条流水线独立消费、多进程横向扩展 |
| 智能体 | agentscope 2.x（ReAct + workspace / 技能框架）· OpenSandbox 自托管云沙箱（每用户持久化 workspace）· MCP 官方 Python SDK |
| 检索 | ES 双索引 `rag_knowledge_chunk` + `rag_kg_vector`：BM25 + 1024 维 dense kNN 动态加权 · 重排按请求选 · 可叠加图谱检索一路 |
| 文档解析 | `common_file_parser`：native + minerU 双引擎 + auto 调度，8 种分块策略 |
| 知识评测 | RAGAS 5 大指标，自写 langchain-core 适配器把本仓模型服务接入裁判 |
| 通信 | httpx 全局连接池（HTTP/2）· WebSocket · SSE（sse-starlette） |
| 前端 | Vue 3.5 · TypeScript · Vite · vben v5（pnpm + turbo monorepo）· ant-design-vue · Vue Flow · CodeMirror/Monaco |
| 鉴权 | JWT + Redis 会话 · API Key 双通道 · RBAC 权限点 |

---

## 五、目录结构

```
├── common/            # 公共层：app 引导 / nacos / 中间件 / 权限 / 模型 / 解析 / 存储 / 连接池，不放业务逻辑
├── service/           # 业务微服务，一服务一端口
│   ├── service_gateway/   :18000 路由转发 + 鉴权 + 限流 + AI 模型网关
│   ├── service_system/    :9001  RBAC + 模型广场 + 运营监控
│   ├── service_login/     :9004  登录注册
│   ├── service_workflow/  :9003  编排域（execution / workflow_engine / routers…）
│   ├── service_rag/       :9002  知识库：配置 / 文档 / 检索 / 图谱 / 评测 / 开放 API
│   └── service_harness/   :9005  深度探索：会话 / 轮次 / SSE / 沙箱文件代理 + agent（ReAct / workspace 工厂 / 技能种入）
├── arq_tasks/         # 四条流水线的 worker 配置 + 任务函数 + 多进程入口（run_workers -t …）
├── ui-ai/             # 前端 vben v5 monorepo（apps/web-antd 是实际使用的 app）
├── scripts/           # 启动脚本：容器与裸机共用同一份
├── docker/            # Dockerfile（后端）· Dockerfile.ui（前端）· compose · nginx 模板
├── sql/               # v2_init.sql（新环境唯一入口）· init_nacos.yaml · old/（历史增量脚本）
├── docs/              # 设计文档
└── k8s/               # 预留
```

---

## 六、系统架构

```
前端 ui-ai (Vue3 + vben v5)  ── 只发 /api/**，同源代理（vite / nginx），浏览器不跨域
        │
        ▼
service_gateway :18000 ── 唯一对外入口：路由发现(Nacos) · JWT · 限流 · 审计 · x-trace-id · API Key
        │                       /api/model：OpenAI 兼容模型入口（key → 限流 → 厂商 → SSE）
        ▼
system :9001 · login :9004 · workflow :9003 · rag :9002 · harness :9005
        │                       │ 异步执行（Redis 分片队列，四条独立流水线）
        │   arq workflow · arq ragflow · arq graphflow · arq harness
        ▼
基础设施  Nacos（注册 + 全部业务配置）· MySQL · Redis · 阿里云 OSS · ES · Neo4j · OpenSandbox 云沙箱（每用户持久化 workspace）
```

**几条贯穿全局的约定**：① 一个 `create_app` 引导所有服务，依赖初始化失败会回滚 Nacos 注册；② 上层鉴权、底层裸查——ES/Neo4j/存储只按服务层下发的 id 白名单裸查，自身不做权限判断；③ 环境差异只存在于 Nacos，代码不写死连接串；④ 存储只搬字节，换后端不改代码。

---

## 七、开发进度

**✅ 已开发（可直接使用）**：`service_system`（RBAC / ACL / 模型广场 / 监控）、`service_login`、`service_gateway`（含 AI 模型网关）、`service_workflow`（画布 / 21 节点 / submit / 审批 / 记忆）、`service_rag`（三类库 / 混合检索 / graphflow 图谱 / RAGAS 评测 / 开放 API）、`service_harness`（ReAct + 持久化云沙箱 / 时间线 / AskUser / 上传优化 / 产物自动渲染）、`arq_tasks` 四流水线、`common_storage`、`ui-ai` 前端。

**📋 规划中（仅空包骨架，别当能力看）**：`service_datasets`、`service_train`、`service_inference`、`service_eval_model`、`service_notebook`——只预留了包目录、限流桶名与网关别名，无任何接口。

---

## 八、路线图

1. 知识库：图谱向量投影与检索融合持续调优、音视频库分型深化、graphflow 多切片横向扩容
2. **智能体团队编排**：在深度探索单智能体之上升级为多智能体协作——组合多个角色 / 模型 / 工具 / 挂载资源的智能体成「团队」，任务分派、消息交接与沙箱产物共享
3. 数据集 / 训练 / 推理 / 评估 / Notebook 模块落地
4. K8s 清单与 Helm chart

---

## 九、相关文档

| 文档 | 内容 |
|---|---|
| `docs/workflow-execution-contract.md` | 工作流执行域对外契约与内部设计（加任何对外字段必须先改这份） |
| `docs/workflow-approval-memory.md` | 审批与记忆的状态机、需求评审结论与实现补记 |
| `docs/workflow-design.md` | 编排模块设计 |
| `docs/RAG知识库模块开发SPEC.md` | 知识库（RAG）模块需求与设计口径 |
| `sql/v2_init.sql` | 新环境数据库初始化唯一入口 |
| `sql/init_nacos.yaml` | Nacos 配置样例（storage 段字段含义、arq 流水线同源关系） |

---

<p align="center">如果这个项目对你有帮助，欢迎点个 Star ⭐ · Issues 与 PR 都欢迎</p>
