"""Idempotent embedding backfill for a single mailbox (S7.5, D12).

Usage:
    python scripts/embed_backfill.py --mailbox-id <uuid> [--batch-size 64]
        [--model voyage-4] [--dry-run] [--confirm]

Selects all non-noise, non-sensitive messages whose embedding is absent or
whose content_hash has changed, then upserts embeddings into message_embedding.

Content hash is SHA-256 of subject + "\\n\\n" + clean_text.  Re-running on an
already-embedded mailbox with unchanged content exits zero and makes no API
calls (idempotent, spec S7.5 acceptance).

Privacy: only subject + clean_text is sent to the embedding API.  Structured
address fields are never transmitted (D12d).
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from services.db import models as orm  # noqa: E402
from services.retrieval.embed_client import EmbedClient, EmbedError  # noqa: E402


_COST_PER_1K_TOKENS = 0.000006  # voyage-4 approximate; for dry-run estimate only
_CHARS_PER_TOKEN    = 4          # rough heuristic for token count estimation


class _DryRunEmbedClient:
    """Sentinel embed client for --dry-run mode.

    Carries model/dim so run_backfill() can fetch existing hashes and report
    correct stats without needing VOYAGE_API_KEY.  embed_documents() raises
    loudly if called — that would be a bug in the dry-run path.
    """

    def __init__(self, model: str, dim: int = 1024) -> None:
        self._model = model
        self._dim   = dim

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embed_documents called during --dry-run (this is a bug)")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embed_query called during --dry-run (this is a bug)")


def content_hash(subject: str, clean_text: str) -> str:
    """SHA-256 of subject + '\\n\\n' + clean_text (D12 spec, S7.5)."""
    payload = subject + "\n\n" + clean_text
    return hashlib.sha256(payload.encode()).hexdigest()


def _fetch_candidates(session, mailbox_id: str) -> list[dict]:
    """All non-noise, non-sensitive messages for this mailbox, in ts order."""
    rows = session.execute(
        text("""
            SELECT id::text, subject, clean_text
            FROM message
            WHERE mailbox_id = :mid
              AND noise = false
              AND sensitivity = '{none}'
            ORDER BY ts, id
        """),
        {"mid": mailbox_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _fetch_existing_hashes(session, mailbox_id: str, model: str) -> dict[str, str]:
    """Map {message_id: content_hash} for rows already in message_embedding."""
    rows = session.execute(
        text("""
            SELECT message_id::text, content_hash
            FROM message_embedding
            WHERE mailbox_id = :mid
              AND embed_model = :model
        """),
        {"mid": mailbox_id, "model": model},
    ).all()
    return {str(r[0]): r[1] for r in rows}


def _upsert_batch(
    session,
    *,
    mailbox_id: str,
    model: str,
    dim: int,
    items: list[dict],
    embeddings: list[list[float]],
) -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "mailbox_id":   mailbox_id,
            "message_id":   item["id"],
            "embed_model":  model,
            "embed_dim":    dim,
            "content_hash": item["content_hash"],
            "embedded_at":  now,
            "embedding":    emb,
        }
        for item, emb in zip(items, embeddings)
    ]
    stmt = pg_insert(orm.MessageEmbedding).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["message_id", "embed_model"],
        set_={
            "embed_dim":    stmt.excluded.embed_dim,
            "content_hash": stmt.excluded.content_hash,
            "embedded_at":  stmt.excluded.embedded_at,
            "embedding":    stmt.excluded.embedding,
        },
    )
    session.execute(stmt)
    session.commit()


def run_backfill(
    *,
    mailbox_id: str,
    embed_client: EmbedClient,
    batch_size: int = 64,
    dry_run: bool = False,
    confirm: bool = False,
    session=None,
) -> dict:
    """Core backfill logic; returns a stats dict.

    ``session`` is injectable for tests.  When None, the function creates and
    closes its own DB session.  The caller owns a passed-in session.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    own_session = session is None
    if own_session:
        from services.db.engine import SessionLocal
        session = SessionLocal()

    try:
        candidates = _fetch_candidates(session, mailbox_id)
        existing   = _fetch_existing_hashes(session, mailbox_id, embed_client.model)

        for c in candidates:
            c["content_hash"] = content_hash(c["subject"], c["clean_text"])

        to_embed = [
            c for c in candidates
            if existing.get(c["id"]) != c["content_hash"]
        ]

        total_chars  = sum(len(c["subject"]) + len(c["clean_text"]) for c in to_embed)
        est_tokens   = total_chars // _CHARS_PER_TOKEN
        est_cost_usd = (est_tokens / 1000) * _COST_PER_1K_TOKENS

        stats: dict = {
            "total_messages":   len(candidates),
            "already_embedded": len(candidates) - len(to_embed),
            "to_embed":         len(to_embed),
            "est_tokens":       est_tokens,
            "est_cost_usd":     est_cost_usd,
            "model":            embed_client.model,
            "embedded":         0,  # updated below; always present so callers never need .get()
        }

        log.info(
            "backfill plan: total=%d already_embedded=%d to_embed=%d "
            "est_tokens=%d est_cost_usd=%.4f model=%s",
            stats["total_messages"], stats["already_embedded"], stats["to_embed"],
            stats["est_tokens"], stats["est_cost_usd"], stats["model"],
        )

        if dry_run:
            log.info("--dry-run: no writes performed.")
            return stats

        if not confirm and to_embed:
            answer = input(
                f"\nEmbed {len(to_embed)} messages (~{est_tokens:,} tokens, "
                f"~${est_cost_usd:.4f} estimated). Proceed? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                log.info("Aborted by user.")
                stats["aborted"] = True
                return stats

        embedded_count = 0
        for batch_num, start in enumerate(range(0, len(to_embed), batch_size), 1):
            batch = to_embed[start : start + batch_size]
            texts = [c["subject"] + "\n\n" + c["clean_text"] for c in batch]

            t0 = time.monotonic()
            embeddings = embed_client.embed_documents(texts)
            elapsed = time.monotonic() - t0

            # Validate before touching the DB: a length mismatch or wrong vector
            # dimension here means the client is broken, not the data.
            if len(embeddings) != len(batch):
                raise EmbedError(
                    f"batch {batch_num}: embed_documents returned {len(embeddings)} "
                    f"vectors for {len(batch)} texts"
                )
            for vec_idx, emb in enumerate(embeddings):
                if len(emb) != embed_client.dim:
                    raise EmbedError(
                        f"batch {batch_num} index {vec_idx}: vector length "
                        f"{len(emb)} != embed_dim {embed_client.dim}"
                    )

            _upsert_batch(
                session,
                mailbox_id=mailbox_id,
                model=embed_client.model,
                dim=embed_client.dim,
                items=batch,
                embeddings=embeddings,
            )

            embedded_count += len(batch)
            stats["embedded"] = embedded_count
            log.info(
                "batch %d: %d messages in %.1fs (total %d/%d)",
                batch_num, len(batch), elapsed, embedded_count, len(to_embed),
            )

        return stats

    finally:
        if own_session:
            session.close()


def _build_voyage_client(model: str) -> EmbedClient:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise SystemExit(
            "VOYAGE_API_KEY is not set.\n"
            "Export it before running the backfill, or use FakeEmbedClient "
            "by calling run_backfill() directly in tests/scripts."
        )
    from services.retrieval.embed_client import VoyageEmbedClient
    return VoyageEmbedClient(api_key=key, model=model)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Embed all non-noise, non-sensitive messages for a mailbox."
    )
    parser.add_argument("--mailbox-id", required=True, metavar="UUID",
                        help="UUID of the mailbox to backfill.")
    parser.add_argument("--batch-size", type=int, default=64, metavar="N",
                        help="Number of messages per embedding API call (default 64).")
    parser.add_argument("--model", default="voyage-4", metavar="MODEL",
                        help="Voyage AI model name (default voyage-4).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counts and estimated cost; write nothing.")
    parser.add_argument("--confirm", action="store_true",
                        help="Skip the interactive confirmation prompt.")
    args = parser.parse_args(argv)

    # Dry-run never calls the API, so it must not require VOYAGE_API_KEY.
    if args.dry_run:
        client: EmbedClient = _DryRunEmbedClient(model=args.model)
    else:
        client = _build_voyage_client(args.model)

    run_backfill(
        mailbox_id=args.mailbox_id,
        embed_client=client,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        confirm=args.confirm,
    )


if __name__ == "__main__":
    main()
