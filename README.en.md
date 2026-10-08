# 🐉 DRAGON-AI — AI Middle Platform

> **If you find this project useful, please give it a Star. Thank you.** ⭐
> [![Star this repo](https://img.shields.io/github/stars/Anton2025-11-06/DRAGON-AI?style=social)](https://github.com/Anton2025-11-06/DRAGON-AI)

> WeChat：![img.png](docs/img.png)

> Online：http://47.111.117.95:12345   viewer/viewer

> This README is kept in sync with the code: **everything marked "Done" actually runs; treat
> "In Progress" and "Planned" as unavailable**.
> [中文](README.md) / English.

---

## 1. Capabilities

### 1.1 Enterprise RBAC & operations foundation

| Capability | Notes |
|---|---|
| User / role / menu / button permission points | A permission point *is* a menu row (`menu_type=3`); one dataset drives both UI visibility and backend `@has_permission`. ADMIN bypasses checks |
| Departments & data scope | Five data scopes (all / dept & below / own dept / self only / custom department set); a dept manager manages members of their own dept |
| Row-level ACL (one engine, whole platform) | **Seven resource types**: knowledge base / document / workflow / workflow template / tool / skill / MCP connection; granted per **action** (view / use / edit / delete / share / upload / reparse / graph / chunk / preview / content / doc_delete / eval / chat / copy / apikey / template / history / export / test / tools / rename / replace — 20+ codes) to **user / role / department (with sub-tree) / user group / everyone**. The decision is `ADMIN ∪ owner ∪ data scope ∪ explicit grant`; `service_system/acl_router` is the single read/write surface, `common_permission/resource_guard` is the single evaluator, and list push-down / per-row button / 403 all consume the same rule set — no service is allowed to hand-write a second copy |
| Authentication | JWT signature + Redis session (`login:{jti}`); logout revokes immediately; hashed password storage; brute-force rate limit on login |
| Audit log | End-to-end `x-trace-id`, sensitive fields masked automatically, one request traceable across every row it touched |
| Online users / ARQ monitor | Force-logout sessions; worker liveness, queue backlog, split count adjustable at runtime; the dropdown switches between the **workflow / ragflow / graphflow** pipelines |
| Rate-limit config centre | Per-module-bucket QPS & daily quota, atomic Redis counters, hot reload in the gateway |

![Role management](docs/role-manager.png)
![Audit log with trace id](docs/audit-log-traceid.png)

### 1.2 AI model gateway & model square

- **OpenAI-compatible entry** `POST /api/model`: api-key auth → model consistency check → QPS + daily quota → vendor call → streaming SSE / plain JSON
- **12 model capability types × 3 providers** (OpenAI-compatible / DashScope / Zhipu): text generation, embeddings, rerank, image & video understanding, OCR, text-to-image, text-to-video, image-to-video, text-to-audio, audio-to-text, ...
- **Capability flags per model**: `supports_stream` / `supports_thinking` / `supports_function_call` are registered in model management; anything not registered is never sent to the vendor
- **Full model lifecycle**: onboard (direct / indirect, suffix rewrite) → apply → approve → issue/revoke API key → one-click connectivity test → online/offline toggle (Redis cache refreshed)
- **Model playground**: multi-model chat in the browser with streaming, reasoning chain and tool-call results

![Model centre](docs/model-center.png)
![Model chat](docs/model-chat.png)

### 1.3 Workflow / agent orchestration

- **Visual canvas**: `{nodes, edges}` editing, draft / publish / version snapshot / rollback / duplicate, strict graph validation before publish (reachability, ports, required params, cycles)
- **21 node types**: boundary (START/END), AI (LLM / question classifier / parameter extractor), control (IF_ELSE / LOOP / PARALLEL / variable assigner / aggregator / reply / **approval**), data (template / code / list operator / document extractor / knowledge retrieval), external (HTTP / tool / MCP tool / **sub-workflow**)
- **The execution contract has exactly three concepts**: `executionId` (the whole identity of one run), `pendingApprovals` (approvals currently owed), `submit` (the only action on the only entry point). First run, continue, re-run and answering an approval are all the same `POST /workflow-executions/submit`
- **Human approval (nested)**: an approval node suspends at the node boundary; snapshots persist and recovery works across processes; a sub-workflow's approval bubbles up to the parent run, answered with a one-shot `approvalToken`
- **Real-time events**: WebSocket (drives the preview page synchronously) + SSE (third-party long connection) + DB replay; every frame carries `pauseGeneration` so generations never interleave
- **Reliability**: pause TTL watchdog, per-node timeout, cancellation, global job idempotency (duplicate enqueue is rejected)
- **Multi-turn memory**: LLM history scoped to one `executionId`, selectable as this node / chosen nodes / whole workflow, with drop or auto-compress strategies when over the limit
- **Function calling**: an LLM node can mount tools / MCP connections / sub-workflows; their parameter schemas are read at execution time and the model decides whether and how to call them
- **Resource centres**: skills (SKILL.zip upload / preview / inline edit / enable-toggle), tools (dynamic Python functions in a sandbox), MCP connections (`tools/list` discovery), workflow templates (seeded on first start)
- **Serving externally**: API key metadata (quota / expiry / enable) plus four open endpoints (submit / status / SSE / cancel)

![Workflow canvas](docs/workflow.png)

### 1.4 Knowledge base & RAG (with retrieval evaluation)

- **Three knowledge-base types**: document / image-text / audio-video, each with per-library default retrieval parameters and generation model; the service keeps the table, the ES indexes and the Neo4j graph in sync
- **Parsing & chunking**: `common_file_parser` with two engines (native: pypdf/docx/xlsx… · minerU private HTTP) + auto dispatch and 8 chunking strategies; chunks are vectorized into ES `rag_knowledge_chunk`
- **Hybrid retrieval**: BM25 + 1024-dim dense_vector kNN fused with per-request weights · rerank model chosen per request (no model, no rerank) · an optional LightRAG-style graph retrieval path can be merged (entity/relation projection into `rag_kg_vector`)
- **Knowledge-graph pipeline (graphflow, split from ragflow)**: documents are mined for entities/relations into Neo4j, optionally vector-projected as a retrieval enhancement. Graph extraction walks **every** chunk of a document through the LLM batch by batch, so a single document can run tens of minutes to hours — far longer than parsing/chunking/vectorising. Sharing one queue with ragflow meant a few large graphs would fill the rag worker's `max_jobs` slots and starve ingestion behind them, so this stage now runs on its own `graphflow_queue:split_{N}` with an `arq_graphflow` worker; the two pipelines scale independently. Redis and back-end config are still read from the `arq_ragflow` Nacos section (same instance), only the queue and worker name are separate. Failure reruns default to checkpoint resume (`resume`) so a retried document only re-extracts the batches that failed, without re-burning finished LLM calls
- **Row-level ACL**: knowledge base and document are just two of the seven platform-wide ACL resources; grants are per action (view / use / edit / upload / reparse / graph / chunk / preview / content / doc_delete / eval / share …) to user / role / department / user group / everyone. When a document row itself denies, we fall back to its parent knowledge base's "files inside" action group (grant once → covers every current and future document). Checked once at the route entry, the lower layers query raw by `kb_id` allow-list (see §1.1 for the full engine)
- **RAGAS evaluation**: pick a knowledge base, configure Q-A pairs (enter several by hand, or fill and upload the downloadable Excel template); answers are generated concurrently and scored on 5 metrics (faithfulness / answer_relevancy / context_precision / context_recall / answer_correctness) + three latency phases (recall / generate / score); results persist as history and the run executes asynchronously on ARQ
- **Open API**: knowledge-base retrieval endpoints for external callers (API-key auth + rate limit), up to 100 files per call



### 1.5 Execution engine & infrastructure

- **Async execution**: arq + Redis sharded queues, **three pipelines consuming independently** — `workflow_queue:split_{N}` / `ragflow_queue:split_{N}` / `graphflow_queue:split_{N}`; routed by `crc32(task_id) % shardCount` so one execution_id / doc_id always lands on the same shard, multi-process scalable. The shard count lives in Redis (not Nacos) so the monitor page can resize on the fly
- **Storage abstraction**: one `StorageBackend` with a local-directory and an Aliyun OSS implementation (official SDK V2 async client), switched by the Nacos `storage:` section or env vars; **diskless** — the backend only moves bytes, document parsing happens in memory
- **Registration & configuration**: Nacos owns every environment difference (database / Redis / storage / vector store); a service registers and pulls config on startup, no connection string is hard-coded
- **Observability**: loguru plus a home-grown `x-trace-id` that survives gateway → service → worker

---

## 2. Development progress

### ✅ Done

| Module | Service | Content |
|---|---|---|
| System management | `service_system` :9001 | Full RBAC, **the single read/write surface for row-level ACL (acl_router, shared by the seven resource types)**, model square & approvals, API keys, audit log, online users, rate-limit config, three-pipeline ARQ monitor (workflow / ragflow / graphflow), gateway route config |
| Login / register | `service_login` :9004 | Login / register / logout / token refresh (sliding expiry), JWT + Redis session |
| Business gateway | `service_gateway` :18000 | Route forwarding (Nacos discovery), JWT auth, module rate limiting, audit log, trace-id, API-key translation |
| AI model gateway | `service_gateway` | `/api/model` OpenAI-compatible entry, 12-type dispatch, streaming passthrough, two-layer quotas |
| Workflow orchestration | `service_workflow` :9003 | Canvas, 21 node types, submit contract, nested approval, SSE/WS, snapshot recovery, timeouts & watchdog, memory, tool/MCP/skill/sandbox, templates, API-key serving; workflow / template / tool / skill / MCP are all wired into the ACL engine |
| Knowledge base / RAG | `service_rag` :9002 | Three kb types, upload & ingestion progress, hybrid retrieval (BM25+dense+rerank+graph enhancement), Neo4j graph pipeline running on the dedicated **graphflow** worker, ACL on knowledge base / document via the platform engine, RAGAS evaluation, open retrieval API |
| Knowledge retrieval node | `service_workflow` | `KNOWLEDGE_RETRIEVAL` is wired up (calls `service_rag` retrieval over HTTP); selectable and configurable in the node palette |
| Async execution | `arq_tasks` | Sharded queues, multi-process workers (**three pipelines: workflow / ragflow / graphflow**, `-t workflow\|ragflow\|graphflow`), health reporting, idempotent enqueue |
| Storage abstraction | `common_storage` | Local + Aliyun OSS, presigned URLs, diskless byte transfer |
| Front end | `ui-ai` :5666 | vben v5 SPA covering every module above |

### 🔄 In progress (code exists, not a usable capability)

| Module | Status |
|---|---|
| Harness agent | Skill / tool / MCP centres are usable inside the workflow service; the standalone autonomous planning loop is not, ports :9005 / :9006 and constants are reserved |

### 📋 Planned (empty skeletons only)

`service_datasets`, `service_train`, `service_inference`, `service_eval_model`, `service_notebook`.
These reserve a package directory, a rate-limit bucket name and a gateway alias — nothing else. Do not read them as features.

---

## 3. Deployment

### 3.1 Requirements

| Dependency | Version              | Purpose |
|---|----------------------|---|
| Python | 3.11+                | Back end |
| Node.js / pnpm | 20.12+ / 10+         | Front-end build |
| MySQL | 8.x (5.7 works)      | Business data |
| Redis | 5+ (7.x recommended) | Sessions / rate limits / queues / config cache |
| Nacos | 2.x+                 | Registry + config centre, **mandatory** — every environment difference lives there |
| Aliyun OSS | optional             | Enabled with `storage.type=oss` |

### 3.2 Initialising a fresh environment (four steps)

**1) Create the schema and seed data** — one file covers it: 28 business tables plus role / admin / department / menu / button permission seeds.

```bash
# To use another database name, edit the two lines in PART 0 (CREATE DATABASE + USE)
# The file must run over a single connection (menu seeds use @session variables for parent ids);
# the mysql CLI satisfies this naturally
# Safe to re-run: CREATE TABLE IF NOT EXISTS, and every seed is INSERT IGNORE, so a rerun never
# resets a changed password or menu ordering
mysql --default-character-set=utf8mb4 -h <host> -u root -p < sql/v2_init.sql
```

**2) Create the Nacos configs**, one per `data_id` = service name: `service_system` / `service_login` /
`service_workflow` / `service_gateway` / `service_rag` / `arq_workflow` / `arq_ragflow`. Copy
`sql/init_nacos.yaml` and change the connection details.

