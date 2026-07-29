# S27 — Hosted Deployment Runbook (readiness & guardrails)

> **Scope: readiness and guardrails, NOT infrastructure provisioning.** This
> runbook covers how to *safely* deploy the already-built application to a hosted
> environment and how the S27 guardrails refuse an unsafe deploy. It does **not**
> provision infrastructure (no Terraform/Helm/cloud-account/DNS/TLS/managed-DB
> creation), stand up an observability vendor, or add an admin UI. Those remain
> out of scope (see `docs/s27-hosted-deploy-readiness-plan.md` §0 non-goals).

**Implements:** `docs/s27-hosted-deploy-readiness-plan.md` (§3 runbook, §4
checklist, §9 resolved defaults). Read that plan first.

---

## What S27 shipped (the guardrails you are operating)

- **`services/hosted_readiness.py`** — the environment-validation module: pure,
  injectable checks returning `PreflightCheck` (safe metadata only; never a
  secret, token, or DB URL value).
- **`scripts/preflight.py --hosted`** — runs the hosted checks; exits non-zero on
  any hard failure. Makes no live external call.
- **Startup guards** — the API (`services/api/main.py` lifespan) and the worker
  (`scripts/run_worker.py`) call `run_startup_guard()`. It is a **no-op unless
  `EKC_DEPLOY_ENV=production`**, so local dev and CI are unaffected; in a hosted
  deploy the process **refuses to boot** on unsafe config with a safe banner.
- **`GET /readyz` + `GET /api/readyz`** — readiness probe returning a **bare**
  `{"status":"ready"}` (200) or `{"status":"degraded"}` (503). No detail leaks.
- **`/healthz` + `/api/health`** — unchanged liveness probes.

### The two-signal gate (accepted default §9.1)

- `EKC_DEPLOY_ENV=production` is the **explicit hosted-deploy signal** that turns
  on fail-fast startup enforcement.
- `AUTH_MODE=production` **and** `EKC_DEPLOY_ENV=production` → guardrails apply.
- `EKC_DEPLOY_ENV=production` **+** `AUTH_MODE=dev` → **startup fails loudly**.
- `AUTH_MODE=production` **+** missing `EKC_DEPLOY_ENV` → **`/readyz` degrades**
  (503) even though startup is not hard-failed.
- Local dev (`AUTH_MODE=dev`, no `EKC_DEPLOY_ENV`) → guard no-op, `/readyz` ready.

### Required hosted environment (set via the secrets manager, never in an image)

- `AUTH_MODE=production`, `EKC_DEPLOY_ENV=production`
- `DATABASE_URL` = the managed Postgres URL (must NOT be the local dev default)
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `GOOGLE_OAUTH_REDIRECT_URI` = the hosted-origin **https** callback (not localhost)
- A **production `TokenVault`** registered at startup (`services/oauth/vault.py::set_vault`);
  `EKC_ALLOW_DEV_VAULT` **must be unset** in hosted production
- `EKC_ALLOWED_ORIGINS` — omit for same-origin (preferred); for cross-origin set an
  explicit comma-separated allow-list (**never `*`**); optionally `EKC_FRONTEND_API_BASE_URL`
- `VOYAGE_API_KEY` / `ANTHROPIC_API_KEY` as needed (backfill stays cost-gated)

Commands below use the blessed venv (`.venv\Scripts\python.exe`) and are
illustrative — pin exact hosted commands to your platform.

---

## Deploy phases (each gate must pass before the next)

### 1. Preflight (no traffic shift)
Run the hosted readiness check against the target environment's config:
```
.venv/Scripts/python.exe scripts/preflight.py --hosted
```
**Gate:** exit code 0 (every hard check passes). This catches dev auth/vault,
missing/dev-default `DATABASE_URL`, un-migrated DB, missing OAuth config, a
localhost redirect URI, missing log redaction, and wildcard CORS **before** shipping.

### 2. Migrations (before API/worker are ready — §9.5)
```
.venv/Scripts/python.exe -m alembic upgrade head
```
**Gate:** `alembic current` reports head (`0012_job_infra` today). Migrations are an
explicit operator step, never lazy at request time. `/readyz` and the worker both
fail if the DB is not at head.

### 3. Deploy API
Roll out `uvicorn services.api.main:app`. The lifespan startup guard runs; a
misconfigured process exits and the rollout halts.
**Gate:** `/healthz` → 200 **and** `/readyz` → 200 on the new replicas before
shifting traffic.

