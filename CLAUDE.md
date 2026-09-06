# CLAUDE.md — Project Instructions for Email Knowledge Continuity

This file is read by Claude Code at the start of every session. Instructions
here override default behavior and apply to all work in this repository.

---

## Mandatory response opener (no exceptions)

**Begin every single response to the user with the exact phrase:**

> Hi, I am not hallucinating

This applies without fail to every reply — the smallest acknowledgement, a
one-line answer, a clarifying question, a status update, a full task report,
everything. It comes first, before any other text. It does not change, replace,
or excuse any of the actual work; it is purely a required prefix, after which you
continue normally. There are no exceptions and no situations where it may be
omitted.

---

## Completion response format

After finishing any task — a commit, a bug fix, a refactor, a doc update,
anything where you are reporting back to the user — write the response in
**plain prose paragraphs**, not tables.

The response must be detailed enough that a code reviewer who has not seen
the conversation can read it cold and understand exactly what changed and why.
Structure it as one paragraph per logical concern (schema change, new tests,
mapper fix, doc update, etc.). Each paragraph should state what the problem
or requirement was, what was changed, and why the change is correct.

Do not use markdown table syntax (`| col | col |`) anywhere in a completion
response. Tables break when copy-pasted into plain-text tools and the
formatting becomes unreadable. Use a flat bulleted list only if you have
more than five distinct items with no explanatory prose needed per item;
prefer full sentences otherwise.

**Example of what not to write:**

    | Finding | Fix |
    |---|---|
    | P1 schema bump | bumped to 0.2.0 |

**Example of what to write:**

    P1 — The SCHEMA_VERSION constant in ekc_schemas/models.py was not bumped
    after MessageEmbeddingRecord was added. AGENTS.md requires a version bump
    for any shared contract change. The constant was updated from 0.1.0 to
    0.2.0, and packages/pyproject.toml was bumped to match so the package
    version stays in sync with the runtime constant.

Apply this format to: post-commit summaries, reviewer finding responses,
end-of-sprint wrap-ups, and any other message where you describe what you did.

---

## Voyage AI API key — authorization required

**VOYAGE_API_KEY is stored in `.env` (gitignored). It must never be used
without the owner's explicit instruction for that specific run.**

Rules that apply in every session, without exception:

1. Do not run `scripts/embed_backfill.py` without `--dry-run` unless the user
   explicitly says to embed (e.g. "run the backfill", "use the key").
2. Do not run any code path that constructs `VoyageEmbedClient` or calls the
   Voyage embed/rerank API.
3. Do not run the live integration test (`test_voyage_embed_documents_live`)
   — it is skip-guarded on `VOYAGE_API_KEY` and that guard must stay.
4. `FakeEmbedClient` is the correct default for all automated tests and CI.
5. If a task could plausibly trigger an API call, stop and ask for explicit
   confirmation before proceeding.

These rules apply even if `VOYAGE_API_KEY` is present in the environment.
The key costs money per token and may transmit mailbox content to a third
party. Full authorization from the user is required before every real use.

---

## Project orientation

Read `AGENTS.md` first before starting any implementation task. It contains
the sprint history, hard rules, and the convention that specs and decisions
in `docs/decisions.md` override anything written elsewhere.

Key docs:
- `docs/decisions.md` — resolved build decisions (authoritative; D14 locks the handoff-package MVP direction)
- `docs/s7-implementation-plan.md` — S7 task breakdown and locked decisions
- `docs/implementation-plan.md` — overall pipeline architecture
- `docs/s18-hosted-product-readiness-plan.md` — S18 (spec shipped, not implemented
  in code): hosted web-app deployment readiness + sprint map
- `docs/s19-auth-tenant-boundary-plan.md` — S19 (spec shipped, not implemented in
  code): production auth + tenant/authorization model (implemented by S22+)
- `docs/s20-oauth-token-vault-plan.md` — S20 (spec shipped, not implemented in
  code): safe Gmail OAuth connect + token-vault boundary (Gmail only)
- `docs/s21-background-job-orchestration-plan.md` — S21 (spec shipped, not
  implemented in code): production background-job system for long/retryable work.
  Authoritative implementation sequence is S21 §14 (S22 auth → S23 OAuth/vault →
  S24 jobs → S25 ingest → S26 enrichment → S27 deploy)
