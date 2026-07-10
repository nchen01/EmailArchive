"""S9 materialize_projects tests.

Offline unit tests cover the embedding bridge (_make_db_embed_fn) and import
safety.  DB-gated integration tests cover the full pipeline, idempotency,
noise/sensitive exclusion, missing-embedding guards, commit persistence, and
cover-for-me routing.

DB-gated tests use two fixture variants:
- fresh_mailbox: standard L0+L1 mailbox, NO voyage-4 embeddings.  Used for
  dry-run path tests and the missing-embedding failure test.
- fresh_mailbox_with_embeddings: same mailbox with deterministic fake voyage-4
  vectors inserted for all embeddable messages.  Required for --confirm path
  tests so the missing-embedding guard does not raise.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select, func, text

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


# ── DB-gated helpers ──────────────────────────────────────────────────────────

def _insert_fake_voyage_embeddings(session, mailbox_id: str) -> int:
    """Insert deterministic fake voyage-4 embeddings for all embeddable messages.

    Queries the DB for non-noise, sensitivity='{none}' messages and inserts a
    seeded random 1024-dim vector for each.  Returns the count inserted.
    Only call this after the mailbox L0+L1 data is committed.
    """
    from services.db import models as orm

    rows = session.execute(
        text(
            "SELECT id::text FROM message "
            "WHERE mailbox_id = :mid AND noise = false AND sensitivity = '{none}'"
        ),
        {"mid": mailbox_id},
    ).all()

    rng = np.random.default_rng(42)
    for (mid,) in rows:
        vec = rng.random(1024).astype(np.float32)
        session.add(orm.MessageEmbedding(
            mailbox_id=mailbox_id,
            message_id=mid,
            embed_model="voyage-4",
            embed_dim=1024,
            content_hash=f"fake-{mid}",
            embedding=vec.tolist(),
        ))
    session.commit()
    return len(rows)


# ── DB-gated fixtures ─────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_mailbox():
    """Isolated L0+L1 mailbox for materialize tests.  No voyage-4 embeddings.

    Use for dry-run path tests and missing-embedding failure tests.
    Tears down via ON DELETE CASCADE.
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
        # Guard against cross-test contamination: if a test left the session in
        # an aborted-transaction state (e.g. a failed flush), roll back first so
        # cleanup can still run. Without this, the mailbox delete below would
        # itself raise, the mailbox would leak, and the failure would cascade
        # into later tests in the same pytest process.
        session.rollback()
        session.execute(orm.Mailbox.__table__.delete().where(orm.Mailbox.id == mailbox_id))
        session.commit()
        session.close()


@pytest.fixture()
def fresh_mailbox_with_embeddings(fresh_mailbox):
    """fresh_mailbox plus fake voyage-4 embeddings for all embeddable messages.

    Required for --confirm path tests: the missing-embedding guard in materialize()
    raises RuntimeError when any eligible message lacks a voyage-4 vector.
    """
    session, mailbox_id = fresh_mailbox
    _insert_fake_voyage_embeddings(session, mailbox_id)
    yield session, mailbox_id


# ── Integration tests — dry-run path (no embeddings needed) ──────────────────

@requires_db
def test_materialize_dry_run_writes_nothing(fresh_mailbox):
    """dry_run=True must not change the Project row count; reports missing_embeddings.

    Note: the fresh_mailbox fixture runs full L1 enrichment, and persist_l1
    persists the clustering result, so the mailbox already has Project rows
    before materialize runs. The invariant under test is that a dry run adds
    nothing on top of that baseline (delta == 0), not that the baseline is empty.
    """
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    def _project_count() -> int:
        return session.execute(
            select(func.count()).select_from(orm.Project)
            .where(orm.Project.mailbox_id == mailbox_id)
        ).scalar()

    before = _project_count()
    stats = materialize(
        mailbox_id=mailbox_id, dry_run=True, session_factory=factory
    )

    assert stats["projects_written"] == 0
    after = _project_count()
    assert after == before, f"dry_run changed project count: {before} -> {after}"
    assert stats["eligible_threads"] > 0
    assert stats["projects_found"] > 0   # clustering ran and found projects
    assert stats["missing_embeddings"] > 0  # no embeddings loaded in fresh_mailbox


@requires_db
def test_materialize_dry_run_reports_missing_embeddings(fresh_mailbox):
    """dry_run=True reports missing_embeddings count but does not raise."""
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    stats = materialize(mailbox_id=mailbox_id, dry_run=True, session_factory=factory)

    assert "missing_embeddings" in stats
    assert stats["missing_embeddings"] > 0, (
        "Expected missing_embeddings > 0 because fresh_mailbox has no voyage-4 vectors"
    )


@requires_db
def test_materialize_confirm_fails_missing_embeddings(fresh_mailbox):
    """dry_run=False must raise RuntimeError when any eligible message lacks an embedding."""
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox

    def factory():
        return session

    with pytest.raises(RuntimeError, match="missing voyage-4 embeddings"):
        materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)


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


# ── Integration tests — confirm path (embeddings required) ───────────────────

