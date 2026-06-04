"""Seed the dev DB with the fixture mailbox and (optionally) start the API.

Usage:
    python scripts/dev_seed.py            # seed + print mailbox_id
    python scripts/dev_seed.py --serve    # seed then run uvicorn on :8000

Idempotent: re-running re-uses the mailbox with owner_email 'alex@acme.com'.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev",
)

from sqlalchemy import select  # noqa: E402

from services.db.engine import SessionLocal  # noqa: E402
from services.db import models as orm  # noqa: E402
from services.db.store import persist_l0, persist_l1  # noqa: E402
from services.enrich.clustering.params import FIXTURE_PARAMS  # noqa: E402
from services.enrich.clustering.testkit import FakeNlp, make_test_embed  # noqa: E402
from services.enrich.pipeline import run_enrichment  # noqa: E402
from services.ingest.params import IngestParams  # noqa: E402
from services.ingest.pipeline import IngestConfig, run_ingest  # noqa: E402

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]
FIXTURE = ROOT / "fixtures" / "mailbox.json"


def get_or_create_mailbox(session) -> str:
    existing = session.execute(
        select(orm.Mailbox).where(orm.Mailbox.owner_email == OWNER_EMAIL)
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing.id)
    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=OWNER_EMAIL,
        status="active",
        embed_model="dev-none",
        embed_dim=0,
        config={
            "internal_domains": INTERNAL_DOMAINS,
            "legal_domains": ["morrislaw.com"],
            "hr_senders": ["hr@acme.com"],
        },
    )
    session.add(mbx)
    session.commit()
    return str(mbx.id)


def seed() -> str:
    session = SessionLocal()
    try:
        mailbox_id = get_or_create_mailbox(session)

        params = IngestParams(
            legal_domains=["morrislaw.com"],
            hr_senders=["hr@acme.com"],
            personal_domains=[],
        )
        cfg = IngestConfig(
            provider="fixture",
            mailbox_path=FIXTURE,
            owner_email=OWNER_EMAIL,
            internal_domains=INTERNAL_DOMAINS,
            params=params,
        )
        store = run_ingest(cfg)
        persist_l0(store, mailbox_id, session)

        # Clustering uses deterministic testkit fakes and FIXTURE_PARAMS (the same
        # params that pass the eval gates), so the Projects tab shows meaningful
        # multi-thread clusters, not near-all-singletons from production defaults.
        result = run_enrichment(
            store.messages, owner_email=OWNER_EMAIL, internal_domains=INTERNAL_DOMAINS,
            threads=store.threads,
            embed_fn=make_test_embed(),
            nlp=FakeNlp(),
            cluster_params=FIXTURE_PARAMS,
        )
        persist_l1(result, mailbox_id, session)

        # Set owner_person_id (spec 04 §3).
        owner_pid = next(
            (i.person_id for i in result.identities if i.email == OWNER_EMAIL), None
        )
        if owner_pid:
            mbx = session.get(orm.Mailbox, mailbox_id)
            mbx.owner_person_id = owner_pid
            session.commit()

        n_projects = len(result.clustering.projects) if result.clustering else 0
        print(f"Seeded mailbox_id={mailbox_id}")
        print(f"  messages={len(store.messages)} threads={len(store.threads)}")
        print(f"  people={len(result.people)} edges={len(result.edges)}")
        print(f"  projects={n_projects}")
        return mailbox_id
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="start uvicorn after seeding")
    args = parser.parse_args()

    seed()

    if args.serve:
        import uvicorn

        uvicorn.run("services.api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
