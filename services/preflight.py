"""Operational preflight checks for the Email Knowledge Continuity stack (S8.3).

All check functions are pure with injectable dependencies (engine, session) so
they can be tested offline without a live DB.  They never log or return secret
values — only presence/absence and operational state.

``run_checks`` is the single entry point used by both the CLI
(``scripts/preflight.py``) and the API (``GET /api/preflight``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CheckStatus = Literal["pass", "fail", "warn", "info"]

_ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"


@dataclass
class PreflightCheck:
    name: str
    status: CheckStatus
    message: str

    @property
    def failed(self) -> bool:
        return self.status == "fail"


def _exc_label(exc: Exception) -> str:
    """Human-readable exception label; surfaces missing module name when relevant."""
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        return f"ModuleNotFoundError('{exc.name}')"
    return type(exc).__name__


# ── Individual check functions ────────────────────────────────────────────────

def check_voyage_api_key() -> PreflightCheck:
    if os.environ.get("VOYAGE_API_KEY"):
        return PreflightCheck("voyage_api_key", "pass", "VOYAGE_API_KEY is configured")
    return PreflightCheck(
        "voyage_api_key", "fail",
        "VOYAGE_API_KEY is not set — L2 retrieval will be disabled",
    )


def check_anthropic_api_key() -> PreflightCheck:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return PreflightCheck("anthropic_api_key", "pass", "ANTHROPIC_API_KEY is configured")
    return PreflightCheck(
        "anthropic_api_key", "fail",
        "ANTHROPIC_API_KEY is not set — synthesis will return 503",
    )


def check_database(engine=None) -> PreflightCheck:
    try:
        if engine is None:
            from services.db.engine import engine as _engine
            engine = _engine
        with engine.connect():
            pass
        return PreflightCheck("database", "pass", "Database is reachable")
    except Exception as exc:
        return PreflightCheck(
            "database", "fail",
            f"Database unreachable ({_exc_label(exc)}) — check DATABASE_URL",
        )


def check_alembic_head(engine=None) -> PreflightCheck:
    try:
        if engine is None:
            from services.db.engine import engine as _engine
            engine = _engine
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        cfg = Config(str(_ALEMBIC_INI))
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head()
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            current_rev = mc.get_current_revision()
        if current_rev == head_rev:
            return PreflightCheck(
                "alembic_head", "pass",
                f"Database is at migration head ({head_rev})",
            )
        return PreflightCheck(
            "alembic_head", "fail",
            f"Database at {current_rev!r}, head is {head_rev!r} — run: alembic upgrade head",
        )
    except Exception as exc:
        return PreflightCheck(
            "alembic_head", "fail",
            f"Could not verify Alembic revision ({_exc_label(exc)})",
        )


def check_embeddings(mailbox_id: str, session=None) -> PreflightCheck:
    own_session = session is None
    try:
        if own_session:
            from services.db.engine import SessionLocal
            session = SessionLocal()
        from sqlalchemy import text
        count = session.execute(
            text(
                "SELECT COUNT(*) FROM message_embedding "
                "WHERE mailbox_id = :mid AND embed_model = 'voyage-4'"
            ),
            {"mid": mailbox_id},
        ).scalar() or 0
        if count > 0:
            return PreflightCheck(
                "embeddings", "pass",
                f"{count} voyage-4 embedding(s) found for this mailbox",
            )
        return PreflightCheck(
            "embeddings", "fail",
            "No voyage-4 embeddings found for this mailbox — run embed_backfill.py",
        )
    except Exception as exc:
        return PreflightCheck(
            "embeddings", "fail",
            f"Could not check embeddings ({_exc_label(exc)})",
        )
    finally:
        if own_session and session is not None:
            session.close()


def check_enable_reranking() -> PreflightCheck:
    if os.environ.get("ENABLE_RERANKING") == "1":
        return PreflightCheck(
            "enable_reranking", "warn",
            "ENABLE_RERANKING=1 — hosted Voyage reranker is active but not validated "
            "for MVP (S7.12 deferred); set to 0 or unset for demo use",
        )
    return PreflightCheck(
        "enable_reranking", "pass",
        "ENABLE_RERANKING is not '1' — reranker is off (correct for MVP)",
    )


def voyage_rate_limit_note() -> PreflightCheck:
    return PreflightCheck(
        "voyage_rate_limits", "info",
        "Production requires a Voyage AI payment method for standard rate limits "
        "(free tier: 3 RPM / 10K TPM). See: dashboard.voyageai.com",
    )


# ── Aggregate runner ──────────────────────────────────────────────────────────

def run_checks(
    mailbox_id: str | None = None,
    engine=None,
    session=None,
) -> list[PreflightCheck]:
    """Run all preflight checks and return the result list.

    ``engine`` and ``session`` are injectable for testing. When None the
    functions fall back to the live engine / SessionLocal.

    Alembic head and embeddings checks are skipped when database is unreachable
    to avoid cascading errors.
    """
    checks: list[PreflightCheck] = []

    checks.append(check_voyage_api_key())
    checks.append(check_anthropic_api_key())

    db_check = check_database(engine=engine)
    checks.append(db_check)

    if not db_check.failed:
        checks.append(check_alembic_head(engine=engine))
        if mailbox_id:
            checks.append(check_embeddings(mailbox_id, session=session))

    checks.append(check_enable_reranking())
    checks.append(voyage_rate_limit_note())

    return checks
