from conftest import by_provider_id, load_gold, run_full_ingest

from services.ingest.normalize.body import clean_body


def test_clean_body_strips_quote_and_signature():
    body = (
        "I'll re-shard the index before cutover.\n\n"
        "On Wed, Apr 1, Jenna Park <jenna@acme.com> wrote:\n"
        "> Hi team, attaching the cutover plan for Atlas. Targeting the 5th.\n\n"
        "--\nRaj Patel | Acme Engineering | raj@acme.com"
    )
    result = clean_body(body, "text/plain", "Raj Patel")
    assert result.startswith("I'll re-shard the index before cutover.")
    assert "Hi team, attaching" not in result
    assert "Raj Patel | Acme Engineering" not in result


def test_html_body_stripped():
    html = "<html><body><p>Hello <b>world</b></p></body></html>"
    result = clean_body(html, "text/html", None)
    assert "Hello" in result
    assert "<b>" not in result


def test_clean_text_checks_against_gold():
    store = run_full_ingest()
    by = by_provider_id(store)
    gold = load_gold("clean_text_checks")
    for pid, checks in gold.items():
        text = by[pid].clean_text
        assert text.startswith(checks["startswith"]), pid
        assert checks["must_not_contain"] not in text, pid
