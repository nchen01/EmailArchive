"""S9 materialize_projects tests.

Offline unit tests cover the embedding bridge (_make_db_embed_fn) and import
safety.  DB-gated integration tests cover the full pipeline, idempotency,
noise/sensitive exclusion, sparse fallback, and cover-for-me routing.

DB-gated tests use the standard fixture mailbox (18 messages / 13 threads,
15 embeddable).  No voyage-4 embeddings are pre-loaded, so embed_fn returns
zero vectors for all messages.  Clustering still succeeds via participant,
keyword, and temporal signals — this explicitly tests the zero-vector fallback
path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
DATABASE_URL = os.environ.get("DATABASE_URL")
OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


def _db_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        from services.db.engine import engine
        with engine.connect():
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="DATABASE_URL not set or Postgres unreachable.",
)


# ── Offline unit tests ────────────────────────────────────────────────────────

def _fake_message(mid: str, clean_text: str):
    """Minimal ekc_schemas Message-like object for embed_fn tests."""
    obj = type("_Msg", (), {"id": mid, "clean_text": clean_text})()
    return obj


def test_make_db_embed_fn_returns_stored_vector():
    from scripts.materialize_projects import _make_db_embed_fn

    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    msg = _fake_message("msg-1", "project status update")
    fn = _make_db_embed_fn([msg], {"msg-1": vec})

    result = fn("project status update")
    np.testing.assert_array_equal(result, vec)


def test_make_db_embed_fn_zero_fallback():
    """Text not in lookup returns zero vector (message had no embedding)."""
    from scripts.materialize_projects import _make_db_embed_fn, _EMBED_DIM

    fn = _make_db_embed_fn([], {})
    result = fn("text with no stored vector")
    assert result.shape == (_EMBED_DIM,)
    assert np.all(result == 0.0)


def test_make_db_embed_fn_empty_clean_text():
    """Empty string is handled without KeyError."""
    from scripts.materialize_projects import _make_db_embed_fn, _EMBED_DIM

    vec = np.ones(_EMBED_DIM, dtype=np.float32)
    msg = _fake_message("msg-1", "")
    fn = _make_db_embed_fn([msg], {"msg-1": vec})

    result = fn("")
    np.testing.assert_array_equal(result, vec)

    result2 = fn(None)  # type: ignore[arg-type]
    np.testing.assert_array_equal(result2, vec)


def test_make_db_embed_fn_message_not_in_vec_dict():
    """Message present but its ID not in the vector dict → zero fallback."""
    from scripts.materialize_projects import _make_db_embed_fn, _EMBED_DIM

    msg = _fake_message("msg-no-vec", "some clean text")
    fn = _make_db_embed_fn([msg], {})  # msg-no-vec not in dict
    result = fn("some clean text")
    assert result.shape == (_EMBED_DIM,)
    assert np.all(result == 0.0)


def test_materialize_import_no_side_effects():
    """Importing materialize_projects must not call load_local_env or touch the DB."""
    import importlib, sys
    from unittest.mock import patch

    called = []

    def _spy():
        called.append(True)

    mod_name = "scripts.materialize_projects"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    with patch("scripts._env.load_local_env", _spy):
        importlib.import_module(mod_name)

    assert not called, "load_local_env must not be called at import time"


# ── DB-gated fixtures ─────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_mailbox():
    """Isolated L0+L1 mailbox for materialize tests.

    No voyage-4 embeddings are loaded, so clustering uses participant/keyword/
    temporal signals only (tests zero-vector fallback path).
    Tears down on exit via ON DELETE CASCADE.
    """
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from services.db.store import persist_l0, persist_l1
    from services.enrich.clustering.eval.run_eval import EVAL_PARAMS as CLUSTER_PARAMS
    from services.enrich.clustering.testkit import FakeNlp, make_test_embed
    from services.enrich.pipeline import run_enrichment
    from services.ingest.params import IngestParams
    from services.ingest.pipeline import IngestConfig, run_ingest

    session = SessionLocal()
    mbx = orm.Mailbox(
        provider="gmail",
        owner_email=OWNER_EMAIL,
        status="active",
        embed_model="voyage-4",
        embed_dim=1024,
        config={"internal_domains": INTERNAL_DOMAINS},
    )
    session.add(mbx)
    session.commit()
    mailbox_id = str(mbx.id)

    ingest_params = IngestParams(
        legal_domains=["morrislaw.com"],
        hr_senders=["hr@acme.com"],
        personal_domains=[],
    )
    cfg = IngestConfig(
        provider="fixture",
        mailbox_path=ROOT / "fixtures" / "mailbox.json",
        owner_email=OWNER_EMAIL,
        internal_domains=INTERNAL_DOMAINS,
        params=ingest_params,
    )
    store = run_ingest(cfg)
    persist_l0(store, mailbox_id, session)

    result = run_enrichment(
        store.messages, OWNER_EMAIL, INTERNAL_DOMAINS,
        threads=store.threads, embed_fn=make_test_embed(), nlp=FakeNlp(),
        cluster_params=CLUSTER_PARAMS,
    )
    persist_l1(result, mailbox_id, session)

    try:
        yield session, mailbox_id
    finally:
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


def _make_factory(session):
    """Return a session_factory that yields the existing session (no close)."""
    class _NonClosingSession:
        def get(self, *a, **kw):
            return session.get(*a, **kw)
        def execute(self, *a, **kw):
            return session.execute(*a, **kw)
        def scalars(self, *a, **kw):
            return session.scalars(*a, **kw)
        def close(self):
            pass  # never close the shared session

    def factory():
        return session  # reuse the existing session; close() is no-op on shared session

    return factory


# ── Integration tests ─────────────────────────────────────────────────────────

@requires_db
def test_materialize_dry_run_writes_nothing(fresh_mailbox):
    """dry_run=True must not create any Project rows."""
    from sqlalchemy import select, func
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats = materialize(
        mailbox_id=mailbox_id, dry_run=True, session_factory=factory
    )

    assert stats["projects_written"] == 0
    count = session.execute(
        select(func.count()).select_from(orm.Project).where(orm.Project.mailbox_id == mailbox_id)
    ).scalar()
    assert count == 0, f"dry_run created {count} project rows"
    assert stats["eligible_threads"] > 0
    assert stats["projects_found"] > 0  # clustering ran and found projects


@requires_db
def test_materialize_confirm_creates_projects(fresh_mailbox):
    """confirm=False,dry_run=False must persist projects, assignments, members."""
    from sqlalchemy import select, func
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats = materialize(
        mailbox_id=mailbox_id, dry_run=False, session_factory=factory
    )

    assert stats["projects_written"] > 0
    assert stats["assignments_written"] > 0
    assert stats["members_written"] > 0

    db_count = session.execute(
        select(func.count()).select_from(orm.Project).where(orm.Project.mailbox_id == mailbox_id)
    ).scalar()
    assert db_count == stats["projects_written"]


@requires_db
def test_materialize_idempotent(fresh_mailbox):
    """Running materialize twice produces identical project IDs."""
    from sqlalchemy import select
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats1 = materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)
    ids_after_first = set(
        session.execute(
            select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
        ).scalars()
    )

    stats2 = materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)
    ids_after_second = set(
        session.execute(
            select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
        ).scalars()
    )

    assert ids_after_first == ids_after_second, (
        "Re-run changed project IDs — not idempotent.\n"
        f"  first : {sorted(ids_after_first)}\n"
        f"  second: {sorted(ids_after_second)}"
    )
    assert stats1["projects_found"] == stats2["projects_found"]


@requires_db
def test_materialize_excludes_noise_and_sensitive_threads(fresh_mailbox):
    """Threads containing only noise or sensitive messages are not clustered."""
    from sqlalchemy import select
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    # Find noise-only and sensitive thread IDs in this mailbox
    noise_thread_ids = set(
        session.execute(
            select(orm.Thread.id).where(orm.Thread.mailbox_id == mailbox_id)
            .where(
                ~orm.Thread.id.in_(
                    select(orm.Message.thread_id)
                    .where(orm.Message.mailbox_id == mailbox_id)
                    .where(orm.Message.noise == False)  # noqa: E712
                )
            )
        ).scalars()
    )

    materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)

    # No noise-only thread should appear in any project assignment
    assigned_thread_ids = set(
        session.execute(
            select(orm.ThreadProjectAssignment.thread_id).where(
                orm.ThreadProjectAssignment.project_id.in_(
                    select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
                )
            )
        ).scalars()
    )

    leaked = noise_thread_ids & assigned_thread_ids
    assert not leaked, f"Noise-only thread(s) leaked into project assignments: {leaked}"


@requires_db
def test_materialize_sparse_fallback(fresh_mailbox):
    """Fixture has <30 eligible threads → agglomerative strategy (sparse fallback)."""
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats = materialize(mailbox_id=mailbox_id, dry_run=True, session_factory=factory)

    # Fixture has 13 eligible threads; params.min_threads_leiden=30 → agglomerative
    assert stats["eligible_threads"] < 30
    assert stats["strategy"] == "agglomerative"


@requires_db
def test_cover_for_me_routes_to_materialized_project(fresh_mailbox):
    """After materialize --confirm, cover-for-me _route finds a project by label."""
    from services.api.routers.cover_for_me import _route
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats = materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)
    assert stats["projects_found"] > 0, "No projects were materialized"

    # Use the top label — it has the most threads and is most likely to match
    top_label = stats["top_labels"][0]

    person, project = _route(f"what is the status of {top_label}", session, mailbox_id)

    assert project is not None, (
        f"cover-for-me failed to route to project {top_label!r} "
        f"after materialization. Known projects: {stats['top_labels']}"
    )
    assert project.label == top_label
    assert person is None


@requires_db
def test_materialize_limit_threads_reduces_eligible(fresh_mailbox):
    """--limit-threads N clusters at most N threads."""
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    full = materialize(mailbox_id=mailbox_id, dry_run=True, session_factory=factory)
    limited = materialize(
        mailbox_id=mailbox_id, dry_run=True, limit_threads=3, session_factory=factory
    )

    assert limited["eligible_threads"] <= 3
    assert limited["eligible_threads"] <= full["eligible_threads"]
