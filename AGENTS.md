# AGENTS.md — start here if you are an AI implementing this repo

This is a **spec-first** project. The architecture, data contracts, and acceptance criteria already
exist; your job is to realize them faithfully, not to redesign them. **Read before you write.**

---

## 1. Orientation — read in this order

1. **`README.md`** — system purpose, the L0→L1→L2→L3 pipeline, tech stack, repo layout, sprint table.
2. **`packages/ekc_schemas/models.py`** — read the top-of-file docstring end to end; it is both the
   contract *and* the rulebook (six numbered conventions). Then skim the models.
3. **`docs/implementation-plan.md`** — the rationale, the MVP cut (§8), and the open questions (§9).
4. **The spec for your assigned layer** in `docs/specs/`. Read the whole spec, not just your ticket.

| Spec | Covers | Status |
|---|---|---|
| `00-l0-ingest.md` | L0 ingest & normalization (deep dive) | ✓ implemented (S1) |
| `01-layer1-enrichment.md` | L1: identity (§3), graph (§4), roles (§5), clustering (§6), events (§7) | ✓ all sections done (S1–S4) |
| `02-project-view.md` | Project-view surface + its API contract | ✓ implemented (S3–S4) |
| `03-project-clustering.md` | Deep dive of L1 §6 (build-ready) | ✓ implemented (S3) |
| `04-storage-schema.md` | Postgres + pgvector schema & migrations | ✓ implemented through migration 0006 (`message_embedding`, S7.1) |
| `05-network-map.md` | Network-map surface + API contract | ✓ implemented (S2) |

## 2. The mental model

- Pipeline: mailbox → **L0 ingest** (clean/normalize) → **L1 enrich** (materialize Person, Org,
  Project, Edge, Event) → **L2 retrieve** → **L3 synthesize** → UI surfaces.
- The differentiated value is **L1 structuring**, not retrieval. L0 makes the data clean; L1 makes
  it structured; L3 turns it into cited answers; L2 is plumbing.
- The MVP product direction is **employee-initiated audited handoff packages** (D14), not generic
  mailbox search. The covered employee scopes/reviews/publishes a package; the recipient receives
  package-scoped evidence, not raw mailbox access.
- A "project" is *constructed*, not given — there is no `project_id` in email (the reason spec 03 exists).

## 3. Invariants you cannot break

1. **Import models from `ekc_schemas`; never re-declare or fork them.** Inline code in specs is
   illustrative; if it conflicts with the schema file, the file wins.
2. **Two id schemes, never mixed:** internal foreign keys use UUID `id`; citations/provenance use
   `Message.message_id_header` (RFC 5322 Message-ID), so they survive re-ingest and open the email.
3. **No citation, no claim.** Any asserting object (currently `Event`) carries ≥1 `source_message_ids`
   holding `message_id_header` values — not UUIDs. The schema enforces this at construction; do not
   engineer around it.
4. **Confidence ∈ [0,1];** the UI hides inferred facts below a per-tenant threshold (default 0.4).
5. **Strip the owner exactly once, in L1:** included in `Thread.participants`, excluded from `Edge`
   and clustering features. Do not re-strip elsewhere.
6. **Determinism & idempotency:** use the fixed `seed`; identical inputs ⇒ byte-identical output;
   persist via idempotent upsert keyed on `message_id_header`. No wall-clock or RNG in logic paths.
7. **Respect the layer boundary:** L0 emits normalized *addresses*, never `person_id`. Identity
   resolution (spec 01 §3) is the bridge — do not build the relationship graph before it runs.
8. **Volume is not accomplishment.** Never let email count imply impact; this is the core product
   failure mode the grounding discipline (proposed/did/outcome) exists to prevent.
9. **Do not build surveillance.** No productivity scores, performance summaries, responsiveness
   metrics, effort inference, ranking, or employment-decision support.
10. **Operational:** the LLM appears only in L3; sensitivity tags gate data (default `exclude`);
   OAuth tokens never touch the app DB or logs.

## 4. How to execute a spec

