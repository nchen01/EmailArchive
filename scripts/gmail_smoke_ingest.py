"""Controlled Gmail smoke ingest — real-mailbox runner (production-hardening-demo).

Runs L0 ingest + L1 identity/graph/role enrichment against a real Gmail mailbox.
Designed for a controlled demo or test on a single mailbox before any customer data.

NOTE: Project clustering is NOT run by this script. Clustering requires a
production embedding model (deferred, D11/spec 04 ticket 4.5). This runner
produces identity resolution, relationship graph, and role inference only.

BEFORE RUNNING
--------------
1. Create a Google Cloud project, enable the Gmail API, create OAuth credentials
   (Desktop app). Run the OAuth consent flow to obtain a token with the
   gmail.readonly scope. Store the result as JSON in an env var (never on disk):

     export GMAIL_TOKEN='{"token": "ya29...", "refresh_token": "1//0g...",
       "token_uri": "https://oauth2.googleapis.com/token",
       "client_id": "<id>.apps.googleusercontent.com",
       "client_secret": "<secret>",
       "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}'

   On subsequent runs with a known mailbox UUID:
     export GMAIL_TOKEN_<uuid>='...'

2. Tokens are read only from env vars. They are NEVER written to the DB,
   logs, or stdout. The audit trail logs the OAuth subject (client_id prefix),
   not the token value.

OAUTH SCOPE
-----------
Only gmail.readonly is requested. Single mailbox. No write/delete/send.

USAGE
-----
  # Smoke-check: fetch one message, print metadata, persist nothing.
  python scripts/gmail_smoke_ingest.py --owner-email you@example.com --confirm --smoke-check

  # First run (prints the mailbox UUID; default cap 200):
  python scripts/gmail_smoke_ingest.py --owner-email you@example.com --max-messages 200 --confirm

  # Dry-run: fetch + normalize + print summary, persist nothing.
  python scripts/gmail_smoke_ingest.py --owner-email you@example.com --dry-run --confirm

  # Incremental (uses stored historyId automatically):
  python scripts/gmail_smoke_ingest.py --mailbox-id <uuid> --owner-email you@example.com --confirm

  # Show raw body excerpt in smoke-check (opt-in):
  python scripts/gmail_smoke_ingest.py --owner-email you@example.com --confirm --smoke-check --show-body
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev",
)

import structlog  # noqa: E402

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_token_dict(mailbox_id: str) -> dict:
    """Read the Gmail token JSON from GMAIL_TOKEN_<mailbox_id> or GMAIL_TOKEN.

    The token is kept in memory only — never written to DB, logs, or stdout.
    """
    raw = os.environ.get(f"GMAIL_TOKEN_{mailbox_id}") or os.environ.get("GMAIL_TOKEN")
    if not raw:
        sys.exit(
            f"ERROR: Set GMAIL_TOKEN_{mailbox_id} (or GMAIL_TOKEN) to the OAuth token JSON.\n"
            "See the docstring at the top of this script for the required format.\n"
            "Required fields: token, refresh_token, token_uri, client_id, client_secret, scopes."
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: token env var is not valid JSON: {exc}")


def _build_credentials(token_dict: dict):
    """Construct a google.oauth2.credentials.Credentials from the token dict."""
    from google.oauth2.credentials import Credentials
    required = {"token", "refresh_token", "token_uri", "client_id", "client_secret"}
    missing = required - token_dict.keys()
    if missing:
        sys.exit(f"ERROR: token JSON is missing required fields: {missing}")
    if "gmail.readonly" not in " ".join(token_dict.get("scopes", [])):
        log.warning("scope_check", warning="gmail.readonly not found in scopes list")
    return Credentials(
        token=token_dict["token"],
        refresh_token=token_dict["refresh_token"],
        token_uri=token_dict["token_uri"],
        client_id=token_dict["client_id"],
        client_secret=token_dict["client_secret"],
        scopes=token_dict.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
    )


def _oauth_subject(token_dict: dict) -> str:
    """A safe, non-secret identifier for audit logging (not the token itself)."""
    cid = token_dict.get("client_id", "unknown")
    return f"client:{cid[:20]}"


def _get_or_create_mailbox(session, owner_email: str, mailbox_id_arg: str | None) -> str:
    from services.db import models as orm
    from sqlalchemy import select

    if mailbox_id_arg:
        mbx = session.get(orm.Mailbox, mailbox_id_arg)
        if mbx is None:
            sys.exit(f"ERROR: No mailbox with id={mailbox_id_arg} in the database.")
        log.info("using_existing_mailbox", mailbox_id=mailbox_id_arg)
        return mailbox_id_arg

    existing = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == owner_email)
    ).scalars().first()
    if existing:
        log.info("reusing_existing_mailbox", mailbox_id=str(existing.id))
        return str(existing.id)

    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=owner_email,
        status="active",
        embed_model="deferred",
        embed_dim=0,
        config={},
    )
    session.add(mbx)
    session.commit()
    log.info("created_mailbox", mailbox_id=str(mbx.id))
    return str(mbx.id)


# ── smoke check ───────────────────────────────────────────────────────────────

def _smoke_check(provider, owner_email: str, show_body: bool) -> int:
    """Fetch exactly one message, print sanitized metadata. Persist nothing.

    Returns the number of messages actually fetched (0 or 1) so the caller
    can write an accurate message_count to the audit log.
    Uses islice so the provider never paginates past the first result.
    Body excerpt is opt-in (--show-body) to avoid leaking third-party content.
    """
    log.info("smoke_check_start")
    ids = list(itertools.islice(provider.list_ids(None), 1))
    if not ids:
        log.info("smoke_check_result", status="mailbox_empty")
        return 0

    raw = provider.fetch(ids[0])

    from services.ingest.normalize.address import parse_addresses
    from services.ingest.normalize.body import clean_body_from_raw
    from services.ingest.normalize.noise import is_noise
    from services.ingest.params import IngestParams

    params = IngestParams()
    senders = parse_addresses(raw.headers.get("From", ""))
    sender_email = senders[0].email if senders else "(unknown)"
    clean_text = clean_body_from_raw(raw, sender_email, params.clean_text_max_chars)
    noise = is_noise(raw, sender_email)

    log.info(
        "smoke_check_result",
        provider_id=raw.provider_id,
        # Subject may contain sensitive/personal content; log only its length.
        # Pass --show-body to print body excerpt (also opt-in).
        subject_chars=len(raw.headers.get("Subject", "")),
        sender_domain=sender_email.split("@")[-1] if "@" in sender_email else "(none)",
        date=raw.headers.get("Date", "")[:10],  # date only, no time zone details
        clean_text_chars=len(clean_text),
        noise=noise,
        mime_parts=[p.type for p in raw.mime_parts],
        attachment_count=len(raw.precomputed_attachments),
    )

    if show_body:
        print("\n--- Normalized body excerpt (first 200 chars; opt-in via --show-body) ---")
        print(clean_text[:200])
        print("--- End ---")
    else:
        print(f"\nSmoke check OK. {len(clean_text)} chars of clean text. Pass --show-body to see excerpt.")
    return 1


# ── main ──────────────────────────────────────────────────────────────────────

def _run_post_start(args, provider, session, mailbox_id, actor, started_at, since_token, ingest_params):
    """All work that happens after the audit-start row is written.

    Extracted so that main() can wrap it in a try/except and guarantee an
    "ingest_error" audit row if anything here raises.
    """
    from services.db.store import (
        persist_l0,
        persist_l1,
        save_sync_token,
        write_audit_event,
    )

    # ── Smoke check ────────────────────────────────────────────────────
    if args.smoke_check:
        fetched = _smoke_check(provider, args.owner_email, show_body=args.show_body)
        write_audit_event(
            session, mailbox_id=mailbox_id, actor=actor, action="ingest_finish",
            scope="gmail.readonly", message_count=fetched,
            started_at=started_at, finished_at=datetime.now(timezone.utc),
        )
        return

    # ── Fetch N+1 IDs to distinguish exact-fit from truncated run ─────
    log.info("fetch_start", max_messages=args.max_messages, since_token=bool(since_token))
    raw_ids = list(itertools.islice(provider.list_ids(since_token), args.max_messages + 1))
    hit_cap = len(raw_ids) > args.max_messages
    ids = raw_ids[: args.max_messages]
    log.info("ids_fetched", count=len(ids), hit_cap=hit_cap)

    raws = [provider.fetch(id_) for id_ in ids]
    new_sync_token = provider.sync_token()

    # ── Normalize ─────────────────────────────────────────────────────
    from services.ingest.normalize.threads import reconstruct
    from services.ingest.store import persist as ingest_persist

    messages, threads = reconstruct(raws, args.owner_email, ingest_params, mailbox_id)
    store = ingest_persist(messages, threads)

    noise_count = sum(1 for m in store.messages if m.noise)
    sensitivity_counts: dict[str, int] = {}
    for m in store.messages:
        for s in m.sensitivity:
            sensitivity_counts[s.value] = sensitivity_counts.get(s.value, 0) + 1

    log.info(
        "normalize_complete",
        messages=len(store.messages),
        threads=len(store.threads),
        noise_flagged=noise_count,
        sensitivity=sensitivity_counts,
    )

    # ── Dry-run exit ───────────────────────────────────────────────────
    if args.dry_run:
        write_audit_event(
            session, mailbox_id=mailbox_id, actor=actor, action="ingest_finish",
            scope="gmail.readonly", message_count=len(store.messages),
            started_at=started_at, finished_at=datetime.now(timezone.utc),
        )
        print(
            f"\nDry-run complete — {len(store.messages)} messages normalized, "
            f"{noise_count} noise-flagged. Nothing persisted."
        )
        return

    # ── Persist L0 ─────────────────────────────────────────────────────
    persist_l0(store, mailbox_id, session, replace_snapshot=False)
    log.info("l0_persisted", messages=len(store.messages), threads=len(store.threads))

    # ── Enrich L1 (identity + graph + roles; clustering deferred) ──────
    from services.enrich.params import EnrichParams
    from services.enrich.pipeline import run_enrichment

    result = run_enrichment(
        store.messages,
        owner_email=args.owner_email,
        internal_domains=args.internal_domains,
        params=EnrichParams(),
        # threads intentionally omitted → clustering + event extraction skipped
    )
    persist_l1(result, mailbox_id, session)
    log.info("l1_persisted", people=len(result.people), edges=len(result.edges))

    # Update owner_person_id.
    from services.db import models as orm
    owner_pid = next(
        (i.person_id for i in result.identities if i.email == args.owner_email.lower()), None
    )
    if owner_pid:
        mbx = session.get(orm.Mailbox, mailbox_id)
        if mbx and not mbx.owner_person_id:
            mbx.owner_person_id = owner_pid
            session.commit()

    # ── Sync token: save only when uncapped AND non-empty ─────────────
    # GmailProvider.sync_token() returns "" when no historyId was captured
    # (e.g. the mailbox returned no messages). Saving "" would be treated as
    # a valid incremental token on the next run.
    token_to_save = new_sync_token if (new_sync_token and not hit_cap) else None
    if hit_cap:
        log.warning(
            "sync_token_not_saved",
            reason="run hit --max-messages cap; snapshot may be incomplete",
            hint="re-run without --max-messages or with a higher cap to enable incremental",
        )
    elif not new_sync_token:
        log.warning("sync_token_not_saved", reason="provider returned no historyId")
    else:
        save_sync_token(session, mailbox_id, new_sync_token)
        log.info("sync_token_saved", preview=new_sync_token[:16] + "...")

    # ── Audit finish ───────────────────────────────────────────────────
    write_audit_event(
        session, mailbox_id=mailbox_id, actor=actor, action="ingest_finish",
        scope="gmail.readonly", message_count=len(store.messages),
        sync_token=token_to_save,
        started_at=started_at, finished_at=datetime.now(timezone.utc),
    )

    print(f"""
