from conftest import by_provider_id, load_gold, run_full_ingest


def test_message_count():
    store = run_full_ingest()
    assert len(store.messages) == 18


def test_thread_count():
    store = run_full_ingest()
    assert len(store.threads) == 13


def test_clean_text_pmsg_001():
    store = run_full_ingest()
    by = by_provider_id(store)
    text = by["pmsg_001"].clean_text
    assert text.startswith("I'll re-shard the index before cutover.")
    assert "Hi team, attaching" not in text


def test_noise_and_sensitivity_from_gold():
    store = run_full_ingest()
    by = by_provider_id(store)
    noise_gold = load_gold("noise")
    sens_gold = load_gold("sensitivity")
    for pid, expected in noise_gold.items():
        assert by[pid].noise == expected, pid
    for pid, expected in sens_gold.items():
        actual = [s.value for s in by[pid].sensitivity]
        assert set(actual) == set(expected), pid


def test_attachments_surfaced():
    store = run_full_ingest()
    by = by_provider_id(store)
    refs = by["pmsg_000"].attachment_refs
    assert len(refs) == 1
    assert refs[0].sha256 == "18e64564df12114fac07bbd3f6689d33305194e111a7fe5407742bf3b9df27f0"
    assert refs[0].filename == "cutover_plan.xlsx"


def test_determinism_idempotent():
    a = run_full_ingest()
    b = run_full_ingest()
    assert [m.id for m in a.messages] == [m.id for m in b.messages]
    assert [m.message_id_header for m in a.messages] == [m.message_id_header for m in b.messages]
    assert [t.id for t in a.threads] == [t.id for t in b.threads]