> ★ The `arq_workflow` section must contain `redis.url`, and it must point at the same Redis the
> `service_workflow` producer uses — otherwise jobs are enqueued where nobody consumes them.
> ★ Same for `arq_ragflow`: its `redis.url` is the rag queue Redis, while `mysql/storage/es/neo4j`
>   must point at the same instances `service_rag` uses — uploads are written by the API process and
>   read back by the worker, so a mismatch means "upload succeeded, worker cannot find the file".
>   The `worker:` sub-section of that block holds the rag worker's concurrency/timeout knobs
>   (omit it and the code defaults apply).
> ★ **graphflow does not need its own data_id**: the graph worker reads the same `arq_ragflow` Nacos
>   section as ragflow (same Redis, same back-ends); only the queue name (`graphflow_queue`) and the
>   worker name (`graphflow_worker`) are separate. The split is about isolating concurrency, not storage.
> The `storage:` section is only needed when workflow files go to object storage; without it the
> local directory backend is used.

**3) Start the back end** — five services plus the three worker pipelines; see 3.3 (bare metal) or 3.4 (containers).

**4) Start the front end** — `pnpm build:antd` produces static files; serve them with nginx and proxy
`/api` to the gateway. With the front-end image from 3.4 you only pass `GATEWAY_URL`.

