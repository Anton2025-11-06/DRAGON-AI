# 🐉 DRAGON-AI — A self-hostable multi-tenant AI middle platform

> **Model gateway, Knowledge RAG, Knowledge Graph, Workflow orchestration, a Deep-Exploration agent (Harness) and enterprise RBAC in one workspace** — your data, models and permissions stay under your control.

> Online demo: `120.77.58.245:12345` (viewer / viewer) · [中文](README.md) / English

> This README is kept in sync with the code: **everything marked "Done" actually runs; treat "Planned" as unavailable**.

---

## 1. Core capabilities

| Module | Problem it solves | Representative features |
|---|---|---|
| RBAC & operations foundation | Capability, permission and runtime governance for multi-user setups | RBAC, row-level ACL, audit log, rate limiting, online users & ARQ monitor |
| Model gateway & model square | Unified onboarding, dispatch and governance of multi-vendor models | OpenAI-compatible entry, 12 capabilities × 3 providers, apply/approve, API keys |
| Workflow orchestration | Break complex tasks into visual flows | 21 node types, submit contract, human approval, SSE/WS, memory, function calling |
| Knowledge base & RAG | Feed answers precise retrieved content | three kb types, hybrid retrieval, graph enhancement, RAGAS eval, open API |
| Deep exploration (Harness) | Let the agent actually run tasks and deliver results | AgentScope ReAct + per-user cloud sandbox, persistent workspace, inline preview & artifact delivery |
| Execution engine & infrastructure | A high-concurrency, horizontally scalable runtime | four arq pipelines, unified storage (local/NAS/OSS), Nacos, trace-id |

---

## 2. Capabilities in detail

### 2.1 RBAC & operations foundation
- **A permission point *is* a menu row** (`menu_type=3`): one dataset drives both front-end button visibility and backend `@has_permission`; ADMIN bypasses checks. Five data scopes (all / dept & below / own dept / self only / custom dept set).
- **Row-level ACL, one engine platform-wide**: seven resource types (knowledge base / document / workflow / template / tool / skill / MCP connection) granted per action to user / role / department (with sub-tree) / user group / everyone; the decision is `ADMIN ∪ owner ∪ data scope ∪ explicit grant`. `acl_router` is the single read/write surface and `resource_guard` the single evaluator — list push-down / per-row button / 403 all consume the same rule set, never hand-written twice.
- JWT + Redis session revoked on logout; end-to-end `x-trace-id` with auto-masked sensitive fields; per-module hot-reloadable rate limits; force-logout and the four-pipeline ARQ monitor.

### 2.2 AI model gateway & model square
- **OpenAI-compatible entry** `POST /api/model`: api-key auth → model consistency check → QPS + daily quota → vendor call → streaming SSE / plain JSON.
- **12 model capability types × 3 providers** (OpenAI-compatible / DashScope / Zhipu): text generation, embeddings, rerank, image & video understanding, OCR, text-to-image, text/image-to-video, text-to-audio, audio-to-text, …
- Capability flags `supports_stream / thinking / function_call` are registered per model and anything unregistered is never sent out; the full square lifecycle (onboard → apply → approve → API key → connectivity test → online/offline toggle) plus an in-browser multi-model playground.

### 2.3 Workflow / agent orchestration
- Visual canvas `{nodes, edges}`: draft / publish / version snapshot / rollback / duplicate, with strict graph validation before publish (reachability, ports, required params, cycles).
- **21 node types**: boundary, AI (LLM / classifier / parameter extractor), control (IF / loop / parallel / assigner / aggregator / reply / **approval**), data (template / code / list / document extractor / knowledge retrieval), external (HTTP / tool / MCP / **sub-workflow**).
- **The execution contract has exactly three concepts**: `executionId` / `pendingApprovals` / `submit` — first run, continue, re-run and answering an approval are all the same `submit`; human approvals bubble up from nested sub-workflows via a one-shot `approvalToken`.
- WebSocket + SSE + DB replay, pause TTL watchdog, per-node timeout, global job idempotency, multi-turn memory (drop / auto-compress), function calling (an LLM node mounts tools / MCP / sub-workflows).

