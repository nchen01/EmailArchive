# S27 — Hosted Deployment Readiness (spec)

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes are made in *this* document. It defines a **safety-gated hosted
> deployment-readiness slice** — a production runbook plus fail-closed startup
> guardrails and readiness checks — that a later implementation sprint builds. It
> is deliberately **not** a broad hosted migration: core product behavior is
> unchanged unless a guardrail is missing. The goal is to make it **hard to
> deploy** with dev vault/auth settings, a missing worker, missing migrations, raw
> OAuth callback logging, or a recipient-access regression.

**Source docs (authoritative, in precedence order):** `docs/decisions.md` (D6
"OAuth tokens never touch the app DB or logs"; D14 handoff direction), then
`docs/s18-hosted-product-readiness-plan.md` (§5 architecture, §6 job states, §7
data boundaries, §9 gap checklist), `docs/s19-auth-tenant-boundary-plan.md`
(`AUTH_MODE`, owner/tenant binding, §7 dev-mode-off-in-prod), `docs/s20-oauth-token-vault-plan.md`
(§3 vault boundary, §5 mismatch, §11 threat model), `docs/s21-background-job-orchestration-plan.md`
(§11 worker, §12 rate/cost gates, §13 privacy), and `README.md` / `AGENTS.md` for
shipped status. Where they disagree, that order wins.

**Status of the arc:**
- **S17.2–S17.20 — shipped** (audited Handoff Package MVP; behavior unchanged here).
- **S18 / S19 / S20 / S21 — shipped as docs-only specs.**
- **S22 (auth), S23 (Gmail OAuth + dev token vault), S24 (job infra), S25 (date-range
  ingest → jobs), S26 (enrichment / event extraction / embedding backfill / project
  materialization → jobs) — implemented.**
- **S27 — this spec.** Hosted deployment readiness: runbook + environment
  validation + fail-closed startup guardrails + readiness/health checks + a hosted
  preflight command. **Not implemented in code by this document** — every module,
  endpoint, and script named below is *proposed for the S27 build*.

**Untouched invariant:** the recipient package view reads **only package-local
snapshot rows** (S18 §7, S19 §5, S20/§S21 restatements). Every guardrail below is
a **creator/operator-side** boundary; none widens recipient-visible data, and one
of the readiness checks exists specifically to *prove* the recipient router never
gained a jobs/pipeline/mailbox path (§4, §5).

---

## 0. Scope & non-goals

**In scope (what the S27 implementation must deliver):**

1. A documented **production process topology** (§1).
2. An **environment-validation module** + **fail-closed startup guardrails** (§2)
   that refuse to boot a hosted process configured with dev auth/vault, a missing
   worker path, an out-of-date DB, missing OAuth config, or missing redaction.
3. A **deployment runbook** with exact ordered phases and a rollback (§3).
4. A **production-readiness checklist** an operator signs off before a deploy (§4).
5. A **hosted preflight command** + **readiness endpoint** additions + a
   **worker-health signal** (§5), so the guardrails are runnable in CI and at
   deploy time, not just prose.
6. A clear **S27-vs-later** split (§6): S27 ships guardrails/runbook/checks only.

**Explicit non-goals (do NOT build in S27):**

- Real hosted **infrastructure provisioning** (Terraform/Helm/cloud accounts,
  managed Postgres creation, CDN, DNS/TLS). S27 documents the topology and the
  checks; it does not stand up infra.
- A production **admin / audit-viewer UI** (that is the later S23-map sprint).
- A full **observability stack** (OTel collector, dashboards, alerting vendor).
  S27 defines *what* must be observable and adds safe health/readiness surfaces;
  it does not integrate a telemetry vendor.
- **Microsoft 365 / Graph** (still the D2 stub).
- A **stronger production auth provider** (real IdP/SSO/session backend) **beyond**
  the shipped S22/S23 foundation — *except* the fail-closed guardrail that a hosted
  process must not run with `AUTH_MODE=dev` or the dev vault. Wiring a concrete IdP
  remains a later sprint; S27 only enforces that production cannot fall back to dev.
- **Recipient account auth**, **manager approval**, **multi-recipient**, and **rich
  recipient relationship/project/owner trees** — all still deferred (D14 scope).
- **Retention/cleanup enforcement** (`cleanup_retention`), **data-deletion jobs**,
  and the external **security review** (later hardening sprint); S27 only *states*
  the backup/restore and retention expectations as checklist items (§4).

---

## 1. Production process topology

Technology-neutral shape (recommended defaults named where obvious), matching S18
§5. Nothing here is provisioned in S27 — this is the target the guardrails and
runbook are written against.