Initial account `admin / Admin@123` — **change the password right after the first login**.

For an existing database that has to catch up on columns and permission points, run the incremental scripts
at the `sql/` root by version (`sql/v2_2_kb_upgrade.sql` for the knowledge base, `sql/v2_3_rag_eval_upgrade.sql`
for RAGAS evaluation); older incrementals live under `sql/old/` and are run one by one, each header states which
table it alters. `sql/old/new_init_.sql` and `sql/old/workflow_schema.sql` have been superseded by `v2_init.sql`
and are kept only as provenance.

### 3.3 Running locally

```bash
pip install -r requirements.txt

python -m service.service_login.login          # :9004
python -m service.service_system.system        # :9001
python -m service.service_workflow.workflow    # :9003
python -m service.service_rag.rag              # :9002 (knowledge base; needs ES + Neo4j)
python -m service.service_gateway.gateway      # :18000

# arq workers: -t is required, one process group per pipeline (fully separated queues)
python -m arq_tasks.run_workers -t workflow   -n 4 -p 1    # 4 processes on workflow_queue:split_1
python -m arq_tasks.run_workers -t ragflow    -n 2 -p 1    # 2 processes on ragflow_queue:split_1 (parse/chunk/vectorise)
python -m arq_tasks.run_workers -t graphflow  -n 2 -p 1    # 2 processes on graphflow_queue:split_1 (KG build, slower than parse)

cd ui-ai && pnpm install && pnpm dev:antd      # :5666 (vite already proxies /api → 127.0.0.1:18000)
```