### 2.4 Knowledge base & RAG (with retrieval evaluation)
- **Three knowledge-base types**: document / image-text / audio-video, each with per-library default retrieval params and generation model; the table, the ES indexes and the Neo4j graph are kept in sync by the service layer.
- Parsing & chunking: `common_file_parser` with two engines (native pypdf/docx/xlsx… · minerU private HTTP) + auto dispatch and 8 chunking strategies; chunks are vectorized into ES `rag_knowledge_chunk`.
- Hybrid retrieval: BM25 + 1024-dim dense kNN fused with per-request weights · rerank chosen per request · an optional LightRAG-style graph path merged (entity/relation projection into `rag_kg_vector`).
- **Knowledge-graph pipeline (graphflow, split from ragflow)**: walks every chunk through the LLM to mine entities/relations into Neo4j — far slower than parsing, so it runs on its own queue and `arq_graphflow` worker instead of starving rag ingestion slots; failed reruns default to checkpoint resume (only re-extract the batches that failed).
- RAGAS evaluation on 5 metrics + three latency phases, run async on ARQ; open retrieval API (API-key + rate limit, up to 100 files per call).

### 2.5 Deep exploration (Harness agent)
- **AgentScope ReAct agent**: every turn is enqueued to its own `harness_queue`, holding one model stream + one sandbox session per turn, fully isolated from workflow / ragflow / graphflow.
- **One cloud sandbox per user + a persistent workspace** (self-hosted OpenSandbox): idempotent `workspace_id`, a custom image that ships the gateway venv (first-time bootstrap skipped); the workspace mounts a **persistent volume** per user (a host NAS directory mapped to the container's `/workspace`) — **deliverables survive even after the server destroys/rebuilds the sandbox on its max-lifetime limit**; when a cached handle goes stale it is probed against the control plane and, if not `running`, evicted and rebuilt so the handle is genuinely usable.
- **Streaming timeline chat**: thinking / tool calls / answers render in frame-arrival order, each segment collapsible; SSE frame protocol `turn.started / text.delta / thinking.delta / tool.started / tool.completed / turn.artifacts / turn.completed|interrupted|failed`.
- **Human-in-the-loop (AskUser)**: the agent can pause mid-turn to ask for a parameter/confirmation and resume from the breakpoint; manual interrupt is supported with the terminal status owned by the message row; AgentState lives in Redis so API and worker processes hand turns across processes.
- **Upload optimization**: uploads go through the backend's dedicated `write_file` writing raw bytes directly (no shell, no single-arg size limit, no base64 chunk/decode overhead); downloads read whole raw bytes via `read_file` (the exec channel truncates big stdout); large files go through object storage / OSS.
- **Final-artifact auto-rendering**: at the end of a turn the agent explicitly registers deliverables with `present_output`, which emits a `turn.artifacts` frame persisted with the message; the chat renders them as clickable **file cards** and the right-hand workspace gives **multi-format inline preview** (image / PDF / audio-video / Markdown / code / CSV / HTML / text), auto-expanding to preview the first artifact when it arrives for the current session's turn.
- Mountable resources: skills (SKILL.zip seeded into the sandbox `skills/` partition), tools, MCP connections, knowledge-base retrieval — ACL-checked with the same engine as the picker; each turn can carry its own model & mount config.

### 2.6 Execution engine & infrastructure
- arq + Redis sharded queues, **four pipelines consuming independently** (workflow / ragflow / graphflow / harness); routed by `crc32(task_id) % shardCount` so one execution_id / doc_id / turn always lands on the same shard, multi-process scalable; the shard count lives in Redis so the monitor page can resize on the fly.
- **Storage abstraction** `StorageBackend`: local directory / Aliyun OSS (SDK V2 async) switched via the Nacos `storage:` section or env vars, and the sandbox persistent volume can be backed by a host NAS mount; **diskless** — the backend only moves bytes, parsing happens in memory.
- Nacos owns every environment difference (register + pull config on start, no hard-coded address); loguru + `x-trace-id` surviving gateway → service → worker.

---

## 3. Quick start

### Requirements

| Dependency | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Back end |
| Node.js / pnpm | 20.12+ / 10+ | Front-end build |
| MySQL | 8.x | Business data |
| Redis | 5+ (7.x recommended) | Sessions / rate limits / queues |
| Nacos | 2.x+ | Registry + config centre, **mandatory** |
| Aliyun OSS / NAS | optional | Object storage / sandbox persistent volume |
| OpenSandbox Server | self-hosted (optional) | Sandbox runtime for deep exploration; not needed if the feature stays off |

### Initialising a fresh environment (four steps)

1. **Create the schema and seed data** — one file covers 28 business tables plus role / admin / department / menu / permission seeds, safe to re-run (`CREATE TABLE IF NOT EXISTS`, every seed is `INSERT IGNORE`):
   ```bash
   mysql --default-character-set=utf8mb4 -h <host> -u root -p < sql/v2_init.sql
   ```
2. **Create the Nacos configs** — one per `data_id` = service name (`service_system/login/workflow/rag/gateway/harness` and `arq_workflow/arq_ragflow`), copying `sql/init_nacos.yaml` and changing connection details. Key points: each `arq_*` `redis.url` must match the producer service's Redis or jobs go unconsumed; graphflow adds no new `data_id` (it reads the `arq_ragflow` section, only the queue name is separate); deep exploration needs the `service_harness` section (opensandbox / agentscope / skills).
3. **Start the back end** — six services plus the four worker pipelines; see "Running locally" or "Docker".
4. **Start the front end** — `pnpm build:antd` produces static files; serve them with nginx and proxy `/api` to the gateway.

Initial account `admin / Admin@123` — **change the password right after the first login**. For an existing database, run the incremental scripts under `sql/` by version to catch up on columns and permission points.

### Running locally

```bash
pip install -r requirements.txt

python -m service.service_gateway.gateway      # :18000
python -m service.service_system.system        # :9001
python -m service.service_login.login          # :9004
python -m service.service_workflow.workflow    # :9003
python -m service.service_rag.rag              # :9002 (needs ES + Neo4j)
python -m service.service_harness.harness      # :9005 (needs an OpenSandbox server)

# arq workers: -t is required, one process group per pipeline (fully separated queues)
python -m arq_tasks.run_workers -t workflow  -n 4 -p 1
python -m arq_tasks.run_workers -t ragflow   -n 2 -p 1
python -m arq_tasks.run_workers -t graphflow -n 2 -p 1
python -m arq_tasks.run_workers -t harness   -n 2 -p 1

cd ui-ai && pnpm install && pnpm dev:antd      # :5666 (already proxies /api → 127.0.0.1:18000)
```

### Docker

One back-end image + one front-end image; **which process a container runs is decided by the injected `SERVICE_NAME`**. All-in-one via two compose files (bring the back end up first, then build/run the front end separately so a single `--build` never compiles both at once and OOMs the host):

```bash
cp docker/.env.example docker/.env   # fill in NACOS_ADDR
docker compose -f docker/docker-compose.backend.yml up -d --build   # back end: 6 services + 4 workers (+ optional OpenSandbox)
docker compose -f docker/docker-compose.ui.yml up -d --build        # front end: after the back end is up
# open http://<host>:8080 (UI_PORT)
```

The compose files already cover every service and all four pipelines; per-container `docker run` examples, environment variables (`SERVICE_NAME` / `NACOS_ADDR` / `<service_name>_port` / `ARQ_WORKERS` / `SPLIT_NUMBER`, …) and start-up script details live in `docker/`, `scripts/` and the "Further reading" docs. The `k8s/` directory is reserved; no manifests yet.

---

## 4. Tech stack

| Layer | Choice |
|---|---|
| Back end | Python 3.11 · FastAPI (fully async) · SQLAlchemy 2.0 + aiomysql · Pydantic v2 |
| Process & deploy | gunicorn + UvicornWorker · Docker · Nacos (registry / config centre) |
| Data & middleware | MySQL 8.x · Redis · Aliyun OSS · Elasticsearch 8.x · Neo4j 5.x |
| Async tasks | arq 0.28 sharded queues; the workflow / ragflow / graphflow / harness pipelines consume independently, multi-process scalable |
| Agents | agentscope 2.x (ReAct + workspace/skill framework) · self-hosted OpenSandbox cloud sandbox (one persistent workspace per user) · official MCP Python SDK |
| Retrieval | two ES indexes `rag_knowledge_chunk` + `rag_kg_vector`: BM25 + 1024-dim dense kNN with per-request weights · rerank chosen per request · an optional graph retrieval path merged in |
| Document parsing | `common_file_parser`: native + minerU two engines + auto selection, 8 chunking strategies |
| Evaluation | RAGAS 5 metrics, with a home-grown langchain-core adapter wiring this repo's model services into the judge |
| Communication | httpx global connection pool (HTTP/2) · WebSocket · SSE (sse-starlette) |
| Front end | Vue 3.5 · TypeScript · Vite · vben v5 (pnpm + turbo monorepo) · ant-design-vue · Vue Flow · CodeMirror/Monaco |
| AuthZ/AuthN | JWT + Redis session · dual-channel API key · RBAC permission points |

---

## 5. Project layout

```
├── common/            # shared layer: app bootstrap / nacos / middleware / permission / model / parser / storage / pools
├── service/           # business micro-services, one port each
│   ├── service_gateway/   :18000 routing + auth + rate limit + AI model gateway
│   ├── service_system/    :9001  RBAC + model square + ops monitors
│   ├── service_login/     :9004  login / register
│   ├── service_workflow/  :9003  orchestration (execution / workflow_engine / routers…)
│   ├── service_rag/       :9002  knowledge base: configs / docs / retrieval / graph / eval / open API
│   └── service_harness/   :9005  deep exploration: sessions/turns/SSE/sandbox file proxy + agent (ReAct / workspace factory / skill seeding)
├── arq_tasks/         # worker settings for all four pipelines + task functions + multi-process launcher
├── ui-ai/             # front end, vben v5 monorepo (apps/web-antd is the one we ship)
├── scripts/           # start-up scripts, shared by containers and bare metal
├── docker/            # Dockerfile (back end) · Dockerfile.ui (front end) · compose · nginx template
├── sql/               # v2_init.sql (the only fresh-install entry) · init_nacos.yaml · old/ (historical incrementals)
├── docs/              # design docs
└── k8s/               # reserved
```

---

## 6. Architecture

```
Front end ui-ai (Vue3 + vben v5)  ── only calls /api/**, same-origin via vite/nginx, no CORS
        │
        ▼
service_gateway :18000 ── the single external entry: Nacos discovery · JWT · rate limit · audit · x-trace-id · API key
        │                       /api/model: OpenAI-compatible entry (key → quota → vendor → SSE)
        ▼
system :9001 · login :9004 · workflow :9003 · rag :9002 · harness :9005
        │                       │ async execution (Redis sharded queue, four independent pipelines)
        │   arq workflow · arq ragflow · arq graphflow · arq harness
        ▼
Infra  Nacos (registry + all business config) · MySQL · Redis · Aliyun OSS · ES · Neo4j · OpenSandbox (one persistent workspace per user)
```

**Conventions that run through the codebase**: ① one `create_app` bootstraps every service, rolling back the Nacos registration if a dependency fails to initialise; ② authorise at the top, query raw at the bottom — ES/Neo4j/storage only query by the id allow-list handed down and never make permission decisions; ③ environment differences exist only in Nacos, no connection string in code; ④ storage moves bytes only, so swapping backends changes no code.

---

## 7. Development progress

**✅ Done (usable)**: `service_system` (RBAC / ACL / model square / monitors), `service_login`, `service_gateway` (incl. AI model gateway), `service_workflow` (canvas / 21 nodes / submit / approval / memory), `service_rag` (three kb types / hybrid retrieval / graphflow graph / RAGAS / open API), `service_harness` (ReAct + persistent cloud sandbox / timeline / AskUser / upload optimization / artifact auto-rendering), the four `arq_tasks` pipelines, `common_storage`, and the `ui-ai` front end.

**📋 Planned (empty skeletons only — do not read as features)**: `service_datasets`, `service_train`, `service_inference`, `service_eval_model`, `service_notebook` — they reserve a package directory, a rate-limit bucket name and a gateway alias, nothing more.

---

## 8. Roadmap

1. Knowledge base: keep tuning the graph vector projection and retrieval fusion; deepen the audio-video layout; expand the graphflow worker to more shards
2. **Multi-agent team orchestration**: evolve deep exploration from a single agent into multi-agent collaboration — compose several agents (each with its own role / model / tools / mounted resources) into a *team* with task delegation, message hand-off and shared sandbox artifacts
3. Datasets / training / inference / evaluation / Notebook
4. Kubernetes manifests and a Helm chart

---

## 9. Further reading

| Document | Content |
|---|---|
| `docs/workflow-execution-contract.md` | External contract and internal design of the execution domain (change it first before adding any external field) |
| `docs/workflow-approval-memory.md` | Approval & memory state machines, review conclusions, implementation notes |
| `docs/workflow-design.md` | Orchestration module design |
| `docs/RAG知识库模块开发SPEC.md` | Requirements and design baseline of the knowledge-base (RAG) module |
| `sql/v2_init.sql` | The only database initialisation entry for a fresh environment |
| `sql/init_nacos.yaml` | Nacos config sample (what every `storage:` field means and how the arq pipelines stay in sync) |

---

<p align="center">If this project helps you, a Star is appreciated ⭐ · Issues and PRs welcome</p>