- **S22 ✓ + S23 ✓ implemented.** S22 = auth + tenant boundary (migration 0010,
  fail-closed `AUTH_MODE`, owner/tenant-gated creator routes). S23 = Gmail OAuth +
  token-vault minimal slice (`services/oauth/`, `services/api/routers/oauth_gmail.py`,
  migration 0011). App DB stores only a `vault_ref` + safe provider metadata; the
  shipped `DevTokenVault` is dev/test-only. Recipient access stays snapshot-only.
- **S24 ✓ implemented.** Background job infrastructure (implements S21):
  `services/jobs/` + `services/api/routers/jobs.py` + migration 0012 — tenant-scoped
  `job` table, enqueue/status/list/cancel APIs (S22-guarded), Postgres
  `FOR UPDATE SKIP LOCKED` worker (lease/heartbeat, idempotency, safe metadata),
  a `noop` validation job. Infra only; recipients never touch jobs.
- **S25 ✓ implemented.** Gmail date-range ingest moved onto the S24 runner
  (`services/jobs/handlers/gmail_ingest.py`, `scripts/run_worker.py`): confirm
  endpoint enqueues an idempotent `gmail_ingest_window` job (S16.0 confirm/
  replace_snapshot/account-guard preserved; preview stays synchronous). Only
  date-range ingest is moved — enrichment/backfill/materialization are S26.
- **S26 ✓ implemented.** Post-ingest pipeline as jobs
  (`services/jobs/handlers/pipeline.py`, `services/api/routers/pipeline_jobs.py`):
  `l1_enrichment`, `event_extraction`, `embedding_backfill` (cost-gated — no live
  Voyage call without an explicit operator confirm), `project_materialization`
  (S9 embeddings precheck). Wraps existing core so the CLI scripts still work.
- **S27 ✓ implemented.** Hosted deployment readiness (`services/hosted_readiness.py`,
  `docs/s27-hosted-deploy-readiness-plan.md`, `docs/s27-hosted-deploy-runbook.md`):
  a safety-gated slice, not a broad hosted migration. An environment-validation
  module + fail-closed startup guards on the API (lifespan) and worker that no-op
  unless `EKC_DEPLOY_ENV=production` and otherwise refuse to boot with dev auth/vault,
  missing/dev `DATABASE_URL`, un-migrated DB, missing/localhost OAuth config, missing
  callback-log redaction, wildcard CORS, an unobservable queue, or a recipient-router
  regression. Safe `GET /readyz` (bare status, no leak), migration-free worker
  readiness off the `job` table, and `scripts/preflight.py --hosted`. No migration
  (head `0012_job_infra`); recipient access stays snapshot-only.
- **S28 ✓ implemented by S29 (read-only viewer) + S30 (audited actions); originating spec.** Admin / Audit Viewer + Operations
  Console (`docs/s28-admin-audit-ops-plan.md`): governance + ops visibility over **safe
  metadata only** (package lifecycle, provider-connection, jobs, audit trails, aggregate
  exclusion counts, readiness) + two audited admin actions (revoke package, disconnect
  provider account, each with a mandatory reason). Hard rule: **no admin route is a
  content backdoor** — no mailbox/evidence bodies, excluded content, Gmail/source links,
  OAuth tokens/codes/vault refs, provider responses, raw job params, or tracebacks;
  admin package access is metadata-only; recipients stay package-local snapshot-only.
  Implementation is S29 (read-only viewer) then S30 (actions); no migration for the
  read-only viewer.
- **S29 ✓ implemented.** Read-only Admin / Audit Viewer (`services/admin/` +
  `services/api/routers/admin.py`): `GET /api/admin/*` (overview, packages, package
  detail/audit, provider-accounts, jobs, audit, exclusions/summary, readiness), guarded
  by tenant `admin`/`security_reviewer` roles, tenant-scoped (cross-tenant → 404).
  Allow-list DTOs, safe metadata only — no evidence/claim bodies, scope detail, raw job
  params/errors, sync tokens, vault refs, tokens, or DB URLs; reviewers get masked
  recipient email + provider accounts limited to provider/status/timestamps
  (no email/scopes/ids); aggregate exclusion counts only. No
  mutations (S30), no migration; recipient routes untouched.
