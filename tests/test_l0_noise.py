from conftest import by_provider_id, load_gold, run_full_ingest


def test_noise_matches_gold():
    store = run_full_ingest()
    by = by_provider_id(store)
    gold = load_gold("noise")
    for pid, expected in gold.items():
        assert by[pid].noise == expected, pid


def test_only_pmsg_014_is_noise():
    store = run_full_ingest()
    noisy = [m.provider_id for m in store.messages if m.noise]
    assert noisy == ["pmsg_014"]
