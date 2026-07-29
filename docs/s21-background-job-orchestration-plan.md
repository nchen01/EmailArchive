# S21 — Background Job Orchestration (spec)

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes. This document defines the production job system that runs long or
> retryable work **outside** the request/response path, while preserving tenant
> isolation (S19), mailbox ownership + token-vault boundaries (S20), audit
> safety, and the recipient package **snapshot-only** invariant.

**Source docs (authoritative, in precedence order):** `docs/decisions.md`, then
`docs/s18-hosted-product-readiness-plan.md` (§5 architecture, §6 job states),
`docs/s19-auth-tenant-boundary-plan.md` (owner/tenant authz, `AUTH_MODE`),
`docs/s20-oauth-token-vault-plan.md` (vault-backed token resolver),
`docs/s16-date-range-ingest-plan.md` (preview → dry-run → confirm; scoped-snapshot
sync-token bypass), `docs/s17-handoff-package-mvp-plan.md`, and `README.md` /
`AGENTS.md` for shipped status.

**Status of the arc:**
- **S17.2–S17.20 — shipped** (audited Handoff Package MVP; behavior unchanged here).
- **S18 / S19 / S20 — shipped as docs-only specs** (hosted readiness; auth+tenant
  boundary; OAuth+token vault). None are implemented as code.
- **S21 — spec.** The **job infrastructure** (§2, §3, §8, §11) is **implemented by
  S24** (`services/jobs/`, migration `0012_job_infra`, `services/api/routers/jobs.py`):
  the tenant-scoped `job` table + the six states, enqueue/status/list/cancel APIs
  (S22 owner/tenant-guarded), a Postgres `FOR UPDATE SKIP LOCKED` worker claim with
  lease/heartbeat + expired-lease reclaim, idempotency dedupe, safe-metadata
  sanitization, and a harmless `noop` job for validation. **Still deferred:**
  moving Gmail ingest / L1 enrichment / event extraction / embedding backfill /
  project materialization onto jobs (§4) — those remain manual scripts for now.
- **S22 (auth), S23 (OAuth/vault), S24 (job infra) — implemented.**

**Untouched invariant:** the recipient package view reads **only package-local
snapshot rows** (S18 §7, S19 §5). Jobs are a **creator/operator-side** mechanism;
a recipient session never reads job rows, job logs, or progress, and jobs never
widen what a recipient can see (§13).

---

## 1. Why jobs are needed

These tasks are too long, too rate-limited, too costly, or too failure-prone to
run inside an HTTP request (which must return in seconds, cannot survive a
deploy/restart, and holds no durable retry state):

- **Gmail ingest** (a date window can be thousands of messages; provider-rate-
  limited; must resume after failure).
- **L1 enrichment** (people/relationships/projects/roles over the whole mailbox).
- **LLM event extraction** (Anthropic calls — latency + cost + rate limits).
- **Embedding backfill** (Voyage `voyage-4` — cost-bearing, rate-limited, long).
- **Project materialization** (clustering over the mailbox).
- **Real-mailbox smoke / eval runs** (long, operator-triggered).
- **Handoff package generation** — synchronous + deterministic today (stays
  request-time); becomes a job **only if it grows heavy** (e.g. optional LLM
  synthesis, large scope).
- **Future export generation** (PDF/DOCX/ZIP) — HTML export is cheap and stays
  inline; heavier formats become jobs producing a stored artifact.
- **Cleanup / retention jobs** (scheduled deletion per policy — inherently async).

Running any of these in a request risks timeouts, partial writes with no
resumption, lost work on deploy, and no safe place to record retry/error state.

---

## 2. Job model (proposed — not implemented)

A single `job` record (service DB only, **not** `ekc_schemas` — same rationale as
S20 §4: internal orchestration, no shared cross-service contract):