### 4. Deploy worker
Roll out `python -m scripts.run_worker`. The worker refuses to start on unsafe
config (dev auth/vault, unreachable DB, DB not at head, unobservable queue).
**Gate:** worker process stays up; `/readyz` worker-activity check is not degraded.

### 5. Deploy frontend
Publish the static build (built against the hosted API base URL; same-origin
preferred). Ensure the production build exposes **no** dev mailbox-id entry path.
**Gate:** the app loads against the hosted API.

### 6. Health checks
Confirm `/healthz` (liveness) and `/readyz` (readiness) across replicas.
**Gate:** all ready.

### 7. OAuth callback smoke (redaction verified)
In a controlled test tenant, run the Gmail connect start→callback against the
hosted redirect URI. Inspect the access log for the callback line.
**Gate:** the `code`/`state` query values appear as `REDACTED`; a token is stored
only as a `vault_ref`. No raw code/token anywhere. (No mailbox ingest required.)

### 8. Worker smoke (noop)
Enqueue the harmless `noop` job (S24) via the jobs API and confirm the worker
drains it to `succeeded`.
**Gate:** `noop` reaches `succeeded` — proves API → queue → worker → DB in the
hosted topology. No content, no cost.

### 9. Recipient package smoke
Publish a package in a test tenant, open the one-time recipient link, confirm
read-only package-local access.
**Gate:** the recipient reads the frozen snapshot; the recipient session cannot
reach any creator/jobs/pipeline route (the `recipient_snapshot_only` guardrail is
green in preflight).

### 10. Rollback
- **API / worker / frontend:** redeploy the previous image/build. All three are
  stateless, so the prior version is always safe to restore; shift traffic back
  before investigating.
- **Migrations:** S27 adds **no migration** (head stays `0012_job_infra`), so a
  normal S27 rollback is a **code/config rollback with no DB change**. For any
  future schema-bearing change, prefer a forward-fix migration over `downgrade`;
  only downgrade when provably reversible with no dependent writes.
- **Vault/secrets:** never roll a secret back into the app DB or an image; rotate
  via the secrets manager (audited separately). A restore must never resurrect a
  revoked OAuth token — revocation state in the app-DB row is authoritative.

---

## Production readiness checklist (operator sign-off)

Each item maps to a hosted-readiness check (`scripts/preflight.py --hosted`) or a
documented manual step. Sign off before shifting traffic.

- **Auth mode** — `AUTH_MODE=production` + `EKC_DEPLOY_ENV=production`; no dev
  fallback reachable (`auth_mode`, `deploy_env`, `no_mailbox_id_bypass` pass).
- **Tenant owner binding** — target mailboxes have `tenant_id` + `owner_user_id`
  (data check, part of tenant onboarding).
- **OAuth / token vault** — production `TokenVault` registered; `EKC_ALLOW_DEV_VAULT`
  unset; client id/secret + hosted https redirect set (`token_vault`,
  `oauth_config`, `oauth_redirect` pass).
- **DB migrations** — at head; applied by the operator step (`database`,
  `alembic_head` pass).
- **Worker status** — worker deployed; `job_queue` observable; `worker_activity`
  not degraded after the worker smoke.
- **Job stuck/retry** — `stuck_jobs` clear; retry/backoff bounded by `max_attempts`;
  per-mailbox/per-tenant limits per S21 §12.
- **Access-log redaction** — `access_log_redaction` passes; OAuth callback smoke
  shows `REDACTED`.
- **Safe job metadata** — jobs sanitize params/progress/summary/errors
  (`services/jobs/sanitize.py`); errors are category/type-name only.
- **Recipient snapshot-only** — `recipient_snapshot_only` passes; recipient smoke
  confirms no jobs/pipeline/mailbox reach.
- **Audit/log safety** — no secret/token/content in logs or audit metadata.
- **Backup / restore** — DB backup + **separate** vault backup configured; a
  restore tested; a restore never resurrects a revoked token.
- **Rate / cost gates** — `embedding_backfill` stays cost-gated; Anthropic
  extraction backoff-bounded; kill-switch (`EKC_EMBEDDING_KILL_SWITCH`) documented.
  (`cost_gates` is informational; full per-tenant cost governance is deferred.)

---

## Warning

This runbook and the S27 guardrails make it **hard to deploy unsafely**; they do
**not** provision infrastructure or guarantee a healthy platform on their own. You
still own the managed Postgres, the secrets manager / KMS-backed production
`TokenVault`, TLS/DNS, backups, and monitoring. Treat a green
`scripts/preflight.py --hosted` as *necessary, not sufficient*.