- **S30 ✓ implemented.** Audited admin actions: two tenant-admin-only, reason-gated
  mutations — `POST /api/admin/packages/{id}/revoke` (reuses the shared S17 revoke
  lifecycle `services/handoff/lifecycle.py`; blocks recipient access + kills sessions;
  audits `package.revoked_by_admin` with the reason) and
  `POST /api/admin/provider-accounts/{id}/disconnect` (reuses the S23 vault path
  `services/oauth/flow.py::disconnect_account`; provider revoke + vault purge; audits
  `provider_account_disconnected_by_admin`). Reviewer/creator → 403, cross-tenant/
  malformed → 404, blank reason → 422. No token/`vault_ref` exposed; no migration;
  recipient snapshot-only untouched.
- **S31 ✓ implemented.** Admin / Audit Viewer **frontend** only (`frontend/src/components/admin/`,
  dev-gated `/app/admin` nav + route — route-gated, so production can't render the
  console via a direct URL): a compact tenant-scoped console over `/api/admin/*` —
  overview/readiness, package list/detail/audit, provider status, jobs, audit log,
  exclusion summary, and the two audited actions behind a typed-reason modal. No
  backend/schema/migration/dependency change; renders only safe metadata (no bodies/
  tokens/vault_ref); respects reviewer masking; recipient routes untouched.
- **S34 ✓ implemented (backend + UI + demo).** Return handoff / coverage delta (D15;
  docs/s33-return-handoff-coverage-delta-plan.md). Reciprocal package
  (`package_type=return_delta`, new lineage) from the coverer's mailbox back to the
  original employee — not a `new-version`. **Migration 0013** (`package_type`, safe
  `coverage_return` reason, `handoff_return_context`); S34's head was
  `0013_return_handoff`, and the **current alembic head is
  `0014_handoff_claim_project_label`** after S39. Auto scope-seed resolves original coverage-area labels →
  coverer-side project ids (original UUIDs are provenance only, never cross-mailbox
  filters). Endpoints `POST /api/handoff/{original}/return-draft` +
  `GET .../return-context`; generation reuses `generate_candidate`. **Part 2 shipped:**
  creator "Create a return handoff" entry + return-mode framing (`HandoffReview.tsx`,
  `ReturnHandoff.tsx`), recipient "what changed while you were away" framing
  (`RecipientPackage.tsx`, via a safe `package_type` DTO field), and a two-mailbox demo
  seed (`scripts/seed_handoff_demo.py`: Dana + `coverer-demo`) + runbook. Recipient
  routes stay snapshot-only; original package untouched by a return publish.
- **S33 ✓ implemented by S34 (originating spec).** Return Handoff / Coverage Delta
  (`docs/s33-return-handoff-coverage-delta-plan.md`, D15): a reciprocal package
  created by the coverer after coverage ends, sourced from the coverer's mailbox
  and automatically seeded from the original package's projects / coverage areas,
  without treating it as a `new-version` or opening a mailbox backdoor. Shipped in
  S34 (see above).
- **S35 ✓ implemented.** Cover-for-me answer quality + rich demo seed: intent-aware
  L1 answers (blocked / next-steps / status / what-changed) using the user's real
  query, softened no-embeddings warning, collapsed evidence + temporal next-step
  grouping, and a richer deterministic mailbox (`scripts/seed_rich_handoff_demo.py`).
  Citation allow-list + sensitivity/noise gates unchanged.
- **S36 ✓ implemented.** Relationship Map readability: frontend-only org/domain
  grouping, large external orgs collapsed by default, progressive disclosure
  (`frontend/src/utils/relationshipGraph.ts`). No derivation/data/DTO change; edge
  evidence stays labeled as volume, not importance.
- **S37 ✓ implemented.** Creator Handoff review ergonomics: creator review grouped by
  **real project identity** (`claim.project_id` resolved to labels from the creator's
  own project list) with within-group filtering + collapsed evidence
  (`frontend/src/utils/handoffGroups.ts`). Frontend/client only; recipient untouched.
- **S38 ✓ implemented.** Frontend copy + landing polish: reduced em dashes in
  user-facing copy; single-column landing section headers. Docs/copy/CSS only.
- **S39 ✓ implemented.** Recipient project grouping via **snapshot coverage labels**:
  **migration 0014** (`0014_handoff_claim_project_label`) adds nullable
  `handoff_claim.project_label`, frozen at generate from the creator/coverer-owned
  mailbox's project table; recipient DTO surfaces it and the recipient rail groups by
  the frozen label (`frontend/src/utils/recipientGroups.ts`), with the `coverageAreas`
  clustering as the pre-S39 fallback. **Recipient stays snapshot-only** (label read from
  the snapshot, never resolved live); admin DTOs unchanged. **Alembic head is now
  `0014_handoff_claim_project_label`.**