```
job
  id                  uuid  pk
  tenant_id           uuid  fk -> Tenant            (S19; every job is tenant-scoped)
  requested_by_user_id uuid fk -> User              (who enqueued; null for scheduled/system)
  mailbox_id          uuid  fk -> Mailbox  null      (null for tenant-wide / cleanup jobs)
  job_type            enum(...)                       (§4)
  status              enum(queued, running, succeeded, failed, canceled, partially_succeeded)
  params              jsonb                           (SAFE inputs only — never tokens/content)
  idempotency_key     text unique                     (§8)
  progress            jsonb                           (safe counters/phase — §9)
  summary             text  null                      (safe human summary; no content)
  error_category      text  null                      (category enum, never a raw message/trace)
  attempt             int   default 0
  max_attempts        int
  next_retry_at       timestamptz null
  cancel_requested_at timestamptz null
  lease_expires_at    timestamptz null                (worker lease/heartbeat — §11)
  created_at          timestamptz
  started_at          timestamptz null
  finished_at         timestamptz null
```

**Hard rule:** `params`, `progress`, `summary`, and `error_category` carry **safe
metadata only** — never OAuth tokens/codes, raw provider responses, email
subjects/bodies/snippets, LLM prompts/responses, capability/session tokens, or
stack traces (§10, §13).

---

## 3. Job states

States (matching S18 §6): `queued`, `running`, `succeeded`, `failed`, `canceled`,
`partially_succeeded`.

Allowed transitions:

```
queued    → running                (worker leases the job)
queued    → canceled               (canceled before it starts)
running   → succeeded              (all units done, no failures)     [terminal]
running   → partially_succeeded    (some units done, some failed;
                                     terminal unless a retry targets
                                     only the failed remainder)      [terminal*]
running   → failed                 (fatal / attempts exhausted)      [terminal]
running   → canceled               (cancel_requested honored at a
                                     safe checkpoint)                 [terminal]
running   → queued                 (retryable failure → re-enqueue,
                                     attempt++ , next_retry_at set)
```

**Terminal:** `succeeded`, `failed`, `canceled`, and `partially_succeeded`
(marked `*` — a follow-up retry is a *new* job targeting the unfinished remainder
via the same idempotency lineage, not a re-open of the terminal row). `queued`
and `running` are non-terminal. A terminal job is immutable except for retention
cleanup. Illegal transitions are rejected (fail closed).

---

## 4. Job types (proposed)

Common contract: **authorization** is checked at enqueue *and* re-checked in the
worker (§7); **tokens** resolve via the vault at execution (§6); **progress** and
**audit** are safe-metadata-only (§9/§10).

| Type | Inputs (safe params) | Authz | Idempotency | Retry | Progress | Key failure modes |
|---|---|---|---|---|---|---|
| `gmail_ingest_window` | mailbox_id, date_from, date_to, mode(preview/dryrun/confirm), replace_snapshot(bool) | `owner(mailbox)` (S19) | key over {tenant,mailbox,type,window,replace}; dedupe active | resumable by message cursor; L0 upsert by `message_id_header` | messages fetched/normalized/persisted counts, phase | provider rate limit, token revoked (§6), partial page failure → `partially_succeeded` |
| `l1_enrichment` | mailbox_id, (optional scope) | `owner(mailbox)` | key over {tenant,mailbox,type,inputs-hash} | rebuild is idempotent (deterministic over L0) | entities processed/total | transient DB error (retry), bad record (skip → partial) |
| `event_extraction` | mailbox_id, (message range) | `owner(mailbox)` | key over {tenant,mailbox,type,range}; skip already-extracted | per-message; skip done; backoff on Anthropic 429 | messages extracted/total | Anthropic rate/cost, provider error category |
| `embedding_backfill` | mailbox_id, (message range) | `owner(mailbox)` + **cost gate** (§12) | skip already-embedded rows | resumable; skip embedded | vectors written/total | Voyage rate/cost/auth; **never auto-run without authorization** |
| `project_materialization` | mailbox_id | `owner(mailbox)` | idempotent rebuild (replaces prior materialization transactionally) | full retry safe | clusters/projects built | clustering error (retry), empty input (succeed w/ 0) |
| `handoff_generation` *(only if heavy later)* | package_id | `owner(package.mailbox)` + `mutable` | key over {package_id, scope-hash}; supersede prior candidate | full retry safe (regenerate) | claims/evidence built | LLM error (if synthesis added); today runs inline |
| `export_generation` *(future PDF/DOCX/ZIP)* | package_id, format | `owner(package.mailbox)` + exports-enabled (S18 §10) | key over {package_id, format, version} | full retry safe (artifact regenerated) | artifact bytes/phase | render error; artifact stored in blob store (D6 later) |
| `cleanup_retention` | tenant_id, policy params | **operator / policy** (not a creator) | key over {tenant, policy, window}; safe to re-run | idempotent (delete-if-present) | rows purged/total | policy misconfig → refuse (fail closed) |

