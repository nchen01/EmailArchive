from conftest import by_provider_id, load_gold, run_full_ingest


def test_sensitivity_matches_gold():
    store = run_full_ingest()
    by = by_provider_id(store)
    gold = load_gold("sensitivity")
    for pid, expected in gold.items():
        actual = [s.value for s in by[pid].sensitivity]
        assert set(actual) == set(expected), (pid, actual, expected)


def test_hr_and_privileged_specifics():
    store = run_full_ingest()
    by = by_provider_id(store)
    assert set(s.value for s in by["pmsg_015"].sensitivity) == {"hr"}
    assert set(s.value for s in by["pmsg_016"].sensitivity) == {"privileged", "legal"}