╔══════════════════════════════════════════════════════╗
║  Gmail Smoke Ingest — Complete                       ║
╠══════════════════════════════════════════════════════╣
  mailbox_id      : {mailbox_id}
  owner           : {args.owner_email}
  messages        : {len(store.messages)} ({noise_count} noise-flagged)
  threads         : {len(store.threads)}
  sensitivity     : {sensitivity_counts}
  people          : {len(result.people)}
  edges           : {len(result.edges)}
  clustering      : deferred (no embedding model configured)
  sync_token      : {"NOT saved (capped run)" if hit_cap else new_sync_token[:16] + "..."}
  persisted       : YES (L0 + L1 identity/graph/roles)
╚══════════════════════════════════════════════════════╝
""")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Controlled Gmail smoke ingest (L0 + L1 identity/graph/roles).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--owner-email", required=True)
    p.add_argument("--mailbox-id", default=None)
    p.add_argument("--internal-domains", nargs="*", default=[])
    p.add_argument(
        "--max-messages", type=int, default=200,
        help="Hard cap on messages fetched per run (default 200, minimum 1). "
             "If Gmail has more messages than the cap, the sync token is NOT saved "
             "to avoid marking an incomplete snapshot as incremental-ready.",
    )
    p.add_argument("--since-token", default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch + normalize + print, persist nothing. Audit log is still written.")
    p.add_argument("--smoke-check", action="store_true",
                   help="Fetch one message, print metadata, persist nothing. Audit log is still written.")
    p.add_argument("--show-body", action="store_true",
                   help="In --smoke-check mode, print a short body excerpt (opt-in; may show personal content).")
    p.add_argument(
        "--confirm", action="store_true",
        help="Required. Acknowledges authorization to access this mailbox and "
             "that third-party personal data will be processed per the project's "
             "privacy policy (implementation-plan.md §7).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.max_messages is not None and args.max_messages < 1:
        sys.exit("ERROR: --max-messages must be >= 1.")

    if not args.confirm:
        sys.exit(
            "ERROR: --confirm is required.\n"
            "By passing --confirm you acknowledge that you hold authorization to access\n"
            "this mailbox and that third-party personal data will be processed per\n"
            "the project's privacy guidelines (see docs/implementation-plan.md §7)."
        )

    # ── Token handling (never logged) ──────────────────────────────────────
    mailbox_id_for_token = args.mailbox_id or "default"
    token_dict = _get_token_dict(mailbox_id_for_token)
    creds = _build_credentials(token_dict)
    actor = _oauth_subject(token_dict)

    from services.db.engine import SessionLocal
    from services.db.store import (
        load_sync_token,
        persist_l0,
        persist_l1,
        save_sync_token,
        write_audit_event,
    )

    session = SessionLocal()
    try:
        mailbox_id = _get_or_create_mailbox(session, args.owner_email, args.mailbox_id)

        since_token = args.since_token
        if since_token is None and not args.smoke_check:
            since_token = load_sync_token(session, mailbox_id)
            if since_token:
                log.info("incremental_sync", using_stored_token=True)
            else:
                log.info("full_fetch", reason="no stored sync token")

        # ── Build provider ─────────────────────────────────────────────────
        from services.ingest.params import IngestParams
        from services.ingest.providers.gmail import GmailProvider

        ingest_params = IngestParams(legal_domains=[], hr_senders=[])
        provider = GmailProvider(ingest_params, args.owner_email)
        provider.authorize({"credentials": creds})

        # ── Audit start — written BEFORE any Gmail data access ─────────────
        # This applies to ALL paths including --dry-run and --smoke-check,
        # because all modes read real mailbox data (spec 00 §12 / §16).
        started_at = datetime.now(timezone.utc)
        write_audit_event(
            session,
            mailbox_id=mailbox_id,
            actor=actor,
            action="ingest_start",
            scope="gmail.readonly",
            started_at=started_at,
        )
        log.info("audit_start_written", actor=actor)

        # ── All post-start work wrapped: failures write audit "ingest_error" ─
        try:
            _run_post_start(
                args, provider, session, mailbox_id, actor, started_at,
                since_token, ingest_params,
            )
        except Exception as exc:
            err_type = type(exc).__name__
            # Log only the error category, not the message string — exception
            # messages from Gmail/Google APIs routinely contain email addresses,
            # API response bodies, and request URLs that may include mailbox data.
            log.error("ingest_failed", error_type=err_type,
                      hint="check stderr or --dry-run for context; message withheld for privacy")
            try:
                write_audit_event(
                    session,
                    mailbox_id=mailbox_id,
                    actor=actor,
                    action="ingest_error",
                    scope="gmail.readonly",
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                )
            except Exception:
                # Swallow — original exception is more important.
                pass
            # Print the error type to stderr for the operator; avoid stdout
            # which may be piped to logs. Full traceback goes to sys.stderr
            # via Python's default uncaught-exception handler if we re-raise,
            # but we sys.exit to keep the exit code predictable.
            sys.exit(f"FAILED ({err_type}) — see structured log above. Re-run with --dry-run to diagnose.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
