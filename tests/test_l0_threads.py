from conftest import by_provider_id, run_full_ingest


def test_thread_count():
    store = run_full_ingest()
    assert len(store.threads) == 13


def test_pt_dup_two_threads_with_conflict():
    store = run_full_ingest()
    by = by_provider_id(store)
    t12 = next(t for t in store.threads if by["pmsg_012"].id in t.message_ids)
    t13 = next(t for t in store.threads if by["pmsg_013"].id in t.message_ids)
    assert t12.id != t13.id
    assert t12.lineage_conflict is True
    assert t13.lineage_conflict is True
    # exactly two threads carry pt_dup
    dup_threads = [t for t in store.threads if "pt_dup" in t.provider_thread_ids]
    assert len(dup_threads) == 2


def test_t1_has_three_messages():
    store = run_full_ingest()
    by = by_provider_id(store)
    t1 = next(t for t in store.threads if by["pmsg_000"].id in t.message_ids)
    member_ids = set(t1.message_ids)
    assert by["pmsg_001"].id in member_ids
    assert by["pmsg_002"].id in member_ids
    assert len(t1.message_ids) == 3


def test_pmsg_008_synthetic_message_id():
    store = run_full_ingest()
    by = by_provider_id(store)
    assert by["pmsg_008"].message_id_header.startswith("synthetic:")


def test_thread_participants_include_owner():
    store = run_full_ingest()
    by = by_provider_id(store)
    t1 = next(t for t in store.threads if by["pmsg_000"].id in t.message_ids)
    assert "alex@acme.com" in t1.participants
