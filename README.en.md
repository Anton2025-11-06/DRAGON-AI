# 🐉 DRAGON-AI — AI Middle Platform

> This document reflects the **current** development status honestly. Modules marked "Done" are
> usable; items marked "Refactoring" or "Planned" must **not** be treated as available
> capabilities.

---

## Development Progress (as of 2026-09)

| Status | Module |
|---|---|
| ✅ Done | Enterprise RBAC & permissions, System management, Login/Registration, Gateway (routing/auth/rate-limit framework), Workflow orchestration |
| 🔄 Refactoring | AI capabilities & gateway AI model forwarding (model-square / chat model-call chain) |
| 📋 Planned | Harness agents, Datasets, Training, Inference, Evaluation, Knowledge base (`service_rag`) |

All services have ports, micro-service skeletons and Nacos registration configs reserved,
ready to be enabled as the roadmap proceeds.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                 Frontend ui-ai (Vue3 + vben v5, :5666)        │
└───────────────────────────┬──────────────────────────────────┘
                            │ /api/* (Vite proxy or gateway)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│           service_gateway (:18000, framework done)            │
│    JWT auth · module rate limit · routing · audit · trace-id  │
│    (AI unified entry /api/model model forwarding: refactoring)│
└───┬──────────────┬──────────────┬──────────────┬─────────────┘
    ▼              ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│ System   │  │ Login    │  │ Workflow │  │ Data/Train/  │
│ :9001    │  │ :9004    │  │ :9003    │  │ Infer/Eval/  │
│ RBAC/    │  │ JWT+Redis│  │ canvas→  │  │ KB :9002 etc.│
│ Model    │  │ session  │  │ exec→SSE │  │ (skeleton/   │
│ Square   │  │          │  │ debug    │  │  planned)    │
└──────────┘  └──────────┘  └──────────┘  └──────────────┘
                            │
                            ▼ async execution
                 ┌──────────────────────┐
                 │ arq workers (shards) │
                 └──────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ Infra: Nacos · MySQL · Redis · optional MinIO/object storage │
│        · in-network OpenAI-compatible inference endpoints     │
└──────────────────────────────────────────────────────────────┘
```

## Done

### System Management (`service_system` :9001)
- Enterprise-grade **RBAC**: user / role / menu / authorization model with fine-grained
  permission points
- Model square: model onboarding (direct / suffix-joined), apply-approve flow, API key issue &
  revoke, one-click connectivity test, online/offline switch (Redis cache refresh)
- Operation-log auditing (sensitive fields masked), online-user monitor, rate-limit config
  center, gateway route config

### Login / Registration (`service_login` :9004)
- Login / register / logout: JWT + Redis session state (`login:{jti}`), instant invalidation
  on logout
- Password hashing, login anti-brute-force rate limiting

### Gateway (`service_gateway` :18000)
- Service routing via Nacos discovery, JWT auth, module-level rate limiting, operation logs,
  `x-trace-id` correlation
- API-key auth translation with short-lived internal tokens
- **Note**: the AI unified entry (`/api/model`, OpenAI-compatible forwarding & SSE pass-through)
  is **being refactored**; the legacy implementation is still in service

### Workflow Orchestration (`service_workflow` :9003)
- Visual canvas: `{nodes, edges}` editing, draft / publish / version snapshot / rollback /
  copy, strict graph validation
- **20 node types**: START/END/LLM/AGENT/IF_ELSE/LOOP/ITERATION/PARALLEL/CODE/TEMPLATE/REPLY/
  HTTP_REQUEST/TOOL/KNOWLEDGE_RETRIEVAL/PARAMETER_EXTRACTOR/QUESTION_CLASSIFIER/
  LIST_OPERATOR/VARIABLE_AGGREGATOR/VARIABLE_ASSIGNER/DOC_EXTRACTOR
- Sync / async execution (arq workers on sharded queues), SSE live event stream + DB replay
  (Redis Pub/Sub cross-process channel)
- Pause / resume / cancel, pause TTL watchdog reclaim, per-node timeout guard, snapshot
  persistence & cross-process resume, breakpoint debugging / live variable editing
- Variable assignment expressions (`{{}}` refs + JSON dot paths + `[index]` array indexing),
  file upload/download (unified storage abstraction, below)
- Workflow API-key metadata management (create / rate limit / expiry / enable-disable)
- MCP / Tool / Sandbox / Model-Chat routes coexist in the same service

### Unified Storage Abstraction (`common/common_storage`)
- `StorageBackend` abstraction with two implementations: **local directory** and
  **S3-compatible object storage** (MinIO / Ceph / OSS)
- Switched via Nacos `storage:` config or env vars (`STORAGE_BACKEND`, `MINIO_*`),
  transparent to business code
- Local by default (`data/workflow_files`); S3 never fails silently — a bad S3 config aborts
  startup instead of writing to the wrong place

## Refactoring (legacy implementation still running)

- **AI capabilities / gateway AI model forwarding**: the model call chain of the model square
  and chat (unified /api/model entry, SSE pass-through, rate-limit translation) is being
  refactored

## Planned (skeleton or constants only — not available yet)

| Module | Status |
|---|---|
| Harness agents (agent / skill) | Ports & constants reserved (9005 / 9006); service directories not created |
| Datasets / Training / Inference / Evaluation / Notebook | Empty package skeletons + rate-limit buckets & gateway aliases reserved |
| Knowledge base (`service_rag` :9002) | Service skeleton exists; Milvus vector store & parse/vectorize pipeline not wired in; the workflow KNOWLEDGE_RETRIEVAL executor is implemented but depends on this module |

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 + FastAPI (async) + SQLAlchemy 2.0 / aiomysql |
| Registry/Config | Nacos (nacos-sdk-python) |
| Data | MySQL 8.x + Redis 7.x (sessions / rate-limit counters / config cache) |
| Async tasks | arq 0.28 (Redis queues, sharded queues for horizontal worker scaling) |
| Object storage | minio SDK (S3-compatible: MinIO / Ceph / OSS, optional) |
| Vector search | Milvus client layer ready (`common_milvus`); KB service planned |
| HTTP | httpx global connection pool |
| Logging | loguru + custom trace-id correlation |
| Frontend | Vue 3.5 + TypeScript + Vite + vben v5 (pnpm/turbo monorepo, ant-design-vue) |
| Auth | JWT + Redis session + API-key dual channel |

## Directory Layout

```
├── common/                     # shared layer (all services)
│   ├── common_app/             #   FastAPI bootstrap factory (lifespan / middleware assembly)
│   ├── common_storage/         #   unified storage abstraction (StorageBackend: local / S3)
│   ├── common_file/            #   document text extraction (11 formats) + DTO (FileUtils)
│   ├── common_middleware/      #   RequestLog / TokenCheck / RateLimit / OperateLog
│   ├── common_nacos/           #   Nacos discovery & config pull
│   ├── common_redis/           #   async Redis client
│   ├── common_mysql/           #   async SQLAlchemy session factory
│   ├── common_milvus/          #   Milvus client (wired once KB service ships)
│   ├── common_httpx/           #   global HTTP connection pool
│   ├── common_permission/      #   RBAC permission decorators
│   ├── common_entity/          #   unified response schema / entities
│   └── common_utils/           #   jwt / password / excel / rate limiter etc.
├── service/                    # business micro-services (independent ports)
│   ├── service_system/         #   system mgmt + model square :9001 (done)
│   ├── service_login/          #   login/registration :9004 (done)
│   ├── service_gateway/        #   gateway :18000 (framework done; AI forwarding refactoring)
│   ├── service_workflow/       #   workflow orchestration :9003 (done)
│   ├── service_rag/            #   knowledge base :9002 (skeleton, planned)
│   ├── service_datasets|train|inference|eval_model|notebook/  # skeletons, planned
├── arq_tasks/                  # arq workers: settings + workflow tasks + multi-process launcher
├── ui-ai/                      # frontend (vben v5 monorepo, :5666)
├── sql/                        # schema scripts (new_init_.sql) + Nacos config sample (init_nacos.yaml)
├── docs/                       # documentation
└── test/                       # verification scripts (see "Testing Status")
```

## Quick Start

### Requirements

| Dependency | Notes |
|---|---|
| Python 3.11+ / Node.js 20+ / pnpm 9+ | backend / frontend |
| MySQL 8.x / Redis 7.x | business data / sessions & rate limiting |
| Nacos 2.x | registry + config center (per-service config by data_id = service name) |
| MinIO etc. | optional (enabled by `storage.type=s3`) |
| OpenAI-compatible endpoint | model-call target (vLLM / DeepSeek / Qwen ...) |

### Run

```bash
# 1) Configure Nacos per sql/init_nacos.yaml (redis/mysql segments with examples)
pip install -r requirements.txt

# 2) Start services (auto-register to Nacos and pull configs)
python -m service.service_login.login          # :9004
python -m service.service_system.system        # :9001
python -m service.service_gateway.gateway      # :18000
python -m service.service_workflow.workflow    # :9003

# 3) arq workers (async workflow execution; -n processes, -p queue shard)
python -m arq_tasks.run_workers -n 4 -p 1

# 4) Frontend
cd ui-ai && pnpm install && pnpm dev:antd      # :5666
```

Default account `admin / Admin@123` (please change after first login).

## Configuration (Nacos yml; sample in sql/init_nacos.yaml)

| Segment | Description |
|---|---|
| `redis:` | connection (sessions / rate-limit counters / config cache) |
| `mysql:` | data source (per-service connection pools) |
| `storage:` | backend: `type: local|s3` + endpoint/access_key/secret_key/bucket/secure (env fallback `STORAGE_BACKEND` / `MINIO_*`) |

## Testing Status

- `test/` and `test/workflow/` contain several **standalone verification scripts** (not a
  pytest suite): workflow full-chain, concurrency, pause/resume, event Pub/Sub,
  document-extraction formats, CRUD, plus browser e2e debug scripts
- Engine-level unit tests (context rendering, comparators, expressions, ...) are scattered
  across historical debug scripts, not yet organized into a pytest suite

## Roadmap

1. Finish AI capabilities & gateway model forwarding refactor (model square / chat / unified
   /api/model entry)
2. Harness agents (agent :9005) + skill center (skill :9006)
3. Knowledge base (`service_rag`: parse → vectorize → hybrid retrieval), wired with the
   KNOWLEDGE_RETRIEVAL workflow node
4. Datasets / Training / Inference / Evaluation / Notebook modules (skeletons ready)

## Related Docs

- Workflow module details: `service/service_workflow/` (module docs follow the latest code)
- DB scripts: `sql/new_init_.sql`; Nacos config sample: `sql/init_nacos.yaml`

---

<p align="center">DRAGON-AI — AI Middle Platform</p>