# 🐉 DRAGON-AI — AI中台

> 本文档如实反映当前开发进度：标注「已完成」的模块可正常使用；「重构中」「规划中」
> 项请勿当作可用能力。

---

## 开发进度总览（截至 2026-09）

| 状态 | 模块 |
|---|---|
| ✅ 已完成 | 企业级 RBAC 权限体系、系统管理、登录注册、网关（服务路由/鉴权/限流框架）、工作流编排 |
| 🔄 重构中 | AI 能力、网关的 AI 模型转发（模型广场/对话的模型调用链路） |
| 📋 规划中 | harness 智能体、数据集、训练、推理、评估、知识库（service_rag）等模块 |

各服务均有多端口、微服务骨架与 Nacos 注册配置预留，随业务迭代即插即用。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                     前端 ui-ai（Vue3 + vben v5，:5666）         │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api/*（Vite 代理或网关）
                            ▼
┌──────────────────────────────────────────────────────────────┐
│              service_gateway 网关（:18000，已完成框架）          │
│    JWT 鉴权 · 模块限流 · 路由转发 · 操作日志 · x-trace-id 链路     │
│    （AI 模型统一入口 /api/model 的模型转发正在重构中）             │
└───┬──────────────┬──────────────┬──────────────┬─────────────┘
    ▼              ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│ 系统管理   │  │ 登录注册   │  │ 工作流编排 │  │ 数据/训练/推理 │
│ :9001    │  │ :9004    │  │ :9003    │  │ /评估/知识库   │
│ RBAC/模型 │  │ JWT+Redis│  │ 画布→执行 │  │ :9002 及预留   │
│ 广场/审计 │  │ 会话下发  │  │ →SSE调试  │  │ （骨架/规划）  │
└──────────┘  └──────────┘  └──────────┘  └──────────────┘
                            │
                            ▼ 异步执行
                 ┌──────────────────────┐
                 │ arq worker（分片队列）│
                 └──────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ 基础设施：Nacos（注册/配置中心）· MySQL · Redis · 可选 MinIO/    │
│           对象存储（storage 配置切换）· 内网 OpenAI 兼容推理服务  │
└──────────────────────────────────────────────────────────────┘
```

## 已完成模块

### 系统管理（service_system :9001）
- 企业级 **RBAC**：用户 / 角色 / 菜单 / 授权模型与细粒度权限点
- 模型广场：模型接入（直连 / 非直连，后缀拼接）、申请审批、API Key 发放与回收、一键连通性测试、动态上下线（Redis 缓存刷新）
- 操作日志审计（敏感字段脱敏）、在线用户监控、限流配置中心、网关路由配置

### 登录注册（service_login :9004）
- 登录 / 注册 / 退出：JWT 签名 + Redis 登录态（`login:{jti}`），退出即时失效
- 密码加密存储、登录限流防爆破

### 网关（service_gateway :18000）
- 服务路由转发（Nacos 注册发现）、JWT 鉴权、模块级限流、操作日志、`x-trace-id` 链路追踪
- API Key 鉴权翻译、内部短时效令牌传递
- **注意**：AI 模型统一入口（`/api/model` OpenAI 兼容转发与 SSE 透传）相关能力**正在重构中**，当前以旧实现运行

### 工作流编排（service_workflow :9003）
- 可视化画布：`{nodes, edges}` 编辑、草稿 / 发布 / 版本快照 / 回滚 / 复制、严格图校验
- **20 种节点**：START/END/LLM/AGENT/IF_ELSE/LOOP/ITERATION/PARALLEL/CODE/TEMPLATE/REPLY/HTTP_REQUEST/TOOL/KNOWLEDGE_RETRIEVAL/PARAMETER_EXTRACTOR/QUESTION_CLASSIFIER/LIST_OPERATOR/VARIABLE_AGGREGATOR/VARIABLE_ASSIGNER/DOC_EXTRACTOR
- 同步 / 异步执行（arq worker 消费分片队列）、SSE 实时事件流 + DB 回放（Redis Pub/Sub 跨进程通道）
- 暂停 / 恢复 / 取消、暂停 TTL 看门狗回收、单节点超时保护、快照落库与跨进程恢复、断点调试 / 变量实时修改
- 变量赋值表达式（`{{}}` 引用 + JSON 点路径 + `[index]` 数组下标）、文件上传下载（统一存储抽象，下方说明）
- API Key 元数据管理（创建 / 限流 / 过期 / 启停）
- 同服务共存 MCP / 工具 / 沙箱 / 模型对话路由

### 统一存储抽象（common/common_storage）
- `StorageBackend` 抽象：**本地目录** 与 **S3 兼容对象存储**（MinIO / Ceph / OSS）两个实现
- 通过 Nacos `storage:` 配置段或环境变量（`STORAGE_BACKEND`、`MINIO_*`）切换，业务无感
- 默认本地存储（`data/workflow_files`），配置 s3 后自动使用对象存储，未配置永不失败

## 重构中（当前以旧实现运行）

- **AI 能力 / 网关 AI 模型转发**：模型广场与对话的模型调用链路（/api/model 统一入口、SSE 透传、限流翻译）正在重构

## 规划中（骨架或常量已预留，暂不可用）

| 模块 | 现状 |
|---|---|
| harness 智能体（agent / skill） | 端口与常量预留（9005 / 9006），服务目录未建立 |
| 数据集 / 训练 / 推理 / 评估 / Notebook | `service_datasets/train/inference/eval_model/notebook` 空包骨架 + 限流桶与网关别名已预留 |
| 知识库（service_rag :9002） | 服务骨架存在；检索依赖的 Milvus 向量库与文档解析/向量化流程未接入，工作流 KNOWLEDGE_RETRIEVAL 节点执行器已实现但依赖本模块就绪 |

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 + FastAPI（async）+ SQLAlchemy 2.0 / aiomysql |
| 注册/配置 | Nacos（nacos-sdk-python，注册发现 + 配置中心） |
| 数据 | MySQL 8.x + Redis 7.x（登录态 / 限流计数 / 配置缓存） |
| 异步任务 | arq 0.28（Redis 队列，分片队列支持多 worker 横向扩展） |
| 对象存储 | minio SDK（S3 兼容：MinIO / Ceph / OSS，可选接入） |
| 向量检索 | Milvus 客户端层已备（common_milvus），知识库服务规划中 |
| HTTP | httpx 全局连接池 |
| 日志 | loguru + 自研 trace-id 链路 |
| 前端 | Vue 3.5 + TypeScript + Vite + vben v5（pnpm/turbo monorepo，ant-design-vue） |
| 鉴权 | JWT + Redis 登录态 + API Key 双通道 |

## 目录结构

```
├── common/                     # 公共层（所有服务共享）
│   ├── common_app/             #   FastAPI 统一引导工厂（lifespan / 中间件装配）
│   ├── common_storage/         #   统一存储抽象（StorageBackend：本地 / S3）
│   ├── common_file/            #   文档文本解析（11 种格式）+ DTO（FileUtils）
│   ├── common_middleware/      #   RequestLog / TokenCheck / RateLimit / OperateLog
│   ├── common_nacos/           #   Nacos 注册发现与配置拉取
│   ├── common_redis/           #   异步 Redis 客户端
│   ├── common_mysql/           #   异步 SQLAlchemy 会话工厂
│   ├── common_milvus/          #   Milvus 向量客户端（知识库服务启用后接入）
│   ├── common_httpx/           #   全局 HTTP 连接池
│   ├── common_permission/      #   RBAC 权限装饰器
│   ├── common_entity/          #   统一响应体 / 实体
│   └── common_utils/           #   jwt / 密码 / excel / 速率限制等工具
├── service/                    # 业务微服务（各自独立端口）
│   ├── service_system/         #   系统管理 + 模型广场 :9001（已完成）
│   ├── service_login/          #   登录注册 :9004（已完成）
│   ├── service_gateway/        #   网关 :18000（框架已完成，AI 转发重构中）
│   ├── service_workflow/       #   工作流编排 :9003（已完成）
│   ├── service_rag/            #   知识库 :9002（骨架，规划中）
│   ├── service_datasets|train|inference|eval_model|notebook/  # 骨架，规划中
├── arq_tasks/                  # arq worker：配置 + 工作流执行/恢复任务 + 多进程启动脚本
├── ui-ai/                      # 前端（vben v5 monorepo，:5666）
├── sql/                        # 建表脚本（new_init_.sql）+ Nacos 配置样例（init_nacos.yaml）
├── docs/                       # 文档
└── test/                       # 验证脚本（见「测试现状」）
```

## 快速开始

### 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.11+ / Node.js 20+ / pnpm 9+ | 后端 / 前端 |
| MySQL 8.x / Redis 7.x | 业务数据 / 会话与限流 |
| Nacos 2.x | 注册中心 + 配置中心（各服务配置按 data_id = 服务名拉取） |
| MinIO 等对象存储 | 可选（配置 `storage.type=s3` 后启用） |
| OpenAI 兼容推理端点 | 模型调用目标（vLLM / DeepSeek / Qwen 等） |

### 启动

```bash
# 1) 依赖：按 sql/init_nacos.yaml 配置 Nacos（redis/mysql 段已含示例）
pip install -r requirements.txt

# 2) 逐服务启动（自动注册 Nacos 并拉取配置）
python -m service.service_login.login          # :9004
python -m service.service_system.system        # :9001
python -m service.service_gateway.gateway      # :18000
python -m service.service_workflow.workflow    # :9003

# 3) arq worker（工作流异步执行；-n 进程数，-p 队列切片号）
python -m arq_tasks.run_workers -n 4 -p 1

# 4) 前端
cd ui-ai && pnpm install && pnpm dev:antd      # :5666
```

初始账号 `admin / Admin@123`（首次登录后请修改）。

## 配置（Nacos yml，样例见 sql/init_nacos.yaml）

| 段 | 说明 |
|---|---|
| `redis:` | 连接地址（登录态 / 限流 / 配置缓存） |
| `mysql:` | 数据源（各服务独立连接池） |
| `storage:` | 存储后端：`type: local|s3` + endpoint/access_key/secret_key/bucket/secure（环境变量 `STORAGE_BACKEND` / `MINIO_*` 兜底） |

## 测试现状

- `test/` 与 `test/workflow/` 下为多套**独立运行的验证脚本**（非 pytest 套件）：工作流全链路、
  并发、暂停恢复、事件 Pub/Sub、文档提取格式、CRUD 等，另有浏览器端到端调试脚本
- 引擎级单测（上下文渲染、比较器、表达式等）散布于历史调试脚本，未统一组织为 pytest 套件

## 路线图

1. 完成 AI 能力与网关模型转发重构（模型广场 / 对话 / /api/model 统一入口）
2. harness 智能体（agent :9005）+ 技能中心（skill :9006）
3. 知识库模块（service_rag：文档解析 → 向量化 → 混合检索，联动工作流 KNOWLEDGE_RETRIEVAL 节点）
4. 数据集 / 训练 / 推理 / 评估 / Notebook 模块落地（骨架已就绪）

## 相关文档

- 工作流模块详述：`service/service_workflow/`（模块内文档以最新代码为准）
- 数据库脚本：`sql/new_init_.sql`；Nacos 配置样例：`sql/init_nacos.yaml`

---

<p align="center">DRAGON-AI — AI中台</p>