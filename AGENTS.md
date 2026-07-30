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
- **S16 — superseded in practice / optional** — Canonical demo readiness was a
  broader purpose-built coverage-handoff fixture + evidence-trust demo spine
  (D13). For the current demo path it is **superseded** by the shipped S17
  handoff-demo work: the seeded **handoff-demo** mailbox
  (`scripts/seed_handoff_demo.py`) drives Handoff package generation, and
  **puluo** drives Cover-for-me / Relationship Map / Network Map, per
  `docs/s17-handoff-manual-demo-runbook.md`. Any remaining S16 "broader demo-story
  / landing" work is optional future polish, not a blocker. See
  `docs/s16-demo-readiness-plan.md`.
- **S17.2–S17.20 ✓** — Audited handoff package MVP (D14), shipped and
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
  backend/API change); it supersedes + removes the S17.12 nav tree. S17.18 turns
  that into a three-part recipient workspace — left coverage-area rail · center
  brief (decisions/outcomes → next actions → blockers → key facts, evidence
  tucked under a disclosure) · Related people & domains section
  (`peopleDetailForEvidence`, honest "Sender"/"Domain contact" labels, no
  invented roles, no volume=importance) + the package-local Ask. S17.19 fixes
  two S17.18 usability gaps: Ask becomes a workspace-level top tab (never buried
  at the bottom), and supporting evidence is attached inline to the specific
  claim it supports (an expandable disclosure per claim; the separate global
  supporting-messages section removed; "no citation, no claim" preserved).
  S17.20 converts the creator Handoff review to a workspace layout (full-width
  package header + sticky right action rail so Publish is reachable without
  scrolling; mobile promotes the rail to the top) and adds an Overview-readiness
  clarification note; immutability, copy-only one-time link, and creator-only
  content (exclusion counts, Copy ID, Remove) all preserved.
  Deferred beyond S17: optional LLM synthesis for the package ask, PDF/docx/zip
  export, manager approval, multi-recipient, **rich snapshotted relationship/
  project/owner trees** (S17.17–S17.19 ship a package-local topic workspace, not a
  graph), and a stronger production auth boundary. See
  `docs/s17-handoff-package-mvp-plan.md`.
- **S18 — shipped as a docs-only plan; not implemented in code.** Hosted product readiness / web-app deployment
  plan: what must be true to go from the localhost/demo MVP to a hosted web app
  (production roles + auth boundary, Gmail-first OAuth + token vault, hosted
  architecture, background-job states, data-tier boundaries with the recipient
  package-local-snapshot invariant intact, privacy/compliance posture, a
  readiness gap checklist, a phased launch path, and an S19+ sprint map). No code
  this sprint. See `docs/s18-hosted-product-readiness-plan.md`.
- **S19 — shipped as a spec; not implemented in code.** Auth + tenant boundary spec: the production
  identity/tenant/authorization model S20+ implements — tenant/workspace model,
  user roles, mailbox ownership + tenant binding, creator/recipient/admin
  permissions, the dev-only `mailbox_id` mode + fail-closed `AUTH_MODE` that
  disables it in production, the authorization check required on every creator
  endpoint, and auth-sensitive audit events. No code this sprint; shipped S17
  behavior unchanged (dev mode preserves the localhost flow); OAuth/token vault
  deferred to S20. See `docs/s19-auth-tenant-boundary-plan.md`.
- **S20 — shipped as a spec; not implemented in code.** OAuth + token vault spec: safe production
  Gmail connect (authorization-code flow with state/nonce, provider-account
  verification, owner/tenant binding per S19), a token-vault boundary that keeps
  refresh tokens out of the app DB and logs (app DB holds only a `vault_ref` +
  safe provider metadata, per D6), the proposed `mailbox_provider_account` object
  (service DB only, not `ekc_schemas`), least-privilege read-only Gmail scopes (no
  write/send), fail-closed provider-account-mismatch handling, full token
  lifecycle, OAuth audit events, and a threat model. Gmail only; M365 stays the D2
  stub. No code this sprint; recipient snapshot-only invariant untouched. See
  `docs/s20-oauth-token-vault-plan.md`.