The two deep-dive specs (00 and 03) share one shape — learn it once:

- **Scope & invariants** — what you build and the rules around it.
- **Data contracts** — a pointer to `ekc_schemas`; import, don't redefine.
- **Stages** — each section is headed with its target module file (e.g. `normalize/threads.py`,
  `communities.py`) and carries reference code. Implement file by file, in order.
- **Parameters & defaults** — go in `params.py`; nothing hardcoded in logic.
- **Edge cases / Observability / Acceptance (Definition of Done)** — your test and review checklist.
- **Sprint task breakdown** — dependency-ordered tickets, each with a done-condition (spec 00:
  0.1–0.10; spec 03: 3.1–3.11). Take a ticket; don't invent scope outside it.
- **Open decisions / questions** — confirm with a human before they block you; don't guess.

Implement to the Definition of Done, run the eval where one exists, and prove determinism.

## 5. Sprint & dependency order

- **S0 ✓** — schemas contract (`packages/ekc_schemas/models.py`), DB spec (spec 04), and seed fixture
  (`fixtures/`, 18 messages + `gold/`) are all in place.
- **S1 ✓** — L0 ingest (`services/ingest/`) + identity resolution + relationship graph
  (`services/enrich/identity.py`, `graph.py`). 38 passing tests.
- **S2 ✓** — role inference (`services/enrich/roles.py`, spec 01 §5) + Alembic migrations
  0001–0005 (`alembic/versions/`) + SQLAlchemy mappers (`services/db/`) + FastAPI network-map
  endpoints (`services/api/`, spec 05) + React frontend (`frontend/`). 46 passing tests.
  DB runs via Docker Compose (`docker-compose.yml`). Seed with `python scripts/dev_seed.py`.
- **S3 ✓** — project clustering (spec 03) + project view surface (spec 02). 124 tests.
- **S4 ✓** — event extraction (spec 01 §7) + L3 synthesis (project summary, contact summary).
  138 tests. `services/enrich/events/`, `services/synthesis/`.
- **S5 ✓** — bounded L1-only cover-for-me query (D11). `POST /api/cover-for-me/{mailbox_id}`.
  Routes over Person, Project, Event, Edge, Thread in the DB; word-boundary entity detection;
  citation allow-list enforced; "insufficient structured evidence" on no match. 148 tests.
  **All three MVP surfaces are now shipped. S6 quality-pass tooling complete.**
- **S7 ✓** — L2 hybrid retrieval. S7.1–S7.11 implemented and live-validated:
  migration 0006, `MessageEmbedding` schema/ORM/mappers, embed client seam,
  `RetrievalParams`, idempotent embed backfill, vector retrieval (`pgvector` HNSW cosine),
  FTS retrieval (`subject_clean_tsv` + `websearch_to_tsquery`), hybrid merge/scoring/quality
  gate, reranker boundary hardening, retrieval eval (7/7 hard gates, MRR 1.0, top-1 1.0),
  cover-for-me L2 upgrade (L1+L2 hybrid routing, L2-only path, citation allow-list enforced),
  and live Voyage validation (15 messages embedded, 7/7 eval cases pass with voyage-4
  query vectors against voyage-4 document vectors, MRR=1.000, top-1 precision=1.000).
  S7 core retrieval is complete and live-validated. Optional S7.12 hosted Voyage reranker
  remains off by default and is not required for MVP.
- **S8 ✓** — Real-Mailbox Demo Readiness. Five tasks all complete and live-validated:
  S8.1 real-mailbox backfill validation (smoke Gmail mailbox);
  S8.2 API/UI evidence transparency (`supporting_evidence` field + citation chips with subject/date);
  S8.3 operational preflight (`scripts/preflight.py` + `GET /api/preflight`);
  S8.4 graceful failure UX (`retrieval_status` enum, distinct states for missing key / rate-limit /
  no embeddings / no hits);
  S8.5 real-mailbox smoke eval (5–10 curated cases, `--embed-client voyage`).
  See `docs/s8-implementation-plan.md`.
