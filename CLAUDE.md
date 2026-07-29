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
- **S28 planned — docs/spec-only, not implemented.** Admin / Audit Viewer + Operations
  Console (`docs/s28-admin-audit-ops-plan.md`): governance + ops visibility over **safe
  metadata only** (package lifecycle, provider-connection, jobs, audit trails, aggregate
  exclusion counts, readiness) + two audited admin actions (revoke package, disconnect
  provider account, each with a mandatory reason). Hard rule: **no admin route is a
  content backdoor** — no mailbox/evidence bodies, excluded content, Gmail/source links,
  OAuth tokens/codes/vault refs, provider responses, raw job params, or tracebacks;
  admin package access is metadata-only; recipients stay package-local snapshot-only.
  Implementation is S29 (read-only viewer) then S30 (actions); no migration for the
  read-only viewer.

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