`handoff_generation` and `export_generation` (HTML) stay **request-time today**;
they are listed so the model already has a slot when they grow heavy.

---

## 5. Date-range ingest integration (S16.0)

Builds on the shipped preview → dry-run → confirm flow:

- **Confirm starts a `gmail_ingest_window` job.** The live ingest is the async unit.
- **Preview / plan stays request-time** while it is cheap (provider listing /
  estimate, no fetch, no persist — shipped behavior). If preview ever needs a real
  fetch at scale, it becomes a `mode=preview` job; the API shape stays the same.
- **`replace_snapshot` remains explicit and dangerous.** All shipped safeguards
  are preserved: it requires an explicit date bound + confirmation, and as a job
  it performs a **staged, transactional swap** (build the new snapshot, then swap)
  so a crash mid-run never leaves the mailbox with partial/lost data (§8).
- **Scoped snapshot runs do not save sync tokens** (shipped S16.0 behavior);
  moving ingest into a job does **not** change that. Any incremental-sync-token
  behavior is a later spec.

---

## 6. OAuth / token-vault integration (S20)

- **Jobs never receive raw tokens in their payload.** `params` carries a
  `mailbox_id` (→ its `mailbox_provider_account`), never a token or `vault_ref`
  value in a form that leaks.
- **Credentials resolve through the vault at execution time** via S20's
  vault-backed resolver; the **worker** obtains a short-lived access token and
  **refreshes inside the worker** — never the frontend, never the enqueue path.
- **Token values never appear** in job rows, `params`, `progress`, `summary`,
  logs, audit metadata, API responses, or error messages (S20 §3, D6).
- **Provider revocation / refresh failure** becomes a **safe job failure
  category** (e.g. `error_category = provider_auth_revoked`), stops the job,
  marks the connection per S20 §8, and surfaces a reconnect prompt — with no token
  detail anywhere.

---

## 7. Auth / tenant integration (S19)

- **Enqueue requires the same authorization the synchronous action would.**
  Creating a `gmail_ingest_window` needs `owner(mailbox)`; a `cleanup_retention`
  needs operator/policy authority. No job can be enqueued that its requester could
  not perform synchronously.
- **Every job row is tenant-scoped** (`tenant_id`), and **workers re-enforce the
  boundary at execution** — a worker re-checks `owner(mailbox)` / tenant match and
  does not trust the enqueue path alone (defense in depth; an enqueue bug must not
  become a cross-tenant execution).
- **Cross-tenant job/resource lookup returns 404** (not 403), consistent with
  S19 §4 (no cross-tenant existence oracle).
- **Admin** may **view job metadata** and **request cancellation** within their
  tenant (governance, S19 §6) — **metadata only**; admins cannot read job content,
  tokens, or mailbox data, and cannot enqueue content-producing jobs on a user's
  behalf unless a future policy explicitly allows it.
- **`AUTH_MODE=dev`** keeps the local/manual-script path working (jobs may run
  inline / synchronously in dev); production requires the real worker + authz.

---

## 8. Idempotency and resumability

- **Idempotency key** = a stable hash over `{tenant_id, mailbox_id, job_type,
  normalized_params}` (for ingest, the normalized date window + `replace_snapshot`
  flag; for backfill, the range). Enqueuing a job whose key matches a **non-
  terminal** job returns that job instead of creating a duplicate — so repeated
  clicks / double-submits never spawn parallel destructive runs.
- **Restartable after worker crash:** a lease (`lease_expires_at` + heartbeat,
  §11) lets a crashed job's lease expire and be re-leased; execution resumes from
  the last **checkpoint** (e.g. message cursor, last-embedded id) rather than
  restarting from zero.
