# Email Knowledge Continuity

> Turns a departing or covered employee's mailbox into a structured, queryable map
> of people, projects, roles, and evidenced work — so a successor can take over fast.

**Status:** S0–S13 complete. S7 L2 retrieval live-validated (S7.1–S7.11; optional S7.12 hosted Voyage reranker remains off by default). S8 real-mailbox demo readiness complete. S9 project-clustering materialization complete (`scripts/materialize_projects.py`). S10 local runtime reliability complete (Voyage embedding via direct HTTP, runtime preflight probe, blessed Windows launch scripts, no-hang frontend). S11 demo polish complete (inspectable citation evidence drawer, deduped citations, display-only project label cleanup, readiness strip). S12 product shell + landing complete (client routing, workspace overview, marketing landing page, Cover-for-me onboarding). S13 Relationship Map complete (new graph-backed, tree-renderable relationship view derived live from L1 tables; owner/project/org/graph modes; sensitive-thread exclusion; Network Map preserved). See D12 in `docs/decisions.md`.
Running: Python 3.13 · PostgreSQL 16 + pgvector (Docker) · React frontend.
Target wedge: **coverage** (employee present, opt-in).

---

## TL;DR for engineers and AI agents reading this repo

- **AI agents implementing this: start with `AGENTS.md`** — read order, the hard rules, and how to pick up a sprint.
- The pipeline has four stages: **L0 ingest → L1 enrich → L2 retrieve (RAG) → L3 synthesize.**
- The differentiated value is **L1 (structuring)**, *not* retrieval. Start with
  `docs/specs/01-layer1-enrichment.md`.