- **S21 — shipped as a spec; not implemented in code.** Background job orchestration spec: the
  production system that runs long/retryable work (Gmail ingest, L1 enrichment,
  event extraction, embedding backfill, project materialization, cleanup/
  retention; heavy handoff generation + PDF/DOCX/ZIP export later) outside web
  requests — a tenant-scoped `job` model + states (queued/running/succeeded/
  failed/canceled/partially_succeeded), per-type authz/idempotency/retry/progress,
  S16.0 ingest as a job (confirm starts it; `replace_snapshot` staged swap),
  vault-backed token resolution inside the worker (no tokens in payloads/logs),
  S19 authz re-checked by the worker, safe audit events, a Postgres-backed queue +
  worker (leases/heartbeat/stuck-job recovery) default, rate/cost controls, and a
  concrete S22+ implementation map. No code this sprint; recipient snapshot-only
  invariant untouched. See `docs/s21-background-job-orchestration-plan.md`.

- **S22–S23 ✓ implemented.** S22 adds the auth/tenant boundary (implements S19):
  `Tenant`/`AppUser`/`TenantMembership` + `Mailbox.tenant_id`/`owner_user_id`
  (migration 0010), fail-closed `AUTH_MODE`, and `require_owner_mailbox`/
  `require_owner_package` on every creator/mailbox route. S23 adds the Gmail OAuth
  + token-vault minimal slice (implements S20): `services/oauth/` +
  `services/api/routers/oauth_gmail.py` + migration 0011 — a state+PKCE start/
  callback/disconnect/status flow, a `mailbox_provider_account` storing only
  `vault_ref` + safe metadata (raw tokens live only in the vault; the shipped
  `DevTokenVault` is dev/test-only), mismatch/fail-closed rules, and a vault-backed
  production resolver. Recipient routes are unchanged and never touch OAuth/vault/
  provider data.
- **S24 ✓ implemented.** Background job infrastructure (implements S21):
  `services/jobs/` + `services/api/routers/jobs.py` + migration 0012 — a
  tenant-scoped `job` table (six states), enqueue/status/list/cancel APIs (S22
  owner/tenant-guarded), a Postgres `FOR UPDATE SKIP LOCKED` worker claim with
  lease/heartbeat + expired-lease reclaim, idempotency dedupe, safe-metadata
  sanitization (params/progress/summary/errors never carry content/tokens/
  traces), and a harmless `noop` job. Infra only — no ingest/enrichment/backfill
  moved into jobs yet. Recipients have no principal and cannot reach job routes.
- **S25 ✓ implemented.** Gmail date-range ingest moved onto the S24 runner
  (`services/jobs/handlers/gmail_ingest.py` = `gmail_ingest_window`,
  `scripts/run_worker.py`): the confirm endpoint validates + verifies the account
  request-time (S16.0 confirm / `replace_snapshot` / account-guard safeguards
  preserved), then enqueues an idempotent job; a worker runs the
  fetch/normalize/persist. Preview stays synchronous. Only date-range ingest is
  moved — enrichment / embedding backfill / project materialization stay manual
  scripts (S26). Recipients never touch jobs.
- **S26 ✓ implemented.** The post-ingest pipeline runs as jobs too
  (`services/jobs/handlers/pipeline.py`, `services/api/routers/pipeline_jobs.py`):
  `l1_enrichment`, `event_extraction`, `embedding_backfill` (cost-gated — dry-run
  estimate unless the operator confirms; no live Voyage call otherwise, consistent
  with the standing Voyage-key rule), and `project_materialization` (S9 embeddings
  precheck). Each wraps the existing core logic so `scripts/embed_backfill.py` +
  `scripts/materialize_projects.py` still work. Owner/tenant-guarded; safe metadata
  only; recipients never touch jobs.