- **S9 ✓** — Project clustering materialization on live mailboxes
  (`scripts/materialize_projects.py`). Runs the S3 clustering pipeline against an
  ingested mailbox and persists Project / ThreadProjectAssignment / ProjectMember
  rows so Project View and cover-for-me project routing work on real data. Reuses
  stored `voyage-4` embeddings as the clustering `embed_fn`; `--confirm` requires
  every eligible message to have an embedding (no silent zero-vector clustering);
  whole-thread sensitivity exclusion (a thread with any non-`{none}` message is
  excluded); deterministic `--limit-threads`; idempotent persist with commit.
- **S10 ✓** — Local runtime reliability. `VoyageEmbedClient` switched
  from the `voyageai` SDK to the Voyage REST API over `httpx` (removes the
  `langchain`/`uuid_utils` native chain blocked by Windows Application Control —
  see decisions.md D12b S10 note); preflight now constructs the embed client to
  prove L2 works in this runtime (optional `--live-embed` for a billed probe,
  off by default); blessed Windows launchers `scripts/check_local_env.ps1`,
  `run_backend.ps1`, `run_frontend.ps1` (no bare `python`); frontend request
  timeout + typed errors so views never hang, Vite `strictPort` on 5173, and
  `GET /api/health` for reachability.
- **S11 ✓** — Frontend demo polish: clickable citation chips open an evidence
  drawer (subject/date/message_id_header/snippet/retrieval source from
  `supporting_evidence` only — no sensitive content, no claim without citation);
  repeated citations deduped; distinct titled error states; display-only project
  label cleanup (`utils/projectLabels.ts`); demo-readiness strip.
- **S12 ✓** — Product shell + landing. Dependency-free client router
  (`src/router.tsx`) over the History API; routes `/` (landing), `/app`
  (overview), `/app/network`, `/app/projects`, `/app/cover`, `/app/status`.
  Workspace shell (`src/workspace/Workspace.tsx`) holds mailbox state + data
  hooks across navigation; professional nav + compact mailbox + single health
  dot. Workspace Overview is the default `/app` entry (counts, readiness,
  suggested questions, top projects). Marketing landing page (`Landing.tsx`).
  Cover-for-me suggested-question chips; project search filter. Frontend-only —
  no backend/schema/AI/retrieval/clustering changes. See
  `docs/s12-product-shell-landing-plan.md` for the manual demo script.
- **S13 ✓** — Relationship Map / tree. New `services/relationships/`
  package derives a graph-backed, tree-renderable relationship map *live* from
  existing L1 tables (no persisted table — see the persistence note in
  `derive.py`). Relationship types: direct_exchange (owner↔person from Edge),
  thread_copresence and project_copresence (person↔person), org_affiliation
  (person→org/domain), bridge (person across ≥2 projects). Whole-thread
  sensitivity + noise exclusion; people known only from excluded threads never
  surface. New `GET /api/relationship-map/{mailbox_id}` (mode=owner|project|org|
  graph, plus root_id/project_id/min_weight/recency_days/relationship_types),
  registered in main.py; the existing network-map endpoints are untouched. New
  frontend "Relationship Map" tab at `/app/relationships` (owner tree default)
  with mode selector, type/recency/min-evidence filters, force-graph render, and
  an evidence drawer; Network Map is preserved. Edge weight/width is labeled as
  evidence volume, never importance. 13 derivation tests; live-validated on the
  smoke mailbox (36 eligible vs 383 excluded threads).
- **S14 ✓** — Evidence & source navigation polish. New safe
  `GET /api/source-message/{mailbox_id}` endpoint keyed by
  `message_id_header`, with mailbox-boundary checks, malformed-UUID 404s, and
  the same whole-thread sensitivity gate used by Cover-for-me
  `supporting_evidence`. Citation drawers now show subject/sender/date/snippet,
  copy Message-ID, and best-effort Gmail `rfc822msgid` search; Relationship Map
  direct-exchange message IDs open source detail, while structural project /
  thread / domain edges show provenance notes and never fabricate message IDs.
  See `docs/s14-implementation-plan.md`.
