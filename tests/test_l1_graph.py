from datetime import datetime, timezone

from conftest import run_full_ingest

from services.enrich.pipeline import run_enrichment

NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


def _enrich(now=NOW):
    store = run_full_ingest()
    res = run_enrichment(store.messages, "alex@acme.com", ["acme.com"], now=now)
    e2p = {i.email: i.person_id for i in res.identities}
    return store, res, e2p


def test_owner_has_no_self_edge():
    _, res, e2p = _enrich()
    owner_pid = e2p["alex@acme.com"]
    assert all(e.person_id != owner_pid for e in res.edges)


def test_all_contacts_have_edge():
    _, res, e2p = _enrich()
    owner_pid = e2p["alex@acme.com"]
    edge_pids = {e.person_id for e in res.edges}
    # every non-owner person that exchanged a non-noise message with owner
    expected = {
        e2p["jenna@acme.com"], e2p["raj@acme.com"], e2p["aiko@acme.com"],
        e2p["grace@acme.com"], e2p["dana@northwind.com"], e2p["marcus@cloudpeak.io"],
        e2p["ben@datapipe.com"], e2p["jenna@vertexlabs.com"],
        e2p["hr@acme.com"], e2p["counsel@morrislaw.com"],
    }
    assert owner_pid not in edge_pids
    assert expected <= edge_pids


def test_weights_positive_and_bounded():
    _, res, _ = _enrich()
    for e in res.edges:
        assert e.weight > 0.0
        assert e.weight < 10.0


def test_determinism():
    _, res_a, _ = _enrich()
    _, res_b, _ = _enrich()
    a = {e.person_id: e.weight for e in res_a.edges}
    b = {e.person_id: e.weight for e in res_b.edges}
    assert a == b


def test_jenna_received_count():
    _, res, e2p = _enrich()
    jenna_pid = e2p["jenna@acme.com"]
    edge = next(e for e in res.edges if e.person_id == jenna_pid)
    # jenna sent pmsg_000, pmsg_004, pmsg_011 to alex
    assert edge.received_count >= 3


def test_noise_excluded_from_edges():
    # pmsg_014 is from news@updates.examplesaas.com (noise). No edge should exist.
    _, res, e2p = _enrich()
    # the noise sender is not a recipient/owner exchange counted; ensure no edge
    # references a person whose only contact was the noise message.
    store = run_full_ingest()
    news_msg = next(m for m in store.messages if m.provider_id == "pmsg_014")
    news_pid = e2p.get(news_msg.sender.email)
    assert all(e.person_id != news_pid for e in res.edges)