- **S27 ✓ implemented.** Hosted deployment readiness
  (`services/hosted_readiness.py`, `docs/s27-hosted-deploy-readiness-plan.md`,
  `docs/s27-hosted-deploy-runbook.md`): a safety-gated slice, **not** a broad hosted
  migration. An environment-validation module of pure, injectable `PreflightCheck`
  functions (auth mode, deploy env, dev-principal bypass, token vault +
  `EKC_ALLOW_DEV_VAULT`, `DATABASE_URL` not dev-default, DB reachable + at Alembic
  head, OAuth config + non-localhost https redirect, access-log redaction installed,
  no wildcard CORS, recipient snapshot-only static assertion, worker/job-queue
  observability, cost-gate/kill-switch info). **Fail-closed startup guards** on the
  API (`services/api/main.py` lifespan) and worker (`scripts/run_worker.py`) that are
  a **no-op unless `EKC_DEPLOY_ENV=production`** and otherwise refuse to boot on any
  hard failure with a safe banner. A safe **`GET /readyz` + `/api/readyz`** (bare
  `ready`/`degraded`, no leak; `/healthz` unchanged), a **migration-free**
  worker-readiness signal off the existing `job` table lease/heartbeat, and a
  **`scripts/preflight.py --hosted`** command. Two-signal gate (§9.1):
  `EKC_DEPLOY_ENV=production`+`AUTH_MODE=production` enforces; `EKC_DEPLOY_ENV=production`
  +`AUTH_MODE=dev` fails startup; `AUTH_MODE=production` without `EKC_DEPLOY_ENV`
  degrades `/readyz`. **No schema migration** (head stays `0012_job_infra`). Recipient
  package access stays package-local snapshot-only. Non-goals unchanged: no infra
  provisioning, admin UI, telemetry vendor, M365, production IdP, recipient auth, or
  retention enforcement.
- **S28 — planned, docs/spec-only (not implemented).** Admin / Audit Viewer +
  Operations Console (`docs/s28-admin-audit-ops-plan.md`): a governance + operational
  visibility surface over **safe metadata only** — package lifecycle, provider-account
  connection metadata, job status, audit trails, aggregate exclusion counts, and
  readiness/ops health — plus exactly two audited admin actions (**revoke package**,
  **disconnect provider account**, each with a mandatory reason). Roles reuse the
  shipped `TenantMembership` enum (`admin`, `security_reviewer`) plus an `operator`
  concept (S19 §6 keeps operator infra-level; §15.1 open question). Hard rule: **no
  admin route is a content backdoor** — no mailbox bodies/subjects/snippets, package
  evidence bodies, excluded content, Gmail/source links, OAuth tokens/codes/vault
  refs, provider/LLM responses, raw `Job.params`, or tracebacks; admin package access
  is metadata-only; recipients stay package-local snapshot-only and unchanged. Proposed
  `/api/admin/*` endpoints + allow-list DTOs. **No migration** for the read-only viewer
  (reads existing rows; a product `operator` role would be one later constraint
  widening). Implementation is S29 (read-only viewer) then S30 (actions).
- **S29 ✓ implemented.** Read-only Admin / Audit Viewer (`services/admin/` read-service
  + `services/api/routers/admin.py`, tests `tests/test_s29_admin_viewer.py`), implementing
  the read set of the S28 spec. `GET /api/admin/*`: `overview`, `packages`,
  `packages/{id}`, `packages/{id}/audit`, `provider-accounts`, `jobs`, `jobs/{id}`,
  `audit`, `exclusions/summary`, `readiness`. Guarded by new `require_admin` /
  `require_admin_or_reviewer` deps on the S22 `Principal.roles`; tenant-scoped
  (cross-tenant → 404), unauthenticated in production → 401, wrong in-tenant role → 403.
  **Allow-list DTOs (`services/admin/contracts.py`) — safe metadata only:** package
  lifecycle (incl. `reason_category`, the safe enum, never free text), provider-connection
  metadata, safe job fields, `AuditLog`/`HandoffAuditEvent` (whitelisted metadata
  projection), and **aggregate exclusion counts only**. Security reviewers see a
  domain/masked recipient email and, for provider accounts, only provider/status/
  timestamps — no email/scopes/ids/mismatch (§18.3/§18.5, Option A). **No field
  leaks** evidence bodies, claim text, scope detail, source headers, raw `Job.params`/
  `error_message`/`worker_id`, `sync_token`, `vault_ref`, tokens, or DB URLs (a
  sentinel-seeded test asserts absence). Sensitive package-detail reads write an
  `admin.package.viewed` audit event. **No mutations** (revoke/disconnect are S30),
  **no migration** (head `0012_job_infra`), recipient routes untouched and package-local
  snapshot-only.

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
