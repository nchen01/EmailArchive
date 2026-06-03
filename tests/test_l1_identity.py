from conftest import load_gold, run_full_ingest

from services.enrich.pipeline import run_enrichment


def _resolve():
    store = run_full_ingest()
    res = run_enrichment(store.messages, "alex@acme.com", ["acme.com"])
    e2p = {i.email: i.person_id for i in res.identities}
    return store, res, e2p


def test_must_merge():
    _, _, e2p = _resolve()
    assert e2p["jenna@acme.com"] == e2p["j.park@acme.com"]


def test_must_not_merge():
    _, _, e2p = _resolve()
    assert e2p["jenna@acme.com"] != e2p["jenna@vertexlabs.com"]


def test_all_addresses_resolved():
    _, _, e2p = _resolve()
    gold = load_gold("identities")
    for email in gold["address_to_person"]:
        assert e2p.get(email) is not None, f"{email} not resolved"


def test_gold_grouping_consistent():
    """Every pair gold maps to the same person id must share a resolved person id,
    and pairs mapped to different gold ids must differ."""
    _, _, e2p = _resolve()
    gold = load_gold("identities")["address_to_person"]
    # group emails by gold person
    groups = {}
    for email, gp in gold.items():
        groups.setdefault(gp, []).append(email)
    seen_pids = {}
    for gp, emails in groups.items():
        pids = {e2p[e] for e in emails}
        assert len(pids) == 1, (gp, pids)
        pid = pids.pop()
        assert pid not in seen_pids, (gp, seen_pids.get(pid))
        seen_pids[pid] = gp


def test_orgs_internal_flag():
    _, res, _ = _resolve()
    orgs = {o.domains[0]: o for o in res.orgs}
    assert orgs["acme.com"].internal is True
    assert orgs["vertexlabs.com"].internal is False
