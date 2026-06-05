"""Controlled Gmail smoke ingest — real-mailbox runner (production-hardening-demo).

Runs the full L0→L1 pipeline against a real Gmail mailbox. Designed for a
controlled demo on a single test or personal mailbox before any customer data.

BEFORE RUNNING
--------------
1. Create a Google Cloud project, enable the Gmail API, create OAuth credentials
   (Desktop app or Service Account).
2. Run the OAuth consent flow to obtain a token with the gmail.readonly scope.
   Store the resulting token JSON in the environment variable (never on disk):

     export GMAIL_TOKEN_<mailbox_id>='{"token": "...", "refresh_token": "...",
       "token_uri": "https://oauth2.googleapis.com/token",
       "client_id": "...", "client_secret": "...",
       "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}'

   The mailbox_id here is the DB UUID returned by this script on first run.
   On first run you can use any placeholder; the script will create the mailbox
   row and print the UUID to re-use.

3. The token string must never appear in DB, logs, or stdout.  The script reads
   it only from the env var and passes it to Google's SDK at runtime.

OAUTH SCOPE
-----------
Only `https://www.googleapis.com/auth/gmail.readonly` is requested.  The scope
is single-mailbox, read-only.  No write, delete, or send permissions are used.

USAGE
-----
  # Smoke-check: fetch one message, normalize, print, persist nothing.
  python scripts/gmail_smoke_ingest.py \\
      --owner-email you@example.com \\
      --confirm --smoke-check

  # First full run (creates a mailbox row; prints its UUID for future runs).
  python scripts/gmail_smoke_ingest.py \\
      --owner-email you@example.com \\
      --max-messages 200 \\
      --confirm

  # Incremental run (uses stored historyId automatically).
  python scripts/gmail_smoke_ingest.py \\
      --mailbox-id <uuid> \\
      --owner-email you@example.com \\
      --confirm

  # Dry-run: fetch + normalize, print summary, persist nothing.
  python scripts/gmail_smoke_ingest.py \\
      --owner-email you@example.com \\
      --max-messages 50 \\
      --confirm --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
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


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_token_env(mailbox_id: str) -> dict:
    """Read the Gmail token JSON from GMAIL_TOKEN_<mailbox_id>.

    The token is read from the environment and kept in memory only — it is never
    written to the DB, logs, or stdout.
    """
    key = f"GMAIL_TOKEN_{mailbox_id}"
    raw = os.environ.get(key)
    if not raw:
        # Also try the generic key for first runs before the UUID is known.
        raw = os.environ.get("GMAIL_TOKEN")
    if not raw:
        sys.exit(
            f"ERROR: Set {key} (or GMAIL_TOKEN) to the OAuth token JSON.\n"
            "See the docstring at the top of this script for the required format."
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: {key} is not valid JSON: {exc}")


def _get_or_create_mailbox(session, owner_email: str, mailbox_id_arg: str | None) -> str:
    from services.db import models as orm
    from sqlalchemy import select

    if mailbox_id_arg:
        mbx = session.get(orm.Mailbox, mailbox_id_arg)
        if mbx is None:
            sys.exit(f"ERROR: No mailbox with id={mailbox_id_arg} in the database.")
        log.info("using_existing_mailbox", mailbox_id=mailbox_id_arg, owner=owner_email)
        return mailbox_id_arg

    # Look for an existing mailbox with this owner.
    existing = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == owner_email)
    ).scalars().first()
    if existing:
        log.info("reusing_existing_mailbox", mailbox_id=str(existing.id), owner=owner_email)
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
    log.info("created_mailbox", mailbox_id=str(mbx.id), owner=owner_email)
    return str(mbx.id)


def _get_oauth_subject(token_dict: dict) -> str:
    """Extract the OAuth subject for audit logging (email, not token value)."""
    # google-auth may expose the email in id_token or as a hint — use client_id
    # prefix as a fallback identifier that doesn't leak the token.
    return token_dict.get("client_email") or f"client:{token_dict.get('client_id', 'unknown')[:12]}"


# ── smoke check ──────────────────────────────────────────────────────────────

def _smoke_check(provider, owner_email: str) -> None:
    """Fetch exactly one message, normalize, print sanitized metadata, persist nothing."""
    log.info("smoke_check_start", fetching="one message")
    ids = list(provider.list_ids(None))
    if not ids:
        log.info("smoke_check_result", status="mailbox_empty")
        return
    raw = provider.fetch(ids[0])
    from services.ingest.normalize.address import parse_addresses
    from services.ingest.normalize.body import clean_body_from_raw
    from services.ingest.normalize.noise import is_noise
    from services.ingest.params import IngestParams
    params = IngestParams()
    sender_raw = raw.headers.get("From", "")
    senders = parse_addresses(sender_raw)
    sender_email = senders[0].email if senders else "(unknown)"
    clean_text = clean_body_from_raw(raw, sender_email, params.clean_text_max_chars)
    noise = is_noise(raw, sender_email)
    log.info(
        "smoke_check_result",
        provider_id=raw.provider_id,
        subject=raw.headers.get("Subject", "")[:80],
        sender_domain=sender_email.split("@")[-1] if "@" in sender_email else "(none)",
        date=raw.headers.get("Date", "")[:30],
        clean_text_chars=len(clean_text),
        noise=noise,
        mime_parts=[p.type for p in raw.mime_parts],
        attachment_count=len(raw.precomputed_attachments),
    )
    print("\n--- Normalized body (first 500 chars) ---")
    print(clean_text[:500])
    print("--- End ---")


# ── main ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Controlled Gmail smoke ingest — real-mailbox runner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--owner-email", required=True, help="Gmail address of the mailbox owner.")
    p.add_argument("--mailbox-id", default=None, help="Existing DB mailbox UUID (optional).")
    p.add_argument(
        "--internal-domains", nargs="*", default=[],
        help="Domains treated as 'internal' for role inference (e.g. acme.com)."
    )
    p.add_argument(
        "--max-messages", type=int, default=500,
        help="Hard cap on messages fetched per run (default 500; prevents accidental 50k runs).",
    )
    p.add_argument(
        "--since-token", default=None,
        help="Gmail historyId for incremental fetch. If omitted, uses stored token from sync_state.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and normalize but do NOT persist to the database.",
    )
    p.add_argument(
        "--smoke-check", action="store_true",
        help="Fetch exactly one message, normalize, print sanitized metadata, persist nothing.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help=(
            "Required. Confirms you hold authorization to access this mailbox "
            "and that third-party personal data will be processed per the project's "
            "privacy policy (implementation-plan.md §7)."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not args.confirm:
        sys.exit(
            "ERROR: --confirm is required.\n"
            "By passing --confirm you acknowledge that you hold authorization to access\n"
            "this mailbox and that third-party personal data will be processed per\n"
            "the project's privacy guidelines (see docs/implementation-plan.md §7)."
        )

    # Read token from env — never printed or logged.
    mailbox_id_for_token = args.mailbox_id or "default"
    token_dict = _get_token_env(mailbox_id_for_token)
    oauth_subject = _get_oauth_subject(token_dict)

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

        # Determine since_token: CLI flag > stored sync_state > None (full fetch).
        since_token = args.since_token
        if since_token is None and not args.smoke_check:
            since_token = load_sync_token(session, mailbox_id)
            if since_token:
                log.info("incremental_sync", using_stored_token=True)
            else:
                log.info("full_fetch", reason="no stored sync token")

        # Build and authorize the Gmail provider.
        from services.ingest.params import IngestParams
        from services.ingest.providers.gmail import GmailProvider
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=token_dict.get("token"),
            refresh_token=token_dict.get("refresh_token"),
            token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_dict.get("client_id"),
            client_secret=token_dict.get("client_secret"),
            scopes=token_dict.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
        )
        ingest_params = IngestParams(
            legal_domains=[],
            hr_senders=[],
        )
        provider = GmailProvider(ingest_params, args.owner_email)
        provider.authorize({"credentials": creds})

        # ── Smoke check path ──────────────────────────────────────────────
        if args.smoke_check:
            _smoke_check(provider, args.owner_email)
            return

        # ── Write audit start BEFORE any data is read (spec 00 §12) ──────
        started_at = datetime.now(timezone.utc)
        if not args.dry_run:
            write_audit_event(
                session,
                mailbox_id=mailbox_id,
                actor=oauth_subject,
                action="ingest_start",
                scope="gmail.readonly",
                started_at=started_at,
            )
            log.info("audit_start_written", actor=oauth_subject, action="ingest_start")

        # ── Fetch and normalize ───────────────────────────────────────────
        log.info("fetch_start", max_messages=args.max_messages, since_token=since_token)

        ids = list(provider.list_ids(since_token))
        total_available = len(ids)
        ids = ids[: args.max_messages]
        log.info("ids_fetched", available=total_available, capped_to=len(ids))

        raws = [provider.fetch(id_) for id_ in ids]
        new_sync_token = provider.sync_token()

        from services.ingest.normalize.threads import reconstruct
        from services.ingest.store import persist as ingest_persist

        messages, threads = reconstruct(raws, args.owner_email, ingest_params, mailbox_id)
        store = ingest_persist(messages, threads)

        # Noise / sensitivity distribution for observability.
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

        if args.dry_run:
            log.info(
                "dry_run_summary",
                messages=len(store.messages),
                threads=len(store.threads),
                noise=noise_count,
                sensitivity=sensitivity_counts,
                sync_token=new_sync_token,
                persisted=False,
            )
            print(f"\nDry-run complete — {len(store.messages)} messages normalized, nothing persisted.")
            return

        # ── Persist L0 ───────────────────────────────────────────────────
        persist_l0(store, mailbox_id, session, replace_snapshot=False)
        log.info("l0_persisted", messages=len(store.messages), threads=len(store.threads))

        # ── Enrich L1 ────────────────────────────────────────────────────
        from services.enrich.params import EnrichParams
        from services.enrich.pipeline import run_enrichment

        enrich_params = EnrichParams()
        result = run_enrichment(
            store.messages,
            owner_email=args.owner_email,
            internal_domains=args.internal_domains,
            params=enrich_params,
        )
        persist_l1(result, mailbox_id, session)
        log.info(
            "l1_persisted",
            people=len(result.people),
            edges=len(result.edges),
        )

        # Update owner_person_id if resolved.
        from services.db import models as orm
        from sqlalchemy import select
        owner_pid = next(
            (i.person_id for i in result.identities if i.email == args.owner_email.lower()),
            None,
        )
        if owner_pid:
            mbx = session.get(orm.Mailbox, mailbox_id)
            if mbx and not mbx.owner_person_id:
                mbx.owner_person_id = owner_pid
                session.commit()

        # ── Save sync token ───────────────────────────────────────────────
        save_sync_token(session, mailbox_id, new_sync_token)
        log.info("sync_token_saved", token_preview=new_sync_token[:12] + "...")

        # ── Write audit finish ────────────────────────────────────────────
        write_audit_event(
            session,
            mailbox_id=mailbox_id,
            actor=oauth_subject,
            action="ingest_finish",
            scope="gmail.readonly",
            message_count=len(store.messages),
            sync_token=new_sync_token,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

        # ── Print summary ─────────────────────────────────────────────────
        print(f"""
╔══════════════════════════════════════════════════════╗
║  Gmail Smoke Ingest — Complete                       ║
╠══════════════════════════════════════════════════════╣
  mailbox_id   : {mailbox_id}
  owner        : {args.owner_email}
  messages     : {len(store.messages)} ({noise_count} noise-flagged)
  threads      : {len(store.threads)}
  sensitivity  : {sensitivity_counts}
  people       : {len(result.people)}
  edges        : {len(result.edges)}
  sync_token   : {new_sync_token[:16]}...
  persisted    : YES
╚══════════════════════════════════════════════════════╝
""")

    finally:
        session.close()


if __name__ == "__main__":
    main()