- **S15 ✓** — Verification hardening. S15.1 fixes S9 materialization test
  contamination by isolating DB fixture state and preserving dry-run invariants.
  S15.2 adds `docs/s15-verification-matrix.md`, the canonical definition of
  local, DB-gated, demo-mailbox, and live-integration "green" states. Use it
  before quoting test counts; counts differ by environment and by whether
  `DATABASE_URL` / live API keys are configured.
- **S16.0 ✓** — Date-range ingest. Adds customizable Gmail date-window preview /
  ingest for large mailboxes and scoped snapshots; date-windowed runs bypass
  stored sync tokens and do not save new ones; replace-snapshot requires an
  explicit date bound and confirmation. See `docs/s16-date-range-ingest-plan.md`.
- **S16 planned / next** — Canonical demo readiness. Purpose-built coverage
  handoff fixture (D13), evidence-trust demo spine, and demo green validation.
  With D14, the demo should preview the employee-reviewed handoff package flow.
  See `docs/s16-demo-readiness-plan.md`.
- **S17.2–S17.17 ✓** — Audited handoff package MVP (D14), shipped and
  end-to-end validated. S17.2 domain spec; S17.3 draft/scope/generate backend;
  S17.4 creator scope-review UI; S17.5 publish/revoke + one-time capability code
  + recipient session/package endpoints; S17.6 read-only recipient view at
  `/handoff/recipient` (fragment-stripped code, sessionStorage-resumed session);
  S17.7 creator publish + one-time share-link UI (no creator "open" affordance
  that would consume the code); S17.8 end-to-end validation + docs alignment
  (`test_full_creator_to_recipient_journey`, `docs/s17-live-validation.md`);
  S17.9 recipient package-local ask (`POST /api/handoff/recipient/ask`,
  `services/handoff/ask.py`) — **deterministic and LLM-free**, grounded only in
  the package's own evidence, no existence oracle; S17.10 package versioning /
  new-version re-share (`POST /api/handoff/{id}/new-version`) — forks a frozen
  package into a fresh draft in the same lineage (copied scope, no claims/
  evidence/recipient/code); publishing supersedes the prior published version and
  blocks its recipient; S17.11 static HTML export
  (`GET /api/handoff/{id}/export.html`, `services/handoff/export_html.py`) — a
  self-contained, escaped, read-only snapshot of a frozen package with recipient-
  view privacy parity (no mailbox id / counts / links / tokens); S17.12
  package-local recipient nav tree (`frontend/src/components/
  PackageNavigationTree.tsx`, `frontend/src/utils/packageTree.ts`) — a
  **frontend-only** contents outline derived purely from the recipient payload's
  claims + evidence (no live-mailbox / relationship-map / source-message calls);
  S17.13 manual-demo readiness — creator "Start over" loading-state fix + a
  creator-only empty-generation diagnostic (`generation` on the creator package
  response: `no_events_for_mailbox` / `no_events_in_scope` /
  `all_events_excluded_by_policy`) that explains an empty candidate without
  weakening any invariant and is never shown to the recipient; S17.14 makes the
  creator workspace mailbox refresh-safe (persists ONLY the mailbox UUID in
  `sessionStorage`, key `ekc_workspace_mailbox_id`) so creator deep links survive
  a reload, and adds `scripts/seed_handoff_demo.py` — a deterministic, LLM-free,
  puluo-isolated demo mailbox with seeded L1 `Event` rows so the full Handoff flow
  (generate → publish → recipient → export → versioning) can be demoed (puluo has
  zero Events and cannot generate a package until LLM event extraction runs);
  S17.15 adds a canonical manual-demo runbook
  (`docs/s17-handoff-manual-demo-runbook.md`) + a `--verify` seed mode
  (side-effect-free generate dry-run) and hardened operator output; S17.16 makes
  `GET /api/network-map/{id}` fall back to the mailbox `owner_person_id` and
  return an empty graph (200) instead of 404 for a mailbox with an owner but no
  L1 identity/edge graph (the Handoff demo mailbox), so its Network/Relationship
  tabs degrade to "No contacts yet" rather than looking broken; S17.17 reshapes
  the recipient view into a compact topic-centric coverage brief — a
  coverage-area selector (`frontend/src/utils/coverageAreas.ts`) drives a focused
  panel (decisions/open-loops first, people/domains, evidence collapsed),
  grouped purely from the snapshot with honest evidence-subject labels (no
  backend/API change); it supersedes + removes the S17.12 nav tree.
  Deferred to S17.18+: optional LLM synthesis for the package ask, PDF/docx/zip
  export, manager approval, multi-recipient, **rich snapshotted relationship/
  project/owner trees** (S17.12 ships the lightweight nav tree only), and a
  stronger production auth boundary. See `docs/s17-handoff-package-mvp-plan.md`.