### 3.4 Docker

Two images — one back end, one front end. **Which process a container runs is decided by injected
environment variables**:

```bash
# Build (both use the repository root as context)
docker build -f docker/Dockerfile     -t dragon-ai-backend:latest .
docker build -f docker/Dockerfile.ui  -t dragon-ai-ui:latest      .

# One service: SERVICE_NAME selects the process
docker run -d --name wf --init -p 9003:9003 \
  -e SERVICE_NAME=service_workflow \
  -e NACOS_ADDR=10.0.0.9:8848 -e NACOS_NAME=nacos -e NACOS_PASSWD=xxx \
  -e service_workflow_port=9003 -e WORKERS=4 \
  dragon-ai-backend:latest

# arq worker: same image, different SERVICE_NAME
docker run -d --name arq --init \
  -e SERVICE_NAME=arq_workflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=4 -e SPLIT_NUMBER=1 \
  dragon-ai-backend:latest

# knowledge-ingestion worker (parse / chunk / vectorise, pure CPU)
docker run -d --name arq-rag --init \
  -e SERVICE_NAME=arq_ragflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=2 -e SPLIT_NUMBER=1 \
  dragon-ai-backend:latest

# knowledge-graph builder (graphflow queue, walks every chunk through the LLM, slower than ingestion)
docker run -d --name arq-graph --init \
  -e SERVICE_NAME=arq_graphflow -e NACOS_ADDR=10.0.0.9:8848 \
  -e ARQ_WORKERS=2 -e SPLIT_NUMBER=1 \
  dragon-ai-backend:latest

# Front end: GATEWAY_URL tells nginx where to send /api
docker run -d --name ui --init -p 8080:80 \
  -e GATEWAY_URL=http://10.0.0.9:18000 \
  dragon-ai-ui:latest
```

