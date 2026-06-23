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


def check_embed_client(live: bool = False) -> PreflightCheck:
    """Verify a VoyageEmbedClient can actually be constructed in this runtime.

    This is the check that catches runtime reality the key-presence check misses:
    a missing httpx, a broken import chain, or (historically) the voyageai SDK's
    native uuid_utils .pyd being blocked by Windows Application Control.  Mere
    presence of VOYAGE_API_KEY does not prove L2 retrieval will work.

    Construction makes NO network call, so this is free and safe to run by
    default.  When ``live=True`` (explicit opt-in only), a single tiny embed call
    is made to prove the API credential and endpoint actually work — this costs
    tokens and is never run by default (CLAUDE.md Voyage authorization rule).

    Never logs or returns the key, the endpoint response, or any embedded text —
    only a safe exception class name on failure.
    """
    if not os.environ.get("VOYAGE_API_KEY"):
        # No key — construction would fail for an uninteresting reason. The
        # dedicated key check already reports this; here it is informational so
        # the construction signal is not conflated with a missing key.
        return PreflightCheck(
            "embed_client", "warn",
            "Skipped — VOYAGE_API_KEY not set (see voyage_api_key check)",
        )
    try:
        from services.retrieval.embed_client import VoyageEmbedClient
        client = VoyageEmbedClient()
    except Exception as exc:
        return PreflightCheck(
            "embed_client", "fail",
            f"Voyage embed client could not be constructed ({_exc_label(exc)}) "
            "— L2 retrieval will be silently disabled; check httpx install / runtime",
        )

    if not live:
        return PreflightCheck(
            "embed_client", "pass",
            f"Voyage embed client constructed OK (model={client.model}, dim={client.dim}); "
            "live API not exercised (use --live-embed to verify the credential)",
        )

    try:
        vec = client.embed_query("preflight connectivity probe")
    except Exception as exc:
        return PreflightCheck(
            "embed_client", "fail",
            f"Live Voyage embed call failed ({_exc_label(exc)})",
        )
    if len(vec) != client.dim:
        return PreflightCheck(
            "embed_client", "fail",
            f"Live Voyage embed returned dim {len(vec)}, expected {client.dim}",
        )
    return PreflightCheck(
        "embed_client", "pass",
        f"Live Voyage embed call succeeded (model={client.model}, dim={client.dim})",
    )


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
    live_embed: bool = False,
) -> list[PreflightCheck]:
    """Run all preflight checks and return the result list.

    ``engine`` and ``session`` are injectable for testing. When None the
    functions fall back to the live engine / SessionLocal.

    Alembic head and embeddings checks are skipped when database is unreachable
    to avoid cascading errors.

    ``live_embed`` (default False) gates a single tiny live Voyage embed call in
    check_embed_client; it costs tokens and must be opted into explicitly.
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

    # Construction probe catches the runtime-reality failures (missing httpx,
    # broken native import chain) that key presence alone cannot.
    checks.append(check_embed_client(live=live_embed))

    checks.append(check_enable_reranking())
    checks.append(voyage_rate_limit_note())

    return checks