@requires_db
def test_materialize_confirm_creates_projects(fresh_mailbox_with_embeddings):
    """dry_run=False must persist projects, assignments, and members."""
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

    def factory():
        return session

    stats = materialize(
        mailbox_id=mailbox_id, dry_run=False, session_factory=factory
    )

    assert stats["projects_written"] > 0
    assert stats["assignments_written"] > 0
    assert stats["members_written"] > 0
    assert stats["missing_embeddings"] == 0

    db_count = session.execute(
        select(func.count()).select_from(orm.Project).where(orm.Project.mailbox_id == mailbox_id)
    ).scalar()
    assert db_count == stats["projects_written"]


@requires_db
def test_materialize_commit_persists_across_sessions(fresh_mailbox_with_embeddings):
    """Projects written by --confirm are visible in a brand-new session.

    Regression test for the missing session.commit() after _persist_clustering().
    The previous bug: _persist_clustering() executed writes but never committed;
    session.close() in the finally block rolled them back silently.
    """
    from services.db import models as orm
    from services.db.engine import SessionLocal
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

    # Call materialize with a real SessionLocal factory so it creates its own
    # session — this is how the CLI runs it.  The shared fixture session is used
    # only for setup (already committed) and final verification.
    stats = materialize(
        mailbox_id=mailbox_id, dry_run=False, session_factory=SessionLocal
    )
    assert stats["projects_written"] > 0, "No projects produced — test precondition failed"

    new_session = SessionLocal()
    try:
        count = new_session.execute(
            select(func.count()).select_from(orm.Project).where(
                orm.Project.mailbox_id == mailbox_id
            )
        ).scalar()
        assert count == stats["projects_written"], (
            f"Only {count} Project row(s) visible in a new session; "
            f"expected {stats['projects_written']}.  "
            "_persist_clustering() writes were not committed."
        )
    finally:
        new_session.close()


@requires_db
def test_materialize_idempotent(fresh_mailbox_with_embeddings):
    """Running materialize twice produces identical project IDs."""
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

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
def test_materialize_excludes_noise_and_sensitive_threads(fresh_mailbox_with_embeddings):
    """Threads containing only noise or sensitive messages are not clustered."""
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

    def factory():
        return session

    # Find noise-only thread IDs (threads with no non-noise message at all)
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
def test_materialize_excludes_mixed_sensitive_thread(fresh_mailbox_with_embeddings):
    """A thread with both clean and sensitive messages must not appear in project assignments.

    Regression test for P2: the old _get_eligible_thread_ids used DISTINCT thread_id
    from the message table, which selected threads with at least one clean message but
    did not exclude mixed threads.  The fix applies whole-thread exclusion: a thread is
    ineligible if ANY of its messages carries sensitivity != '{none}'.
    """
    from services.db import models as orm
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

    # Create a thread containing one clean message and one HR-sensitive message.
    thread_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    thread = orm.Thread(
        id=thread_id,
        mailbox_id=mailbox_id,
        subject_norm="mixed sensitivity thread",
        participants=["alex@acme.com", "hr@acme.com"],
        t_start=now,
        t_end=now,
    )
    session.add(thread)
    # Flush the parent Thread before adding its Messages. Message.thread_id is a
    # raw ForeignKey with no ORM relationship() to Thread, so the unit of work
    # will NOT order the parent insert first on its own — without this flush the
    # message INSERT races ahead of the thread INSERT and raises a
    # ForeignKeyViolation, which aborts the session's transaction and cascades
    # into later tests in the same process.
    session.flush()

    clean_msg = orm.Message(
        mailbox_id=mailbox_id,
        message_id_header=f"<clean-{thread_id}@test>",
        provider_id=f"clean-{thread_id}",
        thread_id=thread_id,
        sender_email="alex@acme.com",
        to_emails=["bob@acme.com"],
        cc_emails=[],
        addresses={},
        ts=now,
        subject="budget review",
        clean_text="budget review project update",
        link_domains=[],
        sensitivity=["none"],
        noise=False,
    )
    session.add(clean_msg)

    hr_msg = orm.Message(
        mailbox_id=mailbox_id,
        message_id_header=f"<hr-{thread_id}@test>",
        provider_id=f"hr-{thread_id}",
        thread_id=thread_id,
        sender_email="hr@acme.com",
        to_emails=["alex@acme.com"],
        cc_emails=[],
        addresses={},
        ts=now,
        subject="performance review",
        clean_text="performance review hr confidential",
        link_domains=[],
        sensitivity=["hr"],
        noise=False,
    )
    session.add(hr_msg)
    session.commit()

    # The mixed thread's clean message is NOT in embeddable_msg_ids (excluded by
    # whole-thread filtering), so it needs no embedding — the guard won't raise.
    def factory():
        return session

    materialize(mailbox_id=mailbox_id, dry_run=False, session_factory=factory)

    assigned_thread_ids = set(
        session.execute(
            select(orm.ThreadProjectAssignment.thread_id).where(
                orm.ThreadProjectAssignment.project_id.in_(
                    select(orm.Project.id).where(orm.Project.mailbox_id == mailbox_id)
                )
            )
        ).scalars()
    )

    assert thread_id not in assigned_thread_ids, (
        "Mixed-sensitivity thread was incorrectly assigned to a project. "
        "Whole-thread exclusion must reject threads that contain ANY sensitive message."
    )


@requires_db
def test_cover_for_me_routes_to_materialized_project(fresh_mailbox_with_embeddings):
    """After materialize --confirm, cover-for-me _route finds a project by label."""
    from services.api.routers.cover_for_me import _route
    from scripts.materialize_projects import materialize

    session, mailbox_id = fresh_mailbox_with_embeddings

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