All of them at once:

```bash
cp docker/.env.example docker/.env   # fill in NACOS_ADDR
docker compose -f docker/docker-compose.yml up -d --build
# open http://<host>:8080
```

| Variable | Purpose | Default |
|---|---|---|
| `SERVICE_NAME` | Which process the container runs: `service_login` / `service_system` / `service_gateway` / `service_workflow` / `service_rag` / `arq_workflow` / `arq_ragflow` / `arq_graphflow` | none (required) |
| `NACOS_ADDR` `NACOS_NAME` `NACOS_PASSWD` `NACOS_NS_ID` | Registry and config centre | none |
| `<service_name>_port` | Override the listening port (lower-case service name — that is the format the code reads) | per-service default |
| `WORKERS` | gunicorn worker count per micro-service | 4 |
| `ARQ_WORKERS` / `SPLIT_NUMBER` | worker process count / queue shard this instance consumes | 4 / 1 |
| `RAG_ARQ_WORKERS` / `RAG_SPLIT_NUMBER` | compose-only overrides for the `arq_ragflow` container, so ingestion scales independently | 2 / 1 |
| `GRAPH_ARQ_WORKERS` / `GRAPH_SPLIT_NUMBER` | compose-only overrides for the `arq_graphflow` container, so the graph pipeline scales independently too | 2 / 1 |
| `GATEWAY_URL` / `LISTEN_PORT` | Front-end container: gateway address / listening port | `http://127.0.0.1:18000` / 80 |

**All start-up scripts live in `scripts/`** — the container and a bare-metal run use the same file:

| Script | Notes |
|---|---|
| `scripts/docker-entrypoint.sh` | Single container entry, dispatches on `SERVICE_NAME` |
| `scripts/run_service.sh` | gunicorn + `uvicorn.workers.UvicornWorker`, 4 workers per service by default. Production flags: `--backlog 4096`, `--timeout 120`, `--graceful-timeout 30`, `--max-requests 50000 (+jitter)`, `--worker-tmp-dir /dev/shm`, `--proxy-headers --forwarded-allow-ips '*'`, logs to stdout |
| `scripts/run_arq_workflow.sh` | Starts N workflow arq workers (`-t workflow`) and translates `docker stop`'s TERM into the INT path that the graceful shutdown branch handles |
| `scripts/run_arq_ragflow.sh` | Same with `-t ragflow`: knowledge-ingestion workers, 2 processes by default (the real concurrency knob is `arq_ragflow.worker.max_jobs` in Nacos) |
| `scripts/run_arq_graphflow.sh` | Same with `-t graphflow`: knowledge-graph builder on its own queue. Redis and back-ends are still read from the `arq_ragflow` Nacos section, only the queue / worker name are separate; per-job timeout is 4 h (longer than parse), no auto-retry, reruns default to checkpoint resume |
| `scripts/run_ui.sh` | Renders `GATEWAY_URL` into the nginx site config and runs nginx in the foreground |

### 3.5 Kubernetes

The `k8s/` directory is reserved; no manifests yet.

---

## 4. Tech stack

| Layer | Choice |
|---|---|
| Back end | Python 3.11 · FastAPI (fully async) · SQLAlchemy 2.0 + aiomysql · Pydantic v2 |
| Process & deploy | gunicorn + UvicornWorker · Docker · Nacos (registry / config centre) |
| Data & middleware | MySQL 8.x · Redis (sessions / rate limits / cache / ingestion progress + the three arq queues) · Aliyun OSS · Elasticsearch 8.x · Neo4j 5.x |
| Async tasks | arq 0.28 with Redis sharded queues and multi-process scaling; the **workflow / ragflow / graphflow pipelines consume independently** (`-t workflow\|ragflow\|graphflow`) — graph extraction is slower than parsing, so the split stops it from starving the ingestion workers; graph and rag share one Nacos section |
| Communication | httpx global connection pool (HTTP/2) · WebSocket · SSE (sse-starlette) |
| Model access | One `openai_impl` / `dashscope_impl` / `zhipu_impl` per capability, dispatched by `common_model.entry` on (capability, provider) |
| Agents | Official MCP Python SDK · sandboxed process isolation (psutil) · code nodes share the same sandbox; `deepagents` is declared as a dependency but the planning loop is not implemented |
| Retrieval | two ES indexes, `rag_knowledge_chunk` (chunks) + `rag_kg_vector` (entity/relation projection): BM25 + 1024-dim dense_vector kNN fused with per-request weights · rerank model chosen per request (no model, no rerank) · an optional LightRAG-style graph retrieval path can be merged in · text / image / audio-video share one vector space |
| Document parsing | `common_file_parser`: native (pypdf/docx/xlsx…) · minerU (private HTTP API), auto engine selection, 8 chunking strategies (20000 chars per chunk cap) |
| Evaluation | RAGAS (SingleTurnSample + EvaluationDataset), 5 metrics; a home-grown langchain-core adapter (`RagasChatModel`/`RagasEmbeddings`) wires this repo's model services into the ragas judge; runs concurrently and asynchronously on the ragflow queue |
| Observability | loguru + home-grown `x-trace-id` (OpenTelemetry dependencies declared, not yet wired into code) |
| Front end | Vue 3.5 · TypeScript · Vite · vben v5 (pnpm + turbo monorepo) · ant-design-vue · Vue Flow canvas · CodeMirror/Monaco |
| AuthZ/AuthN | JWT + Redis session · dual-channel API key · RBAC permission points |

