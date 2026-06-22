"""One-time repair: decode RFC 2047 encoded-word subjects in existing DB rows.

Finds all message rows whose subject column still contains raw RFC 2047
encoded-word sequences (i.e. subject LIKE '%=?%') and updates them with the
decoded Unicode value.

This is safe to run multiple times — already-decoded rows are not touched
(they will not match LIKE '%=?%').

Usage:
    python scripts/repair_encoded_subjects.py --dry-run   # preview, no writes
    python scripts/repair_encoded_subjects.py --confirm   # live update
    python scripts/repair_encoded_subjects.py --mailbox-id <uuid> --confirm
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._env import load_local_env

load_local_env()

from sqlalchemy import text  # noqa: E402 — after env load

from services.db.engine import SessionLocal  # noqa: E402
from services.ingest.normalize.mime import decode_mime_words  # noqa: E402


def repair(
    *,
    mailbox_id: str | None = None,
    dry_run: bool = True,
    batch_size: int = 500,
) -> dict[str, int]:
    """Decode RFC 2047 subjects in the DB. Returns stats dict.

    Parameters
    ----------
    mailbox_id : optional UUID — restrict to one mailbox
    dry_run    : when True, no writes are made
    batch_size : rows fetched per SELECT batch to keep memory bounded
    """
    session = SessionLocal()
    stats = {"scanned": 0, "updated": 0, "unchanged": 0}

    try:
        mid_clause = "AND mailbox_id = :mid" if mailbox_id else ""
        params: dict = {"mid": mailbox_id} if mailbox_id else {}

        # Fetch only rows with encoded-word sequences.
        rows = session.execute(
            text(
                f"SELECT id, subject FROM message "
                f"WHERE subject LIKE '%=?%' {mid_clause} "
                f"ORDER BY id "
                f"LIMIT :limit"
            ),
            {**params, "limit": batch_size},
        ).all()

        while rows:
            stats["scanned"] += len(rows)
            updates: list[dict] = []
            for row_id, raw_subject in rows:
                decoded = decode_mime_words(raw_subject or "")
                if decoded != raw_subject:
                    updates.append({"id": row_id, "subject": decoded})
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1

            if updates and not dry_run:
                session.execute(
                    text("UPDATE message SET subject = :subject WHERE id = :id"),
                    updates,
                )
                session.commit()

            if len(rows) < batch_size:
                break

            last_id = rows[-1][0]
            rows = session.execute(
                text(
                    f"SELECT id, subject FROM message "
                    f"WHERE subject LIKE '%=?%' {mid_clause} "
                    f"AND id > :last_id "
                    f"ORDER BY id "
                    f"LIMIT :limit"
                ),
                {**params, "last_id": last_id, "limit": batch_size},
            ).all()

    finally:
        session.close()

    return stats


def main() -> None:
    p = argparse.ArgumentParser(description="Repair RFC 2047 encoded subjects in the DB.")
    p.add_argument("--mailbox-id", default=None, metavar="UUID",
                   help="Restrict to one mailbox (default: all mailboxes).")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview updates without writing.")
    p.add_argument("--confirm", action="store_true",
                   help="Required to perform live writes (ignored when --dry-run).")
    p.add_argument("--batch-size", type=int, default=500, metavar="N",
                   help="Rows per SELECT batch (default: 500).")
    args = p.parse_args()

    live = not args.dry_run and args.confirm
    if not args.dry_run and not args.confirm:
        print("ERROR: pass --dry-run to preview, or --confirm to write.", file=sys.stderr)
        sys.exit(1)

    mode = "dry-run" if not live else "LIVE"
    mid_note = f" (mailbox {args.mailbox_id})" if args.mailbox_id else " (all mailboxes)"
    print(f"Repair encoded subjects — {mode}{mid_note}")

    stats = repair(
        mailbox_id=args.mailbox_id,
        dry_run=not live,
        batch_size=args.batch_size,
    )

    print(f"  scanned : {stats['scanned']}")
    print(f"  updated : {stats['updated']}" + (" (dry-run — no writes)" if not live else ""))
    print(f"  unchanged: {stats['unchanged']} (already decoded or no change)")

    if live:
        print("Done.")
    else:
        print("Dry-run complete. Re-run with --confirm to apply.")


if __name__ == "__main__":
    main()