- **S40 ✓ implemented.** Recipient package-local **Ask intent shaping**
  (`services/handoff/ask.py`): deterministic, LLM-free answers shaped by intent —
  status / next steps / blocked / decisions — using the S39 frozen `project_label` as a
  searchable + scoping signal, evidence collapsed under each answer item. Snapshot-only
  (only `handoff_*` rows; no Project/Event/Message/L0/L1/L2/retrieval/live mailbox);
  oracle-safe neutral no-answer preserved; no schema/DTO change.
- **S41 ✓ implemented (docs/QA only).** Rich demo final polish + runbook:
  `docs/handoff-demo-quickstart.md` is the canonical investor-demo path (three-mailbox
  separation, ordered rich-demo talk track, fresh-package warning, deferred S40 final-QA
  checklist, return-handoff clarification); light pointer + return caution added to
  `docs/s17-handoff-manual-demo-runbook.md`. No code/schema/migration/dependency change.
- **S42 - docs-status cleanup + quality-first roadmap (docs-only).** Synced the status
  docs to record S34-S41 and the current Alembic head, clarified S28 (implemented by
  S29/S30) and S33 (implemented by S34), and added
  `docs/product-roadmap-quality-first.md`: the quality-first roadmap - prove packages
  are accurate/safe/usable/governable/pilot-ready before more intelligence or broad
  integrations; calendar is the first likely connector after quality/safety/pilot
  readiness; Slack/Teams only after pilot evidence.
- **S43 ✓ implemented.** Offline, deterministic handoff quality evaluation harness
  (`services/handoff/eval/`, `scripts/eval_handoff_quality.py`, `fixtures/handoff_eval/`):
  seeds a throwaway mailbox, runs the real `generate_candidate`, and scores it against a
  small synthetic gold corpus - hard gates (every claim cited, citations in evidence,
  excluded material absent) + quality signals. Requires local Postgres; no external API;
  no product behavior change. Known limitations recorded as candidate work:
  blocker-kind extraction and stale/conflict detection are not implemented.
- **S44 ✓ implemented.** Pre-publish privacy/safety review gates
  (`services/handoff/safety.py`): deterministic, creator-side findings over a generated
  package's own snapshot (credential/secret, payment, personal/SSN/medical, hr_legal,
  security, stale/conflict, blocker, low-confidence) on the creator DTO as safe metadata
  only (category/severity/explanation/ref - never the matched text). HIGH findings block
  publish (422) until the flagged content is removed and regenerated, or acknowledged
  with a required 1-500 char reason; the override audits
  `package_published_with_safety_override` with safe metadata only (`reason_provided` +
  `reason_length`, high count, categories, version) - never the raw reason. Recipient +
  admin DTOs unchanged; recipient stays snapshot-only. No migration, no ekc_schemas
  change, no dependency change. **Alembic head stays `0014_handoff_claim_project_label`.**
- **S45 - creator guided handoff wizard spec (docs/spec-only).**
  `docs/s45-creator-guided-handoff-wizard-plan.md`: the frontend-led, wizard-first
  reframing of the creator flow (create -> scope -> generate/review/prune -> S44 safety
  -> publish), reusing existing endpoints, S37 grouping, and the S44 findings/gate. No
  code change in S45; implemented by S46.