---

## 5. Project layout

```
├── common/                       # Shared layer, no business logic
│   ├── common_app/               #   create_app bootstrap factory (lifespan + middleware + errors)
│   ├── common_nacos/             #   Nacos registration, discovery and config pull
│   ├── common_middleware/        #   RequestLog / TokenCheck / RateLimit / OperateLog / exception handlers
│   ├── common_permission/        #   @has_permission checks + resource_guard row-level ACL (seven resource types)
│   ├── common_model/             #   12 capabilities × 3 providers + entry dispatch
│   ├── common_file_parser/       #   parsing: 2 engines + 8 chunkers + preprocessing/media description
│   ├── common_storage/           #   StorageBackend: local / Aliyun OSS, bytes only
│   ├── common_mysql|redis|es|neo4j|httpx|arq/   # pools and queue wrappers (es/neo4j are global async singletons)
│   ├── common_entity/            #   ApiResponse / RBAC entities
│   ├── common_log|utils|exception|constants|threadpool/
├── service/                      # Business micro-services, one port each
│   ├── service_gateway/          #   :18000 routing + auth + rate limit + AI model gateway
│   ├── service_system/           #   :9001  RBAC + model square + ops monitors
│   ├── service_login/            #   :9004  login / register
│   ├── service_workflow/         #   :9003  orchestration domain
│   │   ├── execution/            #     round request / pause state / approval projection / exec tree / submit resolver
│   │   ├── workflow_engine/      #     graph engine (nodes / context / validation / snapshots)
│   │   ├── routers|services|models|schemas|utils/
│   ├── service_rag/              #   :9002  knowledge base: configs / documents / progress / retrieval / graph / evaluation / open API
│   │   ├── services/             #     kb·doc·task·parse·graph·retrieval·rag_eval·ragas_adapter·kg_search/kg_vector·rag_settings
│   │   ├── routers/              #     knowledge-bases / documents / retrieve / graph / eval / open
│   └── service_datasets|train|inference|eval_model|notebook/   # planned, empty packages
├── arq_tasks/                    # worker settings for all three pipelines + task functions + multi-process launcher
├── ui-ai/                        # front end, vben v5 monorepo (apps/web-antd is the one we ship)
├── scripts/                      # start-up scripts, shared by containers and bare metal
├── docker/                       # Dockerfile (back end) · Dockerfile.ui (front end) · compose · nginx template
├── sql/                          # v2_init.sql (the only fresh-install entry) · init_nacos.yaml · old/ (historical incrementals)
├── docs/                         # design docs and screenshots
└── k8s/                          # reserved
```

---