## 6. Known gaps — flag, don't fake

- **L2 retrieval** — implemented in S7.1–S7.10 under `services/retrieval/`. See D12 in
  `docs/decisions.md` and `docs/s7-implementation-plan.md` for the full design.
  The previous note ("an external query router owns it") is rescinded by D12 and superseded
  by the local hybrid retriever now in production.
- **`message_embedding` table + HNSW index** — implemented by migration 0006 in S7.1.
  Dimension: 1024 (`voyage-4`). Each row stores `embed_model`, `embed_dim`, `content_hash`,
  and `embedded_at` alongside the vector.
- **Cover-for-me L2 upgrade** — completed in S7.11. Same surface API (`POST /api/cover-for-me/{mailbox_id}`).
  Internally upgraded to L1 exact routing plus L2 hybrid supporting evidence. L2 becomes the
  primary source when L1 has no entity match. API contract unchanged; no new UI surface.
- **L3 synthesis** — `services/synthesis/` is built (S4): project "What's been done" and contact
  "Ask about this contact", both citation-validated. Cover-for-me (S5/S7, D11/D12) routes over
  L1 structured objects first, with L2 recall support after S7.
- **Role inference** (spec 01 §5, `services/enrich/roles.py`) is a rules-based v1. It correctly
  classifies all fixture contacts but is not a learned classifier. Upgrade path: labeled data →
  small classifier, logged in the spec.
- **M365 provider** (`services/ingest/providers/msgraph.py`) — raises `NotImplementedError`; Gmail
  is the only live provider. M365 drops in without pipeline changes (D2).
- **Object store for raw MIME** — `Message.raw_uri = None` in all current runs. Wire when deploying
  against a real mailbox (D6).

Terminology heads-up: the implementation-plan narrative sometimes writes `message_id` loosely; the
authoritative field name is `Message.message_id_header`.

## 7. Making changes to shared contracts

- To add or change a shared model: edit `packages/ekc_schemas/models.py` and **bump `SCHEMA_VERSION`**.
  Never fork a model into a service.
- Import as `from ekc_schemas import Message, Person, Project, ...`.
- Python 3.11 backend; Pydantic v2; per-service module layout is given at the bottom of each spec.

## 8. Definition of done (every task)

- Imports schemas; introduces no forked or re-declared models.
- Meets the spec's Acceptance / Definition-of-Done checkboxes.
- Passes its eval where one exists (clustering: extended-BCubed gates, spec 03 §22; L0 classifiers:
  labeled-sample precision/recall, spec 00 §19).
- Deterministic: identical output on identical input + seed.
- Honors privacy: sensitivity exclusion, least-privilege scope, no tokens in DB or logs.

## 9. When docs disagree

Precedence: **`packages/ekc_schemas/models.py` → your layer's spec → `implementation-plan.md` → README →
this file.** Where specs overlap, the newer (spec 03) overrides the older (spec 01 §6). When a spec's
**"Open decisions"** list contains an item that **`docs/decisions.md`** has resolved, the decision log
wins. **Surface the conflict** to a human; do not silently choose.