- **Partial success** is first-class (`partially_succeeded` + `progress` counts of
  done/failed/total), so the UI can say "most of your mailbox is ready" honestly
  instead of all-or-nothing (S18 §6).
- **Retry avoids duplication by construction:** L0 messages upsert by
  `message_id_header`; event extraction and embedding skip rows already done;
  project materialization is an idempotent full rebuild. So a retried unit
  re-does only genuinely missing work.
- **Replacement-snapshot jobs avoid data loss** via the staged transactional swap
  (§5): the existing snapshot stays readable until the new one is built and
  swapped atomically; a failure discards the half-built new snapshot, never the
  old one.

---

## 9. Progress and UX (eventual frontend)

The frontend (built later, rendered by S23-era admin/status surfaces) should show,
from **safe** fields only:

- **Status**: queued / running / succeeded / failed / canceled / partially_succeeded.
- **Progress counts** where safe (e.g. "1,240 / 3,000 messages ingested").
- **Current phase** (e.g. "fetching", "normalizing", "embedding").
- **Safe error category + next action** (e.g. "Gmail access was revoked —
  reconnect your mailbox"), never a raw provider/LLM error dump.
- **Never**: OAuth tokens / provider secrets, raw provider or LLM responses,
  email subjects/bodies/snippets, capability/session tokens, or stack traces.

The dev/manual-script path keeps its current console output locally; the safe-
metadata rule governs the **hosted** surfaces.

---

## 10. Audit events (safe metadata only)

| Event | When |
|---|---|
| `job.created` | Enqueued |
| `job.started` | Worker leases + begins |
| `job.progress` | Milestone progress (throttled; counts/phase only) |
| `job.succeeded` | Terminal success |
| `job.failed` | Terminal failure (attempts exhausted / fatal) |
| `job.canceled` | Canceled (by requester/admin/system) |
| `job.retry_scheduled` | Retryable failure → re-enqueued (`next_retry_at`) |
| `job.partial_success` | Terminal `partially_succeeded` |
| `job.permission_denied` | Enqueue or worker authz check failed |

**Safe fields only:** job id, tenant_id, requested_by_user_id, mailbox_id,
job_type, phase, status, counts, error **category**, timestamps. **Never**: email
body/subject/snippet, raw provider response, OAuth token / auth code / refresh /
access token, stack trace, LLM prompt or response, or capability/session token
(S18 §8, S20 §10, D6). Extends the S19 §9 / S20 §10 audit catalogs.

---

## 11. Worker architecture (recommended)

- **API process enqueues; a separate worker process executes.** The API never
  runs job bodies (S18 §5: stateless API, no long work in requests).
- **Managed Postgres is the source of truth** for job state (already our DB).
- **Queue: Postgres-backed to start** (a `job` table polled with
  `SELECT … FOR UPDATE SKIP LOCKED`) — the **simplest production-friendly default**
  given we already run managed Postgres and need transactional state + idempotency
  in one place. An external broker (Redis / SQS / etc.) is a later swap behind the
  same enqueue interface if throughput demands it. *(Recommended default, to
  confirm at build time.)*
- **Leases + heartbeat:** a worker claims a job with a `lease_expires_at`; it
  renews the lease via heartbeat while running. **Stuck-job recovery:** an expired
  lease (crashed/hung worker) makes the job eligible for re-lease and resumption
  from its last checkpoint (§8).
- **Graceful shutdown:** on deploy/restart, workers stop claiming new jobs, finish
  or checkpoint in-flight work, release leases, and exit; unfinished jobs return
  to `queued`.
- **Concurrency:** bounded worker pool with **per-tenant / per-mailbox / per-
  provider** limits (§12) so one tenant cannot starve others.

---

## 12. Rate limits and cost controls

- **Gmail API limits:** respect per-user/project quotas; exponential backoff on
  429/5xx; a `gmail_ingest_window` never runs unbounded — it pages with backoff
  and honors provider rate headers.
- **Voyage embedding (cost-bearing):** `embedding_backfill` is **cost-gated** — it
  must not auto-run without explicit owner/operator authorization, consistent with
  the repo's standing rule that the Voyage key is used only on explicit
  instruction. A per-tenant budget/limit + a kill switch bound spend.
- **Anthropic event extraction (cost):** batched, rate-limited, backoff on 429; a
  per-tenant cap; failures degrade to `partially_succeeded`, not a spend spiral.
- **Per-tenant concurrency** and **per-mailbox job limits** (e.g. one active
  ingest per mailbox) prevent runaway fan-out and duplicate destructive runs (§8).
- **Backoff/retry:** capped exponential backoff with `max_attempts`; terminal
  `failed` after exhaustion (never infinite retry).
- **Kill switch / cancellation:** `cancel_requested_at` is honored at the next
  safe checkpoint → `canceled`; an operator kill switch can drain/stop a job type
  or a tenant's jobs (e.g. to halt cost immediately).

---

## 13. Privacy and compliance

- **Recipient package views remain snapshot-only and never read jobs.** Recipients
  have no visibility into job rows, progress, logs, or audit; jobs never widen
  recipient-visible data (S18 §7, S19 §5).
- **Jobs never expose sensitive/noise-excluded content** in logs, progress,
  summaries, or audit — exclusion happens during generation, and job telemetry is
  counts/phase/category only, never content. A job cannot become a back-channel
  that reveals excluded material or its "how much / about what".
- **No productivity / performance scoring** — jobs process mailboxes for
  continuity, never to score a person; job metadata must not be repurposed as a
  monitoring signal.
- **Retention / cleanup is policy-driven** (`cleanup_retention`, S24) — deletion
  runs from an explicit policy, not ad hoc, and is itself audited (safe metadata).
- **Job metadata must not become a content side channel:** counts and phases are
  allowed; anything that could reconstruct content, sender identities beyond safe
  domain-level metadata, or excluded topics is not.

---

## 14. Proposed S22+ implementation map (not started)

The prior S18 §11 sprint map was a rough planning order; **this is the concrete
implementation sequencing** and supersedes it. Each is an *implementation* sprint
turning a shipped spec (S19/S20/S21) into code — none are started.

- **S22 — Auth + tenant minimal vertical slice** (implement S19): tenant/user
  model, mailbox ownership binding, the `owner(mailbox)` dependency on every
  creator endpoint, fail-closed `AUTH_MODE`, auth audit events.
- **S23 — Gmail OAuth + token vault minimal vertical slice** (implement S20):
  connect flow, vault-backed token resolver, `mailbox_provider_account`, mismatch
  handling, OAuth audit events.
- **S24 — Background job infrastructure** (implement S21): `job` table,
  Postgres-backed queue + worker, leases/heartbeat, states, idempotency, audit.
- **S25 — Move Gmail date-range ingest into jobs** (`gmail_ingest_window`,
  preserving S16.0 preview/confirm + `replace_snapshot` safeguards).
- **S26 — Move enrichment / embedding backfill / project materialization into
  jobs** (`l1_enrichment`, `event_extraction`, `embedding_backfill`,
  `project_materialization`), with the cost controls of §12.
- **S27 — Hosted deployment / runbook** (S18 §5): managed DB migrations, secrets
  manager (OAuth client secret + token vault/KMS), worker deploy, monitoring,
  backups.

*(Admin/audit viewer and security/privacy hardening from the earlier S18 map
remain planned and slot in after these; renumbering is deferred to whoever opens
those sprints.)*

---

## Acceptance (this sprint)

- Docs/spec-only; **no** backend/frontend/schema/migration/dependency changes.
- New `docs/s21-background-job-orchestration-plan.md`.
- README / AGENTS / CLAUDE / implementation-plan updated with **pointer/status
  lines only**.
- `git diff --check` clean.
- **S21 is clearly not implemented** (every object/state/type is proposed).
- **S17.2–S17.20 shipped**; **S18 / S19 / S20 are docs-only specs**; **S22+
  implementation not started** — stated in the status block.
- **Recipient package snapshot-only invariant untouched** (§13; jobs are
  creator/operator-side and never read by recipient sessions).
- Specific enough that S24 (the job-infra build) can proceed from §2, §3, §8, §11.