- **S46 ✓ implemented.** Creator Guided Handoff Wizard (frontend-only;
  `frontend/src/components/handoff/HandoffWizard.tsx`), mounted as the PRIMARY creator
  Handoff surface (`Workspace.tsx`). Wizard-first Start -> Scope -> Review -> Safety ->
  Publish over the EXISTING creator endpoints (create/scope/generate/prune via
  excluded-header + regenerate/restore/publish); the detailed `HandoffReview` is
  preserved as an "Advanced / full evidence review" mode (additive `export`s only, no
  behavior change). Empty scope DISABLES Generate (no whole-mailbox default); guided
  scope is date + project selection plus seeded person/thread scope editable as
  REMOVABLE chips, with add-new person/thread selection deferred to Advanced/future. The
  S44 safety step is REQUIRED and high-severity findings cannot be skipped (prune/
  regenerate them away, or acknowledge every current high finding id with a reason via
  the existing `safety_ack` contract). The `return_delta` blank-recipient path is
  preserved (recipient field not auto-filled; "leave blank to send back to <original
  creator>"; publishes `recipient_email: ""` so the server default is exercised).
  No backend/schema/migration/dependency/ekc_schemas/recipient/admin change; recipient
  stays snapshot-only. **Alembic head stays `0014_handoff_claim_project_label`.**
- **S47 (docs/spec-only).** Coverage-contract-per-project SPEC
  (`docs/s47-project-coverage-contract-plan.md`): a per-project contract over the
  frozen package (covers/decisions/open-loops/blockers/people/evidence/boundary),
  computed-only MVP recommendation. No code; implemented by S48.
- **S48 ✓ implemented.** Per-project coverage contract MVP, computed-only
  (`services/handoff/coverage_contract.py`): a pure, DB-free assembler that builds a
  per-project contract from a package's FROZEN `handoff_claim` + `handoff_evidence`
  rows, grouped by the S39 frozen `project_label` (with unassigned / other-evidence
  fallbacks). Additive `coverage_contract` block on BOTH the creator
  (`HandoffPackageOut`) and recipient (`RecipientPackageOut`) DTOs - identical,
  recipient-safe shape (no exclusion counts / hidden-content categories; the creator's
  exclusion posture stays in the separate `exclusion_counts`). Citation-backed by
  construction; recipient stays package-local snapshot-only (contract survives wiping
  live Project/Event/Message/Thread rows). Recipient card renders selected-area items
  (Settled / Open / Blockers / optional People, evidence collapsed); creator wizard
  shows a contract overview whose project names + per-kind count chips filter the
  review list. Three additive S43 hard gates (items-cited / evidence-reconciles /
  excluded-absent). `coverage_contract_confirmed` audit is explicitly DEFERRED.
  No migration, no ekc_schemas change, no dependency, no admin change. **Alembic head
  stays `0014_handoff_claim_project_label`.**
- **S49 - calendar-first handoff context (docs/spec-only).**
  `docs/s49-calendar-first-handoff-context-plan.md`: plans calendar as the first
  connector (meetings/deadlines/coverage-window context) - read-only least-privilege
  Google Calendar OAuth (reuse S23 vault), a `calendar_sync_window` job on the S24
  runner, service-DB live tables + a package-local `handoff_calendar_item` snapshot,
  S48 coverage-contract meeting/deadline view, S40 time-aware Ask, and S43/S44
  calendar eval/safety gates. Hard boundaries: NO surveillance/productivity scoring,
  NO recipient live-calendar access (snapshot-only), NO Slack/Teams/Jira, read-only
  scopes, creator-reviews-before-publish. Proposed sub-sprints S50 (OAuth/connect) ->
  S51 (sync job + live layer) -> S52 (wizard/package integration) -> S53 (recipient
  Ask/eval). **Not implemented; no code/schema/migration/dependency change.**
- **S50 ✓ implemented.** Google Calendar OAuth/connect foundation ONLY (the
  OAuth/vault/account boundary; NO event fetch, NO calendar_event/
  handoff_calendar_item tables). Reuses the S23 flow as a DISTINCT provider
  `google_calendar`: **migration 0015** widens the `mailbox_provider_account`
  provider CHECK to `gmail + google_calendar` (service-DB only; **Alembic head is now
  `0015_calendar_provider`**; no ekc_schemas/SCHEMA_VERSION change). New owner/tenant-
  guarded router `services/api/routers/oauth_calendar.py`:
  `POST /api/mailbox/{id}/calendar/connect/start`, `GET /api/oauth/calendar/callback`,
  `GET /api/mailbox/{id}/calendar/status`, `POST /api/mailbox/{id}/calendar/disconnect`.
  Scope is EXACTLY `https://www.googleapis.com/auth/calendar.events.readonly` (never
  the broad `calendar.readonly`). The shared `services/oauth/flow.py` was generalized
  with a `provider` param (Gmail behavior byte-identical by default); the callback
  derives the provider from the single-use state row; vault_ref is provider-prefixed;
  disconnect reuses the S30 fail-closed semantics (vault unavailable / revoke failure
  -> 503, no DB mutation; already-disconnected -> idempotent 200). App DB stores only
  `vault_ref` + safe provider metadata; status exposes safe metadata only. Recipient
  routes untouched (snapshot-only). No frontend change.
- **S51 ✓ implemented.** Google Calendar sync job + live layer ONLY (S49 sub-sprint
  2; NO recipient/package exposure, NO `handoff_calendar_item` snapshot - that is
  S52). **Migration 0016** (`0016_calendar_events`) adds service-DB `calendar_event`
  + `calendar_event_attendee` (creator-owned; **Alembic head is now
  `0016_calendar_events`**; no ekc_schemas/SCHEMA_VERSION change). New
  `services/calendar/` (deterministic `normalize.py` + a read-only `gcal_client.py`
  events.list seam), a vault-backed `resolve_calendar_grant` (S23/S50 vault; fail
  closed if not connected), a `calendar_sync_window` handler on the S24 runner, and
  an owner/tenant-guarded `POST /api/mailbox/{id}/calendar/sync` (fail-fast 409 with
  no connected calendar; windowed idempotency key). Stores ONLY the docs/s49
  section-3 allow-list (title, start/end, all-day, organizer display+domain, is_recurring +
  human recurrence summary - never raw RRULE, has_conferencing bool, attendee
  display+domain - never attendee email or response status). **Private-visibility
  events are excluded at ingest entirely** (no title/attendee persisted). Uses ONLY
  the events.readonly token minted from the vault; job params/progress/summary are
  safe counts only; kill switch `EKC_CALENDAR_SYNC_DISABLED=1`. Gmail OAuth/ingest
  unchanged; recipient routes untouched and cannot read the live calendar tables.
  No frontend change.

Current status: **S0–S16.0 complete; S17.2–S17.20 (handoff package MVP, incl. the
deterministic LLM-free recipient package-local ask, new-version re-share /
supersede, static HTML export, a compact three-part recipient coverage workspace,
creator-only empty-generation diagnostic, refresh-safe workspace mailbox, a
deterministic handoff-demo seed script, a manual-demo runbook, and the creator
Handoff workspace layout with a sticky action rail — Publish reachable without
scrolling — S17.20) shipped and
end-to-end validated. The current demo path is the shipped S17 handoff-demo seed +
runbook (handoff-demo mailbox for Handoff generation; puluo for Cover-for-me /
Relationship Map / Network Map); the older S16 canonical-demo readiness is
superseded in practice for that and remains optional. S18–S21 are each
**shipped as a docs/spec-only plan, not implemented in code**: S18 maps hosted
web-app deployment readiness, S19 specifies the production auth + tenant boundary,
S20 specifies safe Gmail OAuth connect + a token-vault boundary, and S21 specifies
the production background-job system (authoritative implementation order in S21
§14: S22 auth → S23 OAuth/vault → S24 jobs → S25 ingest → S26 enrichment → S27
deploy) — see
`docs/s18-hosted-product-readiness-plan.md`,
`docs/s19-auth-tenant-boundary-plan.md`,
`docs/s20-oauth-token-vault-plan.md`, and
`docs/s21-background-job-orchestration-plan.md`.** Manual demo:
`docs/s17-handoff-manual-demo-runbook.md`.
S7 L2 hybrid retrieval (Voyage AI voyage-4,
pgvector HNSW, cover-for-me upgrade) shipped and live-validated. S8 real-mailbox
demo readiness, S9 project-clustering materialization, and S10 local runtime
reliability are all complete. S10 switched the Voyage embedding runtime from the
`voyageai` SDK to direct REST over `httpx` — see `docs/decisions.md` D12b S10
status note. Optional S7.12 hosted Voyage reranker remains off by default. S11
shipped the inspectable citation evidence drawer + deduped citations; S12 the
product shell, client router, and marketing landing; S13 the graph-backed
Relationship Map tree (`services/relationships/`, `/api/relationship-map`) with
the Network Map preserved. S14 shipped evidence/source-navigation trust polish:
safe source-message detail, richer citation drawers, best-effort Gmail search,
and structural relationship provenance notes — see
`docs/s14-implementation-plan.md`. S15 fixed the S9 DB-test contamination and
added `docs/s15-verification-matrix.md` as the canonical guide for local,
DB-gated, demo-mailbox, and live-integration green states. S16.0 adds
customizable date-window Gmail ingest for scoped snapshots. D13 locks the
purpose-built canonical demo fixture; D14 locks the next MVP direction:
employee-initiated audited handoff packages. Do not frame future work as
generic mailbox search or employee monitoring. The covered employee scopes and
reviews the package; the recipient gets package-scoped cited evidence; managers
approve/govern; HR/legal/IT define policy.
