"""S27 — hosted deployment readiness checks + startup guardrails.

Implements ``docs/s27-hosted-deploy-readiness-plan.md`` (§2 checks, §5 runnable
surfaces, §9 resolved defaults). This is a **safety-gated readiness slice**, not a
hosted migration: the checks below make it *hard* to run a hosted process with dev
auth/vault, a missing/dev-default ``DATABASE_URL``, an un-migrated DB, missing/
localhost OAuth config, missing OAuth-callback log redaction, wildcard CORS, an
unreachable job queue, or a recipient-router snapshot-only regression.

Design (all testable without real external services):

* Every check is a pure function returning a ``PreflightCheck`` (reused from
  ``services.preflight``) — presence/state/category only, **never** a secret value,
  token, DB URL, OAuth client secret, or mailbox content.
* ``HostedEnv`` snapshots the environment once; checks take primitives + injected
  dependencies (engine/session/vault-is-dev/redaction-installed) so tests drive
  them offline.
* **Two gates, per the accepted defaults (§9.1):**
  - ``EKC_DEPLOY_ENV=production`` is the explicit hosted-deploy signal that turns on
    **fail-fast startup enforcement** (``run_startup_guard`` raises → the API/worker
    refuse to boot).
  - ``/readyz`` evaluates hosted checks whenever *either* signal declares production
    (``AUTH_MODE=production`` or ``EKC_DEPLOY_ENV=production``), so an
    ``AUTH_MODE=production`` process that is missing ``EKC_DEPLOY_ENV`` fails
    readiness (degraded) even though it is not hard-failed at startup.
  - Local dev (``AUTH_MODE=dev``, no ``EKC_DEPLOY_ENV``) is a **no-op**: the guard
    does nothing and ``/readyz`` reports ready, so the localhost workflow and the
    test suite never require production secrets.
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from services.preflight import (
    PreflightCheck,
    check_alembic_head,
    check_database,
)

# Env var names (documented in the runbook).
ENV_DEPLOY = "EKC_DEPLOY_ENV"
ENV_ALLOW_DEV_VAULT = "EKC_ALLOW_DEV_VAULT"
ENV_ALLOWED_ORIGINS = "EKC_ALLOWED_ORIGINS"
ENV_FRONTEND_API_BASE = "EKC_FRONTEND_API_BASE_URL"


class HostedReadinessError(RuntimeError):
    """Raised by ``run_startup_guard`` when a hosted process is misconfigured.

    Carries the failed checks so callers can log a **safe** banner (names +
    messages) and refuse to start. Never carries secret values.
    """

    def __init__(self, failures: list[PreflightCheck]) -> None:
        self.failures = failures
        super().__init__(self.safe_summary())

    def safe_summary(self) -> str:
        return "; ".join(f"{c.name}: {c.message}" for c in self.failures)


# ── Environment snapshot ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class HostedEnv:
    auth_mode: str            # "dev" | "production" (resolved like get_auth_mode)
    deploy_env: str           # EKC_DEPLOY_ENV, lowercased/stripped
    allow_dev_vault: bool     # EKC_ALLOW_DEV_VAULT == "1"
    database_url: str         # raw DATABASE_URL (never logged/echoed)
    is_default_database_url: bool
    oauth_client_id: str
    oauth_client_secret: str
    oauth_redirect_uri: str
    allowed_origins: str      # raw EKC_ALLOWED_ORIGINS
    frontend_api_base: str    # raw EKC_FRONTEND_API_BASE_URL

    @property
    def hosted_declared(self) -> bool:
        """Explicit hosted-deploy signal → fail-fast startup enforcement (§9.1)."""
        return self.deploy_env == "production"

    @property
    def production_intent(self) -> bool:
        """Either signal declares production → ``/readyz`` evaluates hosted checks."""
        return self.deploy_env == "production" or self.auth_mode == "production"


def gather_env(getenv: Callable[[str, str], str] = None) -> HostedEnv:
    """Snapshot the environment. ``getenv`` is injectable for tests."""
    if getenv is None:
        getenv = lambda k, d="": os.environ.get(k, d)  # noqa: E731

    auth_mode = "dev" if getenv("AUTH_MODE", "").strip().lower() == "dev" else "production"

    database_url = getenv("DATABASE_URL", "")
    from services.db.engine import DEFAULT_DATABASE_URL
    is_default = bool(database_url) and database_url == DEFAULT_DATABASE_URL

    # OAuth config values come from the same loader the app uses; the client secret
    # is read only to test presence — it is NEVER placed in a check message.
    from services.oauth.config import load_config
    cfg = load_config()

    return HostedEnv(
        auth_mode=auth_mode,
        deploy_env=getenv(ENV_DEPLOY, "").strip().lower(),
        allow_dev_vault=getenv(ENV_ALLOW_DEV_VAULT, "").strip() == "1",
        database_url=database_url,
        is_default_database_url=is_default,
        oauth_client_id=cfg.client_id,
        oauth_client_secret=cfg.client_secret,
        oauth_redirect_uri=cfg.redirect_uri,
        allowed_origins=getenv(ENV_ALLOWED_ORIGINS, ""),
        frontend_api_base=getenv(ENV_FRONTEND_API_BASE, ""),
    )


# ── Config / auth / vault checks ──────────────────────────────────────────────

def check_auth_mode(env: HostedEnv) -> PreflightCheck:
    if env.auth_mode == "production":
        return PreflightCheck("auth_mode", "pass", "AUTH_MODE is production")
    return PreflightCheck(
        "auth_mode", "fail",
        "AUTH_MODE must be 'production' in a hosted deployment "
        "(dev auth would enable the synthetic dev principal)",
    )


def check_deploy_env(env: HostedEnv) -> PreflightCheck:
    if env.deploy_env == "production":
        return PreflightCheck("deploy_env", "pass", f"{ENV_DEPLOY} is production")
    return PreflightCheck(
        "deploy_env", "fail",
        f"{ENV_DEPLOY} must be set to 'production' in a hosted deployment "
        "(required positive deploy signal)",
    )


def check_no_mailbox_id_bypass(env: HostedEnv) -> PreflightCheck:
    """Raw mailbox-id loading / the dev synthetic principal must be unreachable in
    production. It is reachable ONLY when auth resolves to dev, so this restates the
    auth-mode invariant as its own explicit guardrail (S19 §7)."""
    if env.auth_mode == "production":
        return PreflightCheck(
            "no_mailbox_id_bypass", "pass",
            "Raw mailbox-id / dev-principal bypass is disabled (production auth)",
        )
    return PreflightCheck(
        "no_mailbox_id_bypass", "fail",
        "Dev principal / raw mailbox-id loading is reachable — production auth required",
    )


def check_token_vault(env: HostedEnv, vault_is_dev: bool) -> PreflightCheck:
    """The dev token vault must never back a hosted deployment (§9.3)."""
    if env.allow_dev_vault:
        return PreflightCheck(
            "token_vault", "fail",
            f"{ENV_ALLOW_DEV_VAULT} must not be set in hosted production "
            "(it would allow the dev token vault)",
        )
    if vault_is_dev:
        return PreflightCheck(
            "token_vault", "fail",
            "No production TokenVault is registered — DevTokenVault is dev/test-only; "
            "register a KMS/secrets-manager vault",
        )
    return PreflightCheck("token_vault", "pass", "A production TokenVault is registered")


def check_database_url(env: HostedEnv) -> PreflightCheck:
    """Present and not the local dev default. The URL value is never echoed."""
    if not env.database_url:
        return PreflightCheck("database_url", "fail", "DATABASE_URL is not set")
    if env.is_default_database_url:
        return PreflightCheck(
            "database_url", "fail",
            "DATABASE_URL is the local dev default — set the hosted database URL",
        )
    return PreflightCheck("database_url", "pass", "DATABASE_URL is configured")


# ── OAuth checks ──────────────────────────────────────────────────────────────

def check_oauth_config(env: HostedEnv) -> PreflightCheck:
    """Client id + secret configured. The secret value is never echoed."""
    missing = []
    if not env.oauth_client_id:
        missing.append("client id")
    if not env.oauth_client_secret:
        missing.append("client secret")
    if missing:
        return PreflightCheck(
            "oauth_config", "fail",
            f"Gmail OAuth {', '.join(missing)} not configured "
            "(GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET)",
        )
    return PreflightCheck("oauth_config", "pass", "Gmail OAuth client id + secret configured")


def check_oauth_redirect(env: HostedEnv) -> PreflightCheck:
    uri = env.oauth_redirect_uri or ""
    if not uri:
        return PreflightCheck("oauth_redirect", "fail", "OAuth redirect URI is not set")
    lowered = uri.lower()
    if "localhost" in lowered or "127.0.0.1" in lowered:
        return PreflightCheck(
            "oauth_redirect", "fail",
            "OAuth redirect URI is localhost — set the hosted-origin HTTPS callback",
        )
    if not lowered.startswith("https://"):
        return PreflightCheck(
            "oauth_redirect", "fail",
            "OAuth redirect URI must be an https:// hosted-origin callback",
        )
    return PreflightCheck("oauth_redirect", "pass", "OAuth redirect URI is a hosted https callback")


def check_access_log_redaction(redaction_installed: bool) -> PreflightCheck:
    """The OAuth-callback access-log redaction filter must be installed (S23)."""
    if redaction_installed:
        return PreflightCheck(
            "access_log_redaction", "pass",
            "OAuth callback access-log redaction is installed",
        )
    return PreflightCheck(
        "access_log_redaction", "fail",
        "OAuth callback access-log redaction is NOT installed — code/state could leak to logs",
    )


# ── CORS / frontend origin checks ─────────────────────────────────────────────

def _origin_list(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


def check_cors_no_wildcard(env: HostedEnv) -> PreflightCheck:
    """No wildcard CORS in hosted production (§9.4). Unset = same-origin default."""
    origins = _origin_list(env.allowed_origins)
    if "*" in origins or env.allowed_origins.strip() == "*":
        return PreflightCheck(
            "cors", "fail",
            "Wildcard (*) CORS is not allowed in hosted production — "
            "use same-origin or an explicit allow-list",
        )
    if not origins:
        return PreflightCheck("cors", "pass", "No CORS allow-list set (same-origin default)")
    return PreflightCheck("cors", "pass", f"Explicit CORS allow-list configured ({len(origins)} origin(s))")


def check_frontend_origin(env: HostedEnv) -> PreflightCheck:
    """Same-origin is the preferred default; cross-origin should declare the API base."""
    cross_origin = bool(_origin_list(env.allowed_origins))
    if not cross_origin:
        return PreflightCheck(
            "frontend_origin", "pass",
            "Same-origin frontend/API deployment (preferred default)",
        )
    if not env.frontend_api_base:
        return PreflightCheck(
            "frontend_origin", "warn",
            "Cross-origin CORS configured but no frontend API base declared "
            f"({ENV_FRONTEND_API_BASE}) — ensure the frontend build targets the hosted API",
        )
    return PreflightCheck("frontend_origin", "pass", "Cross-origin frontend API base configured")


# ── Recipient snapshot-only static assertion ──────────────────────────────────

# Import-surface tokens a recipient (package-local snapshot-only) router must never
# contain. Chosen to be import-specific so they never false-match docstring prose
# (e.g. the word "job" appears in the module docstring).
_RECIPIENT_FORBIDDEN = (
    "get_principal",
    "require_owner",
    "from services.jobs",
    "import services.jobs",
    "pipeline_jobs",
    "from ..auth import",
    "from services.api.auth",
)


def check_recipient_snapshot_only() -> PreflightCheck:
    """Static guard: the recipient router imports none of jobs/pipeline/principal/
    owner-guards, so a recipient session cannot reach creator/jobs/mailbox rows."""
    try:
        from services.api.routers import handoff_recipient
        src = inspect.getsource(handoff_recipient)
    except Exception as exc:  # pragma: no cover - defensive
        return PreflightCheck(
            "recipient_snapshot_only", "fail",
            f"Could not inspect the recipient router ({type(exc).__name__})",
        )
    hits = [tok for tok in _RECIPIENT_FORBIDDEN if tok in src]
    if hits:
        return PreflightCheck(
            "recipient_snapshot_only", "fail",
            "Recipient router references a forbidden creator/jobs/pipeline symbol — "
            "package-local snapshot-only invariant may be broken",
        )
    return PreflightCheck(
        "recipient_snapshot_only", "pass",
        "Recipient router is package-local snapshot-only (no jobs/pipeline/principal imports)",
    )


# ── Worker / job-queue checks (migration-free, §9.2) ──────────────────────────

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def check_worker_queue_observable(session) -> PreflightCheck:
    """The Postgres-backed job queue must be readable (a worker can claim from it)."""
    try:
        from sqlalchemy import func, select

        from services.db import models as orm
        session.execute(select(func.count()).select_from(orm.Job))
        return PreflightCheck("job_queue", "pass", "Job queue is observable")
    except Exception as exc:
        return PreflightCheck(
            "job_queue", "fail",
            f"Job queue is not observable ({type(exc).__name__})",
        )


def check_worker_activity(session, *, now: datetime = None, max_age_seconds: int = 300) -> PreflightCheck:
    """Migration-free worker liveness via the `job` table lease/heartbeat (§9.2).

    Warn (not fail) so an idle-but-healthy system is not marked not-ready: recent
    ``started_at`` (a worker claimed a job) proves liveness; absence is a warn.
    """
    now = now or datetime.now(timezone.utc)
    try:
        from sqlalchemy import func, select

        from services.db import models as orm
        last = session.execute(select(func.max(orm.Job.started_at))).scalar()
    except Exception as exc:
        return PreflightCheck("worker_activity", "warn", f"Could not read worker activity ({type(exc).__name__})")
    if not isinstance(last, datetime):
        return PreflightCheck("worker_activity", "warn", "No worker activity observed yet (no jobs have run)")
    age = (now - _aware(last)).total_seconds()
    if age <= max_age_seconds:
        return PreflightCheck("worker_activity", "pass", f"Worker active within {int(age)}s")
    return PreflightCheck("worker_activity", "warn", f"No recent worker activity (last ~{int(age)}s ago)")


def check_stuck_jobs(session, *, now: datetime = None) -> PreflightCheck:
    """Warn on running jobs whose lease expired (a crashed/hung worker signature)."""
    now = now or datetime.now(timezone.utc)
    try:
        from sqlalchemy import func, select

        from services.db import models as orm
        count = session.execute(
            select(func.count()).select_from(orm.Job).where(
                orm.Job.status == "running",
                orm.Job.lease_expires_at.is_not(None),
                orm.Job.lease_expires_at < now,
            )
        ).scalar() or 0
    except Exception as exc:
        return PreflightCheck("stuck_jobs", "warn", f"Could not check stuck jobs ({type(exc).__name__})")
    if count:
        return PreflightCheck("stuck_jobs", "warn", f"{count} running job(s) with an expired lease (possible stuck worker)")
    return PreflightCheck("stuck_jobs", "pass", "No stuck jobs")


# ── Cost / kill-switch (env/config only — no cost-governance system, §9.6) ─────

def check_cost_gates(env: HostedEnv, getenv: Callable[[str, str], str] = None) -> PreflightCheck:
    """Confirm the cost gates are intact; do NOT build cost governance here.

    The `embedding_backfill` cost gate and the Voyage-key authorization rule are
    enforced in code and cannot be bypassed by hosted config. This check surfaces
    that state and notes the optional kill-switch env for the runbook.
    """
    if getenv is None:
        getenv = lambda k, d="": os.environ.get(k, d)  # noqa: E731
    kill = getenv("EKC_EMBEDDING_KILL_SWITCH", "").strip() == "1"
    if kill:
        return PreflightCheck(
            "cost_gates", "info",
            "Embedding kill switch is ON (EKC_EMBEDDING_KILL_SWITCH=1); backfill blocked",
        )
    return PreflightCheck(
        "cost_gates", "info",
        "Embedding backfill stays cost-gated (no live Voyage call without explicit confirm); "
        "per-tenant cost governance deferred",
    )


# ── Aggregate runner ──────────────────────────────────────────────────────────

def run_hosted_checks(
    env: HostedEnv = None,
    *,
    engine=None,
    session=None,
    vault_is_dev: bool = None,
    redaction_installed: bool = None,
    getenv: Callable[[str, str], str] = None,
    now: datetime = None,
) -> list[PreflightCheck]:
    """Run all S27 hosted-readiness checks. Every dependency is injectable so this
    runs offline in tests; with no injection it reads the live env/engine/vault."""
    if env is None:
        env = gather_env(getenv)
    if vault_is_dev is None:
        from services.oauth.vault import current_vault_is_dev
        vault_is_dev = current_vault_is_dev()
    if redaction_installed is None:
        from services.api.log_redaction import is_redaction_installed
        redaction_installed = is_redaction_installed()

    checks: list[PreflightCheck] = [
        check_auth_mode(env),
        check_deploy_env(env),
        check_no_mailbox_id_bypass(env),
        check_token_vault(env, vault_is_dev),
        check_database_url(env),
    ]

    db_check = check_database(engine=engine)
    checks.append(db_check)
    if not db_check.failed:
        checks.append(check_alembic_head(engine=engine))
        own_session = session is None
        try:
            if own_session:
                from services.db.engine import SessionLocal
                session = SessionLocal()
            checks.append(check_worker_queue_observable(session))
            checks.append(check_worker_activity(session, now=now))
            checks.append(check_stuck_jobs(session, now=now))
        finally:
            if own_session and session is not None:
                session.close()

    checks.extend([
        check_oauth_config(env),
        check_oauth_redirect(env),
        check_access_log_redaction(redaction_installed),
        check_cors_no_wildcard(env),
        check_frontend_origin(env),
        check_recipient_snapshot_only(),
        check_cost_gates(env, getenv=getenv),
    ])
    return checks


def readiness_failed(checks: list[PreflightCheck]) -> bool:
    return any(c.failed for c in checks)


def evaluate_readiness(
    env: HostedEnv = None,
    **injected,
) -> tuple[bool, list[PreflightCheck]]:
    """(ready, checks) for ``/readyz``.

    Local dev / no production intent → ready with no DB work. Otherwise run the
    hosted checks and report not-ready on any hard failure (§9.1/§9.5)."""
    if env is None:
        env = gather_env(injected.get("getenv"))
    if not env.production_intent:
        return True, []
    checks = run_hosted_checks(env, **injected)
    return (not readiness_failed(checks)), checks


def run_startup_guard(component: str = "api", env: HostedEnv = None, **injected) -> list[PreflightCheck]:
    """Fail-fast startup guard for a hosted process.

    No-op unless ``EKC_DEPLOY_ENV=production`` (the explicit deploy signal), so local
    dev and the test suite never trip it. When hosted-declared, runs the hosted
    checks and **raises ``HostedReadinessError``** on any hard failure so the API /
    worker refuse to boot. Returns the checks (for a safe startup banner) otherwise.
    """
    if env is None:
        env = gather_env(injected.get("getenv"))
    if not env.hosted_declared:
        return []
    checks = run_hosted_checks(env, **injected)
    failures = [c for c in checks if c.failed]
    if failures:
        raise HostedReadinessError(failures)
    return checks