```
   Browser ─────────►  Static frontend        (CDN / static host; the Vite build)
   (creator,               │  HTTPS (JSON, /api/*)
    recipient)             ▼
                     FastAPI API process       (stateless, N replicas; NO job bodies)
                     services.api.main:app         │ enqueue (writes job rows)
                        │  reads/writes            │
                        ▼                          ▼
                  Managed Postgres 16 + pgvector  ◄── Background worker process(es)
                  (app DB: tenants, mailboxes,        scripts.run_worker
                   L0/L1/L2, packages, `job` queue)   (claims via FOR UPDATE SKIP LOCKED)
                        ▲                          │ resolves creds per job
                        │                          ▼
                  Token vault / KMS / secrets manager
                  (OAuth client secret, refresh tokens, VOYAGE/ANTHROPIC keys, DB creds)

   Cross-cutting: structured logs (OAuth callback redaction ON) · health/readiness
   probes · backups (DB + vault, separately). Object/blob store: deferred (raw MIME
   not stored, D6).
```

Process roles (all run the **same codebase**, differentiated by entrypoint + env):

- **Frontend** — the existing React build (`npm.cmd --prefix frontend run build`)
  served as static assets behind a CDN. Needs a build-time **API base URL** and a
  production build that never exposes the dev mailbox-id entry path (S19 §7).
- **API process** — `uvicorn services.api.main:app`, stateless, horizontally
  scalable, **runs no job bodies** (S18 §5). It enqueues jobs and serves reads.
- **Worker process** — `python -m scripts.run_worker` (one or more), the only
  process that executes job bodies and the only one that resolves OAuth creds
  through the vault (S20 §3, S21 §6). Same DB, no external broker (Postgres queue).
- **Managed Postgres + pgvector** — the app DB *and* the job queue live here (S21
  §11: one transactional store for state + idempotency). Migrations applied by an
  operator step, never lazily at request time.
- **Secrets manager / token vault / KMS** — hosts the OAuth client secret, the
  provider refresh tokens (a **production** `TokenVault`, not `DevTokenVault`), and
  the Voyage/Anthropic keys + DB credentials. Injected at runtime; never in the
  image, `.env`, or app DB.
- **Object/blob store** — reserved in the diagram only; **not** used in S27 (raw
  MIME unstored per D6; heavy exports are a later job).

**Recommended defaults to confirm at build time:** managed Postgres+pgvector;
CDN-fronted static frontend; a cloud secrets manager as the production `TokenVault`
backing store; OTel-compatible structured logs. These are defaults, not S27
commitments.

---

## 2. Environment validation & fail-closed startup guardrails

The heart of S27. A single **environment-validation module** produces a list of
typed checks (reusing the shipped `services/preflight.py::PreflightCheck`
dataclass — `name`/`status`/`message`, `status ∈ {pass, fail, warn, info}`). A
**startup guard** runs the *fail-closed subset* when the process detects a hosted
context and **refuses to boot** on any failure.

### 2.1 Hosted-context detection

A process is "hosted / production" for guardrail purposes when **`AUTH_MODE`
resolves to `production`** (the shipped `services/api/auth.py::get_auth_mode()` —
anything other than an explicit `dev` is already `production`). To avoid gating
local dev, the *startup* guard is a no-op under `AUTH_MODE=dev`; the checks are
still runnable on demand via the CLI (§5) in any mode. An explicit
`EKC_DEPLOY_ENV=production` may be required as a redundant, positive signal so a
misconfigured `AUTH_MODE` cannot silently downgrade the gate (open question §8.1).

### 2.2 Checks (each returns pass/fail/warn; fail = refuse to boot in hosted mode)

Grounded in the shipped seams noted in parentheses:

1. **Auth mode is not dev** — `get_auth_mode() == "production"` in a hosted
   context. **FAIL** if a hosted process resolves `dev`. (Mirrors S19 §7 "app
   refuses to boot if hosted + `AUTH_MODE=dev`.") *(seam: `services/api/auth.py`.)*
2. **Production token vault configured** — the process must **not** use
   `DevTokenVault`. `DevTokenVault.__init__` already refuses unless
   `AUTH_MODE=dev` or `EKC_ALLOW_DEV_VAULT=1` (`services/oauth/vault.py::_dev_vault_allowed`).
   The guard adds: in a hosted context, **FAIL** if `EKC_ALLOW_DEV_VAULT=1` is set
   **or** if the process registry (`services/oauth/vault.py::get_vault`) would
   construct a `DevTokenVault`. A production `TokenVault` implementation must be
   registered (`set_vault(...)`) at startup. *(seam: `services/oauth/vault.py`.)*
3. **Dev/test vault refuses production** — assert the negative of (2): constructing
   `DevTokenVault` in a hosted context raises `VaultError` (already true) and the
   guard surfaces it as a boot failure rather than a lazy runtime error on first
   OAuth use. *(seam: `services/oauth/vault.py`.)*
4. **`DATABASE_URL` present and not the dev default** — set, and **not** silently
   the `services/db/engine.py::DEFAULT_DATABASE_URL` localhost dev string in a
   hosted context. **FAIL** if unset or equal to the dev default. *(seam:
   `services/db/engine.py`.)*
5. **DB reachable + migration head current** — reuse
   `services/preflight.py::check_database` and `check_alembic_head`. **FAIL** if
   unreachable or `current_rev != head_rev` (today head is `0012_job_infra`). A
   hosted process must never boot against an un-migrated DB. *(seam:
   `services/preflight.py`.)*
6. **Worker path configured / reachable** — the API must know a worker is expected.
   Minimum: an `EKC_WORKER_REQUIRED=1` (or topology) flag asserting a worker is
   deployed; stronger: a **worker-heartbeat** freshness check (§5.3). **FAIL** (API)
   if a worker is required but no live worker heartbeat is seen within the
   threshold. *(seam: `job` table + `scripts/run_worker.py`.)*
7. **Job queue health** — no jobs stuck `running` with an **expired lease** beyond
   a threshold (a hung/crashed worker signature), and queue depth within a sane
   bound. **WARN** by default (operational, not boot-blocking), escalates to a
   readiness-degraded signal (§5). *(seam: `services/jobs/worker.py` lease fields.)*
8. **OAuth client id/secret/redirect URI configured** —
   `services/oauth/config.py::load_config().configured` is true (id + secret set),
   and `redirect_uri` is **not** the localhost `_DEFAULT_REDIRECT` in a hosted
   context (it must be the hosted-origin HTTPS callback). **FAIL** if unconfigured
   or still localhost. *(seam: `services/oauth/config.py`.)*
9. **OAuth callback access-log redaction installed** — assert the
   `uvicorn.access` logger carries an `AccessLogQueryRedactionFilter`
   (`services/api/log_redaction.py::install_access_log_redaction` is idempotent and
   called at import in `main.py`). **FAIL** if, at startup, the filter is not
   present on the logger — a hosted process must never serve the OAuth callback
   without redaction. *(seam: `services/api/log_redaction.py`, `main.py`.)*
10. **Frontend API base URL configured** — a build-time env (e.g.
    `VITE_API_BASE_URL`) is set for the production frontend build and points at the
    hosted API origin, not a dev proxy. Validated in the frontend build/CI step,
    surfaced in the runbook (§3). **FAIL** the frontend build if unset in a
    production build.
11. **CORS / allowed origins configured** — the API's allowed origins are set to
    the hosted frontend origin(s) and are **not** a wildcard in production. (Today
    `main.py` installs no CORS middleware; if the hosted split serves the frontend
    from a different origin than the API, an explicit allow-list is required.)
    **FAIL** if a hosted context has no explicit allow-list (or uses `*`). *(seam:
    `services/api/main.py`.)*
12. **Recipient routes still snapshot-only** — a static assertion that the
    recipient router (`services/api/routers/handoff_recipient.py`) imports **none**
    of jobs, pipeline, `get_principal`, or `require_owner_*`, mirroring the check
    already enforced in tests. Run at startup as a self-guard and in CI. **FAIL**
    if the recipient router gained a creator/jobs/pipeline import. *(seam:
    recipient router import surface.)*
13. **No raw mailbox-id production bypass** — assert there is no env/flag that
    re-enables the dev synthetic principal or raw mailbox-id loading under
    production (`_resolve_production_principal` returns `None` → 401; the dev
    principal is only reachable when `get_auth_mode() == "dev"`). **FAIL** if a
    production context can resolve the dev principal. *(seam: `services/api/auth.py`.)*

Additional **warn/info** (non-boot-blocking, operational): Voyage/Anthropic key
presence (`check_voyage_api_key` / `check_anthropic_api_key` — a hosted deploy may
legitimately run without a Voyage key since backfill is cost-gated and opt-in, so
these stay **warn**, not fail); `ENABLE_RERANKING` off (`check_enable_reranking`);
Voyage rate-limit note (`voyage_rate_limit_note`).

### 2.3 Startup behavior

- In a **hosted context**, the API and worker call the guard **once at startup**
  (an app lifespan/startup hook in `services/api/main.py`; an equivalent call at
  the top of `scripts/run_worker.py::main`). Any **fail** logs a loud, **safe**
  banner (check names + safe messages, never secret values) and **exits non-zero**
  before serving traffic / claiming jobs. This mirrors the existing loud auth-mode
  banner intent (S19 §7).
- In **dev**, the guard is a no-op at startup (local flow unchanged) but every
  check remains runnable via the CLI (§5).
- **Never leaks:** guard output is safe-metadata only — presence/absence and
  operational state, never a key, token, secret, DB password, or the OAuth client
  secret (the existing `preflight.py` contract; extend it verbatim).

---

## 3. Deployment runbook (exact phases)

A single ordered runbook (new `docs/s27-hosted-deploy-runbook.md` in the build).
Each phase has a gate; a failed gate stops the deploy. Commands assume the blessed
venv (`.venv\Scripts\python.exe`, never a bare `python`) and are illustrative — the
implementing sprint pins exact hosted commands.

1. **Preflight (no traffic shift).** Run the hosted preflight command (§5.1)
   against the target environment's config. **Gate:** every fail-closed check
   passes. This catches dev auth/vault, missing OAuth config, wrong redirect URI,
   missing redaction, and (if reachable) DB/migration state *before* anything ships.
2. **Migrations.** Apply `alembic upgrade head` against the managed DB as an
   explicit operator step (never lazy at request time). **Gate:**
   `check_alembic_head` reports head (`0012_job_infra` today). Migrations are
   forward-only in normal operation; rollback is §3-rollback.
3. **Deploy API.** Roll out the API process(es). Startup guard (§2.3) must pass or
   the process exits and the rollout halts. **Gate:** `/healthz` returns ok **and**
   `/readyz` (§5.2) returns ready on the new replicas before shifting traffic.
4. **Deploy worker.** Roll out the worker process(es). **Gate:** the worker
   emits a fresh heartbeat (§5.3) and the API's worker-required check (§2.2.6)
   flips to pass.
5. **Deploy frontend.** Publish the static build (built with the production API
   base URL, §2.2.10). **Gate:** the app loads against the hosted API and shows no
   dev mailbox-id entry path.
6. **Health checks.** Confirm `/healthz` (liveness) and `/readyz` (readiness:
   DB + migration + auth-mode + vault + redaction) across replicas. **Gate:** all
   ready.
7. **OAuth callback smoke.** Exercise the Gmail connect start→callback against the
   hosted redirect URI in a controlled test tenant; confirm the access log shows
   the callback with `code`/`state` **REDACTED** (§2.2.9) and that a token is stored
   only as a `vault_ref`. **Gate:** redaction verified in the actual hosted logs;
   no raw code/token anywhere. *(Does not require a live mailbox ingest.)*
8. **Worker smoke.** Enqueue the harmless `noop` job (S24) via the jobs API and
   confirm the worker drains it to `succeeded` within the threshold. **Gate:**
   `noop` terminal-succeeds; proves API→queue→worker→DB round-trips in the hosted
   topology. *(No mailbox content, no cost.)*
9. **Recipient package smoke.** Publish a package in a test tenant, open the
   one-time recipient link, and confirm read-only package-local access works **and**
   that no recipient-reachable route touches jobs/pipeline/mailbox rows (§2.2.12
   assertion holds in the running system). **Gate:** recipient can read the frozen
   snapshot; recipient session cannot reach any creator/jobs/pipeline route.
10. **Rollback procedure.** If any post-deploy gate fails:
    - **API/worker/frontend:** redeploy the previous image/build (they are
      stateless; the previous version is always safe to restore). Traffic shifts
      back before investigating.
    - **Migrations:** prefer **forward-fix** (a new migration) over `alembic
      downgrade`. A downgrade is only run if the new migration is provably
      reversible and no writes depend on it; because S27 adds **no migration** and
      the current head is `0012_job_infra`, a normal S27 rollback is a code/config
      rollback with **no DB change**. Any future schema-bearing sprint documents its
      own reversible downgrade + a tested restore.
    - **Vault/secrets:** never roll a secret back into the app DB or an image;
      secret rotation is a secrets-manager procedure (S20 §3), audited separately.
    - **Backups:** DB and vault are backed up **separately** (S20 §3); a restore
      must never resurrect a revoked OAuth token (revocation state is authoritative
      in the app-DB row).

---

## 4. Production readiness checklist (operator sign-off)

A checklist embedded in the runbook; each item maps to a §2 check or a documented
manual step. Sign-off is required before traffic shift.

- **Auth mode** — hosted context resolves `AUTH_MODE=production`; no dev fallback
  reachable (§2.2.1, §2.2.13).
- **Tenant owner binding** — every mailbox in the target has `tenant_id` +
  `owner_user_id`; no tenant-less mailbox-derived rows (S19 §3). *(Data check, not
  a boot gate; part of tenant onboarding.)*
- **OAuth / token vault** — production `TokenVault` registered; `EKC_ALLOW_DEV_VAULT`
  unset; client id/secret/redirect URI set to the hosted origin (§2.2.2, §2.2.8).
- **DB migrations** — at head; migrations applied by the operator step, not lazily
  (§2.2.5).
- **Worker status** — worker deployed, heartbeat fresh, API worker-required check
  green (§2.2.6, §5.3).
- **Job stuck/retry behavior** — no `running` jobs with expired leases beyond
  threshold; retry/backoff bounded by `max_attempts`; per-mailbox/per-tenant limits
  as specified (S21 §12); queue depth sane (§2.2.7).
- **Access-log redaction** — `AccessLogQueryRedactionFilter` present; OAuth callback
  smoke shows `REDACTED` (§2.2.9, §3.7).
- **Safe job metadata** — jobs sanitize params/progress/summary/errors
  (`services/jobs/sanitize.py`); errors are category/type-name only, no tracebacks
  (S21 §10). *(Assertion + spot check.)*
- **Recipient snapshot-only invariant** — recipient router import assertion green;
  recipient package smoke confirms no jobs/pipeline/mailbox reach (§2.2.12, §3.9).
- **Audit/log safety** — no secret/token/content in logs or audit metadata; audit
  events are safe-field only (S19 §9, S20 §10, S21 §10).
- **Backup / restore expectations** — DB backup + separate vault backup configured;
  a restore has been tested; a restore never resurrects a revoked token (S20 §3).
  *(Expectation stated + verified operationally; enforcement/automation is later.)*
- **Rate / cost gates for Voyage / Anthropic** — `embedding_backfill` stays
  cost-gated (no live Voyage call without an explicit operator confirm, S26 + the
  standing Voyage-key rule); Anthropic event extraction batched/backoff-bounded; a
  per-tenant cost cap / kill switch is documented (S21 §12). *(Guardrail behavior
  already shipped; checklist confirms it is not bypassed by hosted config.)*

---

## 5. Runnable checks — hosted preflight, readiness endpoint, worker health

The guardrails must be **runnable**, not just prose, so CI and the deploy pipeline
enforce them.

### 5.1 Hosted preflight command

A CLI that runs the §2 checks and exits non-zero on any fail-closed failure.
Recommended: **extend the existing `scripts/preflight.py`** with a `--hosted` flag
(reusing the `PreflightCheck` framework and safe-output contract) rather than a
parallel tool, so there is one preflight surface. `--hosted` adds the §2.2 checks
to the existing operational checks and applies the fail-closed exit semantics. Run
in CI (against a synthetic hosted config with a fake DB/vault so no secret or live
call is needed) and as runbook phase 1.

### 5.2 Readiness endpoint

Add `GET /readyz` (and mirror under `/api/readyz` for the proxy, matching the
existing `/healthz` + `/api/health` pattern in `services/api/main.py`). Semantics:

- **Liveness** stays `/healthz` (process up) — unchanged.
- **Readiness** `/readyz` returns **200 `{"status":"ready"}`** only when the
  boot-critical checks hold (DB reachable, migration head current, auth-mode
  production in a hosted context, production vault registered, redaction installed);
  otherwise **503 `{"status":"degraded"}`**. **Safety:** the public body is a bare
  status string — **no** check names, counts, secret presence, or config values
  (an unauthenticated readiness probe must not become an info-leak or existence
  oracle). A **detailed** readiness report (the full §2 check list) is available
  **only** via the CLI (§5.1) or, later, an operator/admin-guarded endpoint — never
  unauthenticated.

### 5.3 Worker health signal

The API needs evidence a worker is alive without the worker holding an HTTP port.
Recommended minimal signal, **no schema change**: the worker periodically writes a
lightweight heartbeat the API can read. Options (pick at build time, open question
§8.2):

- **(a) Reuse the `job` table** — treat a recent `started_at`/`lease_expires_at`
  touch, or a periodic self-enqueued/self-claimed internal `noop`, as liveness. No
  migration; slightly indirect.
- **(b) A tiny `worker_heartbeat` marker** — a single-row keyed table the worker
  upserts every N seconds with `worker_id` + `beat_at` (safe metadata only). Cleaner
  signal but **adds a migration** (see §7 migration expectation — this is the one
  thing that could make S27 schema-bearing; default to (a) to keep S27 migration-free
  unless the operator wants (b)).

The API's worker-required check (§2.2.6) reads whichever signal is chosen and the
readiness/queue-health checks (§2.2.7) read the lease fields already on `job`.

---

## 6. What implementation belongs in S27 vs later

**S27 implements (guardrails / runbook / checks only):**

- The **environment-validation module** (new `services/hosted_readiness.py`, or an
  extension of `services/preflight.py`) producing the §2 checks.
- The **startup guard** wired into `services/api/main.py` (lifespan/startup hook)
  and `scripts/run_worker.py` (top of `main`), fail-closed in hosted contexts.
- The **hosted preflight** CLI surface (`scripts/preflight.py --hosted`, §5.1).
- The **`/readyz` + `/api/readyz`** endpoints (§5.2) and the **worker-health signal**
  (§5.3, default the migration-free option).
- The **recipient-router import assertion** as a startup self-guard + CI test
  (§2.2.12) — cheap insurance the invariant cannot silently regress.
- **Docs:** `docs/s27-hosted-deploy-runbook.md` (§3 + §4), plus status pointers in
  README / AGENTS / CLAUDE / `docs/implementation-plan.md`.

**S27 does NOT implement (later sprints):** real infra provisioning; the admin/audit
UI; a telemetry vendor/collector; M365; a concrete production IdP/SSO/session
backend (only the fail-closed guard that production ≠ dev); recipient accounts;
manager approval; multi-recipient; rich recipient trees; retention/cleanup
enforcement; the external security review. (See §0 non-goals.)

---

## 7. Acceptance criteria for the S27 build (so the next sprint doesn't guess)

**Exact files likely to change in implementation:**

- `services/preflight.py` — add the §2.2 hosted checks (or add a sibling
  `services/hosted_readiness.py` reusing `PreflightCheck`).
- `services/api/main.py` — call the startup guard in a lifespan/startup hook; add
  `GET /readyz` and `GET /api/readyz`.
- `scripts/preflight.py` — add `--hosted` (fail-closed exit semantics).
- `scripts/run_worker.py` — call the startup guard at the top of `main`; emit the
  worker-health signal (§5.3).
- `services/oauth/vault.py` — (small) a `set_vault` production-registration seam /
  a `is_dev_vault()` predicate the guard reads (no behavior change to `DevTokenVault`).
- `services/api/log_redaction.py` — (optional) export an `is_redaction_installed()`
  predicate the guard reads.
- `frontend/` build config — assert `VITE_API_BASE_URL` (or equivalent) in a
  production build; keep the dev mailbox-id entry path dev-only.
- `docs/s27-hosted-deploy-runbook.md` (new); pointer updates in README / AGENTS /
  CLAUDE / `docs/implementation-plan.md`.
- `tests/test_s27_hosted_readiness.py` (new).

**Proposed endpoints / scripts:** `GET /readyz` + `GET /api/readyz` (safe status
only); `scripts/preflight.py --hosted`; startup guard invoked by both the API and
the worker; optional operator/admin-guarded detailed-readiness endpoint (later).

**Migration expectation:** **None by default.** S27 is a guardrail/runbook slice and
should ship with **no Alembic migration** (head stays `0012_job_infra`). The *only*
thing that would introduce a migration is choosing the dedicated `worker_heartbeat`
table (§5.3 option b) over the migration-free job-table signal (option a). Default
to option (a) → no migration; if (b) is chosen, it is a single safe, reversible
metadata table (open question §8.2).

**Manual validation steps (the S27 build must demonstrate):**

- With `AUTH_MODE=production` + no production vault (or `EKC_ALLOW_DEV_VAULT=1`),
  the API and worker **refuse to boot** with a safe banner; with a valid hosted
  config they boot.
- `scripts/preflight.py --hosted` exits non-zero on each fail-closed condition
  (dev auth, dev vault, missing/dev-default `DATABASE_URL`, un-migrated DB, missing
  OAuth config, localhost redirect URI, missing redaction, wildcard/missing CORS)
  and zero on a clean synthetic hosted config — **without** any live Voyage/Anthropic
  call or real secret.
- `/readyz` returns 503 when a boot-critical check fails and 200 when ready; the
  body never contains check names, config, or secrets.
- OAuth callback smoke: the hosted access log shows `code`/`state` as `REDACTED`.
- Worker smoke: an enqueued `noop` reaches `succeeded`; worker-required check flips
  to pass on worker deploy and to fail/degraded when no worker heartbeat is fresh.
- Recipient package smoke: recipient link reads the frozen snapshot; the recipient
  session reaches **no** jobs/pipeline/mailbox route.

**Security / privacy invariants (must survive the S27 build):**

- **Recipient package views stay snapshot-only** — the import assertion + the
  recipient smoke prove it; no guardrail or health surface widens recipient reach.
- **No secret/token/content in any new surface** — preflight output, the startup
  banner, `/readyz`, worker heartbeat, and audit metadata carry safe metadata only
  (presence/state/category), never keys, tokens, the OAuth client secret, DB
  passwords, or email content (D6; `preflight.py`/`sanitize.py` contracts).
- **OAuth callback redaction cannot be silently disabled** — a hosted process that
  lacks the redaction filter fails the guard and does not serve (§2.2.9).
- **Production cannot fall back to dev** — no env/flag re-enables the dev principal,
  raw mailbox-id loading, or `DevTokenVault` under production (§2.2.2/§2.2.13).
- **No productivity/performance scoring; not employee monitoring** — health/queue
  metrics are operational counts/phases only, never repurposed as a per-person
  signal (S21 §13).
- **Cost gates intact** — hosted config cannot bypass the `embedding_backfill`
  cost gate or the Voyage-key authorization rule.

**Tests expected for the S27 implementation (`tests/test_s27_hosted_readiness.py`):**

- Each §2.2 check: pass and fail cases, driven by env/dependency injection (reuse
  the `preflight.py` injectable-engine/session pattern — **no** live DB/secret
  required for the pure checks).
- Startup guard: hosted context + a failing check → raises/exits; dev context →
  no-op; clean hosted config → boots.
- `/readyz`: 200 when ready, 503 when a boot-critical dependency is down; body is
  the bare safe status (assert no check names/secrets leak).
- Recipient-router import assertion (mirrors the existing recipient-isolation test).
- Worker-health signal: fresh vs. stale heartbeat drives the worker-required check.
- Redaction predicate: filter present → pass; absent → fail (reuse
  `log_redaction` unit coverage).
- **No live Voyage/Anthropic call** in any S27 test; the two live-call guards stay
  skipped (739 passed / 2 skipped baseline preserved).

**Explicit non-goals** — as §0 (no infra provisioning, admin UI, telemetry vendor,
M365, production IdP beyond the fail-closed guard, recipient auth, manager approval,
multi-recipient, rich recipient trees, retention enforcement, external security
review).

---

## 8. Open questions (for product / engineering before the S27 build)

1. **Hosted-context signal:** gate solely on `AUTH_MODE=production`, or require a
   redundant positive `EKC_DEPLOY_ENV=production` so a misconfigured `AUTH_MODE`
   cannot silently downgrade the guardrails? (Recommend: require both.)
2. **Worker-health mechanism:** the migration-free job-table signal (§5.3a) or a
   dedicated `worker_heartbeat` table (§5.3b, adds one migration)? (Recommend: (a)
   to keep S27 migration-free; revisit if a richer worker fleet view is needed.)
3. **Production `TokenVault` backend:** which secrets manager / KMS is the first
   production vault, and is its interface exactly the shipped `TokenVault` protocol
   (`services/oauth/vault.py`) with no signature change?
4. **CORS / origin split:** will the hosted frontend be same-origin with the API
   (reverse-proxied) or cross-origin (separate CDN host)? Determines whether an
   explicit CORS allow-list middleware is added in S27 (§2.2.11).
5. **Readiness exposure:** is a bare public `/readyz` acceptable, or must even
   liveness/readiness sit behind the load balancer only? Confirm the detailed report
   stays CLI/operator-only.
6. **Migration application ownership:** who runs `alembic upgrade head` in the
   hosted pipeline (a dedicated migration job vs. an operator step), and how is the
   "migrations applied before API boot" ordering enforced (§3.2)?
7. **Backup/restore ownership:** what is the concrete DB + vault backup cadence and
   who owns the tested-restore drill (S27 states the expectation; enforcement/
   automation is later)?
8. **Per-tenant cost caps / kill switch:** are the S21 §12 per-tenant Voyage/Anthropic
   caps and the operator kill switch in scope as *documented runbook levers* for
   S27, or deferred to the security-hardening sprint? (Recommend: document the
   levers in the runbook now; automated enforcement later.)

---

## 9. Resolved product/engineering defaults

The §8 open questions may still be revisited on detail, but the S27 **implementation
has a clear default path** and does not need to wait on them. These defaults are
authoritative for the build; deviating from one requires an explicit decision.

1. **Hosted-context gate (resolves §8.1).** Require **both** `AUTH_MODE=production`
   **and** `EKC_DEPLOY_ENV=production` for the hosted production guardrails to
   apply. Fail-closed pairing rules the build must enforce:
   - `EKC_DEPLOY_ENV=production` **and** `AUTH_MODE=dev` → **fail startup and
     readiness loudly** (a hosted deploy that fell back to dev auth is the exact
     thing to catch; §2.2.1).
   - `AUTH_MODE=production` **but** `EKC_DEPLOY_ENV` missing/not `production` in a
     hosted deployment → **fail readiness** (`/readyz` degraded) — the positive
     deploy-env signal is required, so a partially-configured process is not
     mistaken for ready.
   - Both set to production → guardrails apply; both absent (local dev) → the
     startup guard is a no-op and checks remain CLI-runnable (§2.1, §2.3).

2. **Worker-health signal (resolves §8.2).** **Migration-free for S27.** Reuse the
   existing `job` table + worker lease/heartbeat behavior (§5.3a — `started_at`
   /`lease_expires_at` freshness and the `FOR UPDATE SKIP LOCKED` claim) to prove a
   worker is alive and claiming. **Do not** add a `worker_heartbeat` table (§5.3b)
   unless the implementation demonstrably proves the job-table approach is
   insufficient. Default: **no migration; head stays `0012_job_infra`.**

3. **Production `TokenVault` (resolves §8.3).** Keep the provider-neutral
   `TokenVault` protocol (`services/oauth/vault.py`) unchanged; the first concrete
   production adapter (KMS/secrets-manager) is selected during implementation. S27
   **must fail closed if only `DevTokenVault` is available in a hosted production
   context** (§2.2.2/§2.2.3), and **`EKC_ALLOW_DEV_VAULT` must never be honored in
   hosted production** — if set alongside `EKC_DEPLOY_ENV=production`, that is a
   boot/readiness failure, not an override.

4. **Frontend / API origin (resolves §8.4).** **Same-origin deployment is the
   preferred default** (frontend served behind the same origin as `/api`, e.g. a
   reverse proxy) and needs no CORS middleware. An explicit CORS **allow-list** is
   added **only** when the frontend and API are cross-origin. **No wildcard (`*`)
   CORS in production** under any topology (§2.2.11).

5. **Migration ordering (resolves §8.6).** Migrations **must run before** the API
   or worker are considered ready. `GET /readyz` **fails (503)** when the DB is not
   at Alembic head, and the worker likewise **refuses to run / fails its readiness
   signal** when the DB is not at head (reusing `services/preflight.py::check_alembic_head`;
   §2.2.5, §3.2). A process never lazily migrates at request time.

6. **Cost caps / kill switch (resolves §8.8).** S27 includes **environment/readiness
   checks that a provider kill switch and the existing cost gates are configured/
   intact** (the `embedding_backfill` cost gate + the Voyage-key authorization rule
   stay un-bypassable), but S27 does **not** build a full cost-governance system.
   **Per-tenant hard cost caps remain deferred** to the security/privacy hardening
   sprint unless a given cap is already trivial with current config.

7. **S27 implementation boundary (resolves scope).** The S27 build is
   **guardrails / runbook / readiness only**: the hosted preflight/check command,
   the safe `/readyz`, the worker health/readiness signal, the environment-validation
   module, the deployment runbook, and their tests. **No** broad hosted migration,
   **no** admin UI, **no** new auth provider (only the fail-closed guard that
   production ≠ dev), and **no** core product-behavior changes.

---

## Acceptance (this docs/spec sprint)

- Docs/spec-only; **no** backend/frontend/schema/migration/dependency changes in
  this document's sprint.
- New `docs/s27-hosted-deploy-readiness-plan.md` (this file).
- README / AGENTS / CLAUDE / `docs/implementation-plan.md` updated with
  **pointer/status lines only** — S27 is **planned / docs-only, not implemented**.
- `git diff --check` clean.
- **S27 is clearly not implemented** — every module, endpoint, script, and check
  above is *proposed for the S27 build*; none exist yet.
- **S17.2–S17.20 shipped; S18–S21 docs-only specs; S22–S26 implemented; S27
  docs-only** — stated in the status block.
- **Recipient package snapshot-only invariant untouched** (stated up top; every
  guardrail is creator/operator-side and one check exists to prove the invariant).
- Specific enough that the S27 build can proceed directly from §2 (checks), §3
  (runbook), §5 (runnable surfaces), and §7 (files/tests/acceptance) without
  guessing.