## 6. Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│  Front end ui-ai  Vue3 + vben v5 (dev :5666 / nginx in prod)        │
│  Only calls /api/**; same-origin via vite proxy or nginx, no CORS   │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌───────────────────────────────────────────────────────────────────┐
│  service_gateway :18000  —— the single external entry point          │
│  /api/{service_name}/{path} → Nacos discovery → forward              │
│  JWT · module rate limit · audit log · x-trace-id · API-key translate │
│  /api/model: OpenAI-compatible entry (key → quota → vendor → SSE)     │
└──────┬──────────────┬───────────────┬───────────────┬─────────────┘
       ▼              ▼               ▼               ▼
  system :9001    login :9004    workflow :9003    rag :9002
  RBAC / ACL      JWT+session    canvas/submit     knowledge base (3 types)
  audit/monitors                 approval/tools    configs/docs/retrieval/graph/eval/open API
       │                             │                   │
       │                             ▼ async execution (Redis sharded queue, three pipelines)
       │        ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
       │        │ arq workflow   │  │ arq ragflow    │  │ arq graphflow  │
       │        │ worker recovery│  │ worker parse   │  │ worker KG build│
       │        └────────────────┘  └───────┬────────┘  └───────┬────────┘
       │                                    ▼                    ▼
       │                  ES rag_knowledge_chunk · Neo4j · OSS
┌───────────────────────────────────────────────────────────────────┐
│ Infra: Nacos (registry + all business config) · MySQL · Redis · OSS · ES · Neo4j │
└───────────────────────────────────────────────────────────────────┘
```

### Conventions that run through the whole codebase

1. **One `create_app` bootstraps every service** (`common_app.bootstrap`): Nacos registration, Redis/MySQL/storage/arq/ES/Neo4j pools, the middleware stack and exception handling all live in the lifespan; a service only declares its routers and switches (services that need to load business parameters add the `on_ready` hook). If a dependency fails to initialise, the Nacos registration is rolled back so no unusable instance stays in the registry.
2. **Authorise at the top, query raw at the bottom**: functional permission points are checked once at the route entry, while row-level visibility and operability are decided by the `resource_guard` ACL — **one engine shared by the seven platform-wide resources** (knowledge base / document / workflow / workflow template / tool / skill / MCP connection), and the decision is `ADMIN ∪ owner ∪ data scope ∪ explicit grant`. ES/Neo4j/storage only ever query with the `kb_id` / resource id allow-list handed down by the service layer and never make permission decisions themselves.
3. **Environment differences exist only in Nacos**: no connection string in code; `data_id` is the service name and the port is overridden by the `<service_name>_port` env var.
4. **A permission point is a menu row**: `menu_type=3` in `tb_menu` serves both front-end button visibility and backend `@has_permission`; the seeds in `sql/v2_init.sql` are generated from what the backend actually checks, so the two cannot drift apart.
5. **One fact, one owner, one copy** (execution contract, see `docs/workflow-execution-contract.md`): externally only `executionId` / `pendingApprovals` / `submit`; internal coordinates (child execution id, pause scope, generation) appear solely on the troubleshooting endpoint.
6. **No legacy compatibility shims**: superseded endpoints, fields and comments get deleted, not marked `@deprecated`, so old and new paths never run in parallel.
7. **Storage moves bytes only**: business code has exactly `upload/download/delete/exists`; swapping backends changes no code, and since there is no "give me a local path" API, document parsing happens in memory.
8. **Uniform request/response shape**: every response is `ApiResponse{code, message, data}`, exceptions are normalised by the global handler, model-call failures pass the upstream status code through.

---

## 7. Roadmap

1. Knowledge base: keep tuning the graph vector projection (`rag_kg_vector`) and the retrieval fusion strategy; deepen the audio-video kb layout; expand the graphflow worker to more shards as needed
2. Harness agent: an autonomous planning loop on top of the skill / tool / MCP resource centres (`:9005` / `:9006`)
3. Datasets / training / inference / evaluation / Notebook
4. Kubernetes manifests and a Helm chart

## 8. Further reading

| Document | Content |
|---|---|
| `docs/workflow-execution-contract.md` | External contract and internal design of the execution domain (**any new external field must change this file first**) |
| `docs/workflow-approval-memory.md` | Approval & memory state machines, review conclusions, implementation notes |
| `docs/workflow-design.md` | Orchestration module design |
| `sql/v2_init.sql` | The only database initialisation entry for a fresh environment |
| `sql/init_nacos.yaml` | Nacos config sample: what every `storage:` field means and how the three arq pipelines stay in sync (graphflow reuses the `arq_ragflow` section) |
| `docs/RAG知识库模块开发SPEC.md` | Requirements and design baseline of the knowledge-base (RAG) module: three kb types, the ragflow / graphflow pipeline split, top-level ACL with raw queries at ES/Neo4j |

---

<p align="center">If this project helps you, a Star is appreciated ⭐ · Issues and PRs welcome</p>