- Every user-facing claim MUST be **citation-bound** to a source `message_id_header`. No citation, no claim.
- Schemas in `packages/ekc_schemas/` are the single source of truth. Generate, don't hand-copy.
- Each spec section is tagged `@sprint` and `@acceptance` for sprint planning — see
  [Sprint orientation](#sprint-orientation).

## Why

Onboarding is well tooled; offboarding and coverage are not. When someone leaves, goes
on leave, or hands off a role, the institutional memory in their inbox — who they worked
with, what they owned, the live state of each project — evaporates. This system turns the
unstructured mailbox into structured knowledge a successor can query.

## Two products, one engine

| | Coverage (0-to-1, build first) | Offboarding (v2) |
|---|---|---|
| Trigger | Vacation, leave, role handoff | Departure / termination |
| Employee present? | Yes — participates (clean consent) | No (admin-side, after the fact) |
| Data freshness | Current | Historical |
| Scrutiny | Low | High (reads as monitoring) |
| Buyer | Manager | HR / IT |

Same pipeline underneath; different go-to-market and risk profile.

## Architecture

```
   mailbox ─► [L0 ingest] ─► [L1 enrich] ─► [L2 retrieve/RAG] ─► [L3 synthesize] ─► UI
                                  │
              materializes Person / Org / Project / Edge / Event objects
              that L2 queries and L3 cites.
```

| Layer | Service | Responsibility |
|---|---|---|
| L0 | `services/ingest` | OAuth pull, thread reconstruction, dedupe, noise + sensitivity tagging |
| L1 | `services/enrich` | identity resolution, relationship graph, role inference, project clustering, event extraction |
| L2 | `services/retrieval` | embeddings + hybrid (vector + structured) retrieval over L1 objects |
| L3 | `services/synthesis` | grounded, citation-bound answer generation |

Full design rationale: `docs/implementation-plan.md`.

## Tech stack

One language per side: Python 3.11+ across the backend (data, ML, and services share a runtime),
TypeScript/React on the frontend. Pydantic schemas in `packages/ekc_schemas` are the single contract
that threads every layer, and FastAPI turns those same schemas into the API and OpenAPI types.

| Area | Choice | Why |
|---|---|---|
| Language (backend) | Python 3.11+ | One runtime for ingest, ML enrichment, and services. |
| Language (frontend) | TypeScript + React 18 | Network map + project view surfaces. |
| Schemas / contract | Pydantic v2 → OpenAPI → TS types | One source of truth; no drift between API and UI. |
| L0 ingest | `google-api-python-client`, `msgraph-sdk`, `authlib`, `html2text`, `charset-normalizer`, `tldextract` | Provider pull + MIME/body normalization. `talon` for quote/sig stripping where available; regex fallback otherwise. |
| L1 enrich | `rapidfuzz` (identity); `numpy`, `scipy`, `scikit-learn`, `spaCy`, `sentence-transformers`, `hnswlib`, `python-igraph` + `leidenalg` (clustering, S3) | Identity resolution, graph, role inference, clustering. |
| L2 retrieval | Postgres + `pgvector`, hybrid with Postgres FTS (BM25) | Vectors live next to relational data — one store to start. Graduate to Qdrant/OpenSearch only at scale. |
| L3 synthesis | Claude via Anthropic API, structured outputs (`instructor`/Pydantic) | The only nondeterministic component, isolated behind the citation contract. |
| API | FastAPI (async) | Pydantic-native; the schemas *are* the API. |
| Frontend graph | React + `react-force-graph-2d` (D3 force simulation) | D9: chosen over sigma.js for simpler React integration at current scale. sigma.js is the upgrade path if the graph grows to thousands of nodes. |
| Primary store | PostgreSQL 16 + `pgvector` (Docker) | Objects, embeddings, FTS in one engine. Migrations via Alembic. |
| Object store | S3-compatible | Raw MIME archive + debug artifacts (attachments hash-only by default). Deferred to production hardening. |
| Queue / cache | Redis + a task queue (`arq`/`dramatiq`) | Rate-limited ingest, async enrichment jobs. Synchronous for S1/S2 fixture runs; wired when real Gmail is connected. |
| Secrets | Vault or cloud secrets manager | OAuth tokens are the crown-jewel credential — never in app DB/logs. Env-var shim behind `get_token()` for development. |
| Infra | Docker Compose (dev); orchestration TBD (k8s / ECS / Fly) | CI runs tests + the clustering eval gates (spec 03 §18). |
| Observability | `structlog` + OpenTelemetry/Prometheus | Plus per-stage debug artifacts (spec 03 §21, spec 00 §18). |

Guiding choices: keep infra lean for the 0-to-1 (Postgres+pgvector instead of a separate vector
DB), isolate the LLM at L3 so everything upstream is deterministic and debuggable, and let the
embedding model be configured once and shared between L1 features (spec 03 §5) and L2 retrieval.

## Repository layout

```
email-archive/
  README.md
  AGENTS.md                         # start here if you are an AI implementing this repo
  docker-compose.yml                # Postgres 16 + pgvector dev DB
  pyproject.toml                    # package config + pytest settings
  requirements.txt
  alembic.ini
  alembic/
    env.py
    versions/
      0001_baseline.py              # extensions + mailbox + audit tables + schema_meta
      0002_l0_tables.py             # thread + message (with FTS tsvector) + attachments
      0003_l1_tables.py             # org + person + identity + edge
      0004_l1_projects.py           # project + event + assignments (S3)
      0005_fix_event_citation_check.py  # D8: cardinality() replaces array_length()
      0006_message_embedding.py        # message_embedding + HNSW + subject_clean_tsv; schema v0.2.0 (S7.1)
  docs/
    implementation-plan.md          # the why + end-to-end design
    decisions.md                    # D1–D12 resolved build decisions (supersede spec open decisions)
    specs/
      00-l0-ingest.md               # L0 ingest + normalization  ✓ S1
      01-layer1-enrichment.md       # L1: identity, graph, roles, clustering, events  ✓ S1-S4
      02-project-view.md            # project-view surface + activity panel  ✓ S3-S4
      03-project-clustering.md      # L1 §6 deep dive — full clustering impl  ✓ S3
      04-storage-schema.md          # Postgres + pgvector schema & migrations  ✓ S2
      05-network-map.md             # network-map surface + API  ✓ S2
  fixtures/                         # seed synthetic mailbox + gold labels
    generate.py                     #   deterministic generator (source of truth)
    mailbox.json                    #   18-message synthetic mailbox L0 ingests
    gold/                           #   hand-labeled answers the acceptance gates check against
  packages/
    ekc_schemas/                    # installable schema package (`pip install -e packages/`)
      __init__.py
      models.py                     #   THE source of truth — import, never re-declare
    pyproject.toml                  #   declares `ekc-schemas`; root pyproject.toml depends on it
  services/
    db/                             # SQLAlchemy ORM models + Pydantic↔row mappers
      engine.py  models.py  mappers.py  store.py
    ingest/                         # L0  ✓ S1
      providers/                    #   base.py · fixture.py · gmail.py · msgraph.py (stub)
      normalize/                    #   address · threads · body · artifacts · noise · sensitivity
      params.py  store.py  pipeline.py
    enrich/                         # L1  ✓ S1-S4
      identity.py  graph.py  roles.py  params.py  pipeline.py
      clustering/                   # project clustering (spec 03)  ✓ S3
        params.py  features.py  blocking.py  similarity.py  graph.py
        communities.py  materialize.py  confidence.py  labeling.py
        incremental.py  pipeline.py  testkit.py
        eval/  metrics.py  run_eval.py
      events_llm.py                 # production extract_fn (Anthropic, D10)  ✓ S4
      events/                       # event extraction orchestration (spec 01 §7)  ✓ S4
        __init__.py
        eval/  run_eval.py
    api/                            # FastAPI  ✓ S2-S4
      main.py  deps.py
      routers/network_map.py  routers/project_view.py  routers/synthesis.py
      schemas/network_map.py  schemas/project_view.py
    retrieval/                      # L2 hybrid retrieval (S7.1–S7.10 done)
      contracts.py                  #   RetrievalHit + InsufficientEvidence
      embed_client.py               #   FakeEmbedClient + VoyageEmbedClient seam
      params.py                     #   RetrievalParams
      vector.py                     #   pgvector HNSW retrieval
      fts.py                        #   Postgres FTS retrieval
      hybrid.py                     #   vector + FTS merge, scoring, quality gates
      reranker.py                   #   Reranker protocol + NoOpReranker
      eval/                         #   S7.10 hard-gate retrieval eval
    synthesis/                      # L3  ✓ S4
      params.py  client.py  contracts.py
      project_summary.py  contact_summary.py
  frontend/                         # React 18 + TypeScript + react-force-graph-2d  ✓ S2-S4
    src/
      api/        # client.ts + types.ts (network map + project view + synthesis)
      components/ # NetworkMap · RoleLegend · ContactPanel · ProjectList · ProjectDetail
      hooks/      # useNetworkMap · useContactDetail · useProjects · useProjectDetail
      utils/      # roleColors.ts
  scripts/
    dev_seed.py                     # seed fixture mailbox + all L1 layers; --serve starts uvicorn
    download_models.py              # download spaCy en_core_web_sm for production runs
    embed_backfill.py               # idempotent embedding backfill for a mailbox (S7.5)
    _env.py                         # load_local_env() helper for CLI scripts (dotenv, dev-only)
  tests/                            # example baseline: 475 passed, 103 skipped as of S13 (DB-gated tests skip without DATABASE_URL)
    test_l0_*.py   test_l1_*.py   test_clustering_*.py
    test_db_roundtrip.py   test_api_network_map.py
```

## Conventions

- **Language:** Python 3.11 for the backend. Data models are Pydantic v2, defined
  authoritatively in `packages/ekc_schemas/models.py` — import them, never re-declare them.
- **IDs:** every object has a stable `id` (UUIDv4). Email provenance is preserved as
  `message_id_header` (RFC `Message-ID`) and provider `thread_id`; citations use `message_id_header`.
- **Citations:** any asserting field carries `source_message_ids: list[str]` holding
  `Message.message_id_header` (RFC) values — not internal ids — so they resolve to openable email.
- **Confidence:** inferred fields (`role`, `project` membership) carry a `confidence: float`
  in `[0, 1]`. The UI renders inferred facts only above a configurable threshold.
- **Idempotency:** every L1 stage is a pure function of its inputs + a content hash, so a
  mailbox can be re-processed without duplicating objects.
- **Sprint tags:** spec sections carry `@sprint S<n>` and `@acceptance` blocks so this repo
  is plannable by humans and parseable by AI agents.

## Sprint orientation

| Sprint | Theme | Status | Primary doc |
|---|---|---|---|
| S0 | Schemas contract + DB spec + synthetic fixture | ✓ done | `packages/ekc_schemas`, spec 04 |
| S1 | L0 ingest + identity resolution + relationship graph | ✓ done | spec 00, spec 01 §3–§4 |
| S2 | Role inference + DB migrations + network-map surface | ✓ done | spec 01 §5, spec 05 |
| S3 | Project clustering + project view surface | ✓ done | spec 03, spec 02 |
| S4 | Event extraction + L3 grounded synthesis | ✓ done | spec 01 §7 |
| S5 | Cover-for-me query (bounded L1-only, D11) | ✓ done | implementation-plan §6.3 |
| S6 | Real-mailbox quality pass (live report, eval, smoke dataset, identity/graph inspection) | ✓ done | docs/s6-real-mailbox-quality-pass.md |
| S7 | L2 hybrid retrieval (voyage-4 embeddings, pgvector HNSW, hybrid retrieval, D12) | ✓ done (S7.1–S7.11); optional S7.12 hosted Voyage reranker remains | docs/s7-implementation-plan.md |
| S8 | Real-Mailbox Demo Readiness — real-mailbox backfill, evidence transparency, preflight, graceful failure UX, smoke eval | ✓ done | docs/s8-implementation-plan.md |
| S9 | Project-clustering materialization on live mailboxes (Project/assignment/member persistence; embedding-gated; whole-thread sensitivity exclusion) | ✓ done | `scripts/materialize_projects.py` |
| S10 | Local runtime reliability — Voyage embedding via direct HTTP (no voyageai SDK), runtime preflight probe, blessed Windows launch scripts, no-hang frontend, Vite strictPort | ✓ done | `docs/decisions.md` D12b S10 note |
| S11 | Demo polish — inspectable citation evidence drawer, deduped citations, distinct error states, display-only project label cleanup, readiness strip | ✓ done | frontend |
| S12 | Product shell + landing — client routing, workspace overview, status screen, marketing landing page, Cover-for-me onboarding, project search | ✓ done | `docs/s12-product-shell-landing-plan.md` |
| S13 | Relationship Map — graph-backed tree views (owner/project/org/graph) derived live from L1; new `services/relationships/` + `/api/relationship-map`; new tab beside Network Map | ✓ done | `docs/s13-relationship-map-tree-plan.md` |

**Real Gmail smoke ingest (production-hardening-demo):**
```bash
# 1. Obtain an OAuth token with gmail.readonly scope (see scripts/gmail_smoke_ingest.py docstring).
#    Store the token JSON — exactly these fields:
#    { "token": "...", "refresh_token": "...", "token_uri": "https://oauth2.googleapis.com/token",
#      "client_id": "...", "client_secret": "...",
#      "scopes": ["https://www.googleapis.com/auth/gmail.readonly"] }
export GMAIL_TOKEN='<paste token JSON here>'   # never committed; never logged

# 2. Smoke-check first (fetches one message, persists nothing):
python scripts/gmail_smoke_ingest.py --owner-email you@example.com --confirm --smoke-check

# 3. First real run (capped at 200 messages):
python scripts/gmail_smoke_ingest.py --owner-email you@example.com --max-messages 200 --confirm

# 4. Incremental (uses stored historyId automatically):
python scripts/gmail_smoke_ingest.py --mailbox-id <uuid> --owner-email you@example.com --confirm
```

**Quick start (S0–S13 local stack):**

On Windows, prefer `scripts/run_backend.ps1` and `scripts/run_frontend.ps1` from
the "Windows local stack" section below — they pin the blessed `.venv` interpreter
and a deterministic port, so you never need the bare `python`/`uvicorn` commands
shown here.

```bash
# 1. Python deps (one time)
pip install -e .[dev]      # app + all dev/test/api/db/gmail deps
# or: pip install -r requirements.txt  (same thing)
# optional: pip install -e packages/  # only if actively editing models.py

# 2. Database
docker compose up -d                                          # start Postgres + pgvector
DATABASE_URL=postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev \
  alembic upgrade head                                        # apply migrations

# 3. Run
python scripts/dev_seed.py                                    # seed fixture; prints mailbox UUID
uvicorn services.api.main:app --reload                        # API on :8000
cd frontend && npm install && VITE_MAILBOX_ID=<uuid> npm run dev  # UI on :5173

# 4. Tests
DATABASE_URL=postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev \
  pytest                                # example baseline: 475 passed, 103 skipped as of S13 (DB-gated tests skip without DATABASE_URL)

# For live embedding backfill or the live Voyage integration test, set VOYAGE_API_KEY.
# Offline tests and --dry-run use FakeEmbedClient and do not require a key.
# Production deployments should configure a Voyage AI payment method so standard
# rate limits apply; free-tier limits are only appropriate for fixture/demo runs.
# See CLAUDE.md for full authorization rules before using the key.
```

**Windows local stack (blessed runtime, no bare `python`):**

On Windows there is exactly one supported Python runtime for local dev:
`.\.venv\Scripts\python.exe`. Three PowerShell scripts make startup
deterministic so an operator never has to use a bare `python` on PATH:

```powershell
# Validate the runtime (venv present, backend imports clean, embed client
# does not pull in the blocked voyageai/uuid_utils native chain):
.\scripts\check_local_env.ps1

# Start the backend: validates env, runs preflight (gated), then uvicorn on :8000.
# Pass -MailboxId to also verify embeddings; -Force starts despite preflight fail.
$env:EKC_MAILBOX_ID = "<mailbox-uuid>"
.\scripts\run_backend.ps1

# Start the frontend on a deterministic port (5173, strictPort). In a SECOND
# window. If 5173 is busy it fails loudly — Ctrl+C the old frontend window first.
.\scripts\run_frontend.ps1 -MailboxId <mailbox-uuid>
```

The backend runs preflight before binding the port; `python -m scripts.preflight
--mailbox-id <uuid>` can also be run standalone. Preflight now constructs the
Voyage embed client to prove L2 will actually work in this runtime (mere
`VOYAGE_API_KEY` presence is not enough); add `--live-embed` to make one tiny
billed API call that verifies the credential end-to-end (off by default). The
Voyage embed client uses a plain HTTP path (httpx) and deliberately does **not**
import the `voyageai` SDK, which dragged in a native `uuid_utils` `.pyd` that
Windows Application Control blocks.

## Privacy guardrails (non-negotiable)

- The mailbox holds **third-party personal data**; those people retain GDPR/CCPA rights.
- L0 must tag privileged / legal / HR / personal content so later layers can exclude it.
- All access is logged (immutable audit trail); support retention limits and deletion.
- *Not legal advice — a privacy review precedes any regulated-buyer sale.*
