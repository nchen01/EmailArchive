"""Run the Track-A event-extraction eval against the shared fixtures (S4 §10).

Modeled on ``clustering/eval/run_eval.py``. **Structural gates only** — event
extraction is nondeterministic, so there is deliberately NO byte-identical gate
(the one exception to AGENTS §3 #6/#8, flagged in the S4 guide §4). The gates:

1. Every extracted Event parses without error (Event(...) validated).
2. Every Event carries >=1 source_message_ids.
3. The gold events in ``fixtures/gold/events.json`` are extracted with the
   correct epistemic type (proposed/did/outcome).

The LLM is replaced by a deterministic, gold-driven fake ``extract_fn`` so the
eval runs offline. The fake emits, per thread, the gold events whose cited
message belongs to that thread, using the cited message's real sender email as
the actor (so the production actor-resolution path is exercised).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from services.enrich.events import ExtractedEventRaw, ThreadContext, extract_events

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "fixtures"

OWNER_EMAIL = "alex@acme.com"
INTERNAL_DOMAINS = ["acme.com"]


def _load_inputs():
    from conftest import run_full_ingest  # type: ignore
    from services.enrich.pipeline import run_enrichment

    store = run_full_ingest()
    enrich = run_enrichment(store.messages, OWNER_EMAIL, INTERNAL_DOMAINS)
    e2p = {i.email: i.person_id for i in enrich.identities if i.person_id}
    by_thread: dict = defaultdict(list)
    for m in store.messages:
        by_thread[m.thread_id].append(m)
    return store, e2p, by_thread


def _gold_events() -> list[dict]:
    return json.loads((FIXTURES / "gold" / "events.json").read_text(encoding="utf-8"))


def make_gold_extract_fn(store, gold: list[dict]):
    """Deterministic fake: emit gold events whose cited message is in the thread.

    The gold ``source`` is a provider_id; resolve it to the message's
    ``message_id_header`` and real sender email. The actor handed to
    ``extract_events`` is that sender email, so the production resolver maps it
    back to a person_id exactly as it would for the LLM path.
    """
    by_provider = {m.provider_id: m for m in store.messages}
    # Group gold events by the header of their cited message.
    by_header: dict[str, list[ExtractedEventRaw]] = defaultdict(list)
    for g in gold:
        msg = by_provider[g["source"]]
        by_header[msg.message_id_header].append(
            ExtractedEventRaw(
                actor_name_or_email=msg.sender.email,
                type=g["type"],
                summary=g["summary"],
                source_message_id_headers=[msg.message_id_header],
            )
        )

    def extract_fn(ctx: ThreadContext) -> list[ExtractedEventRaw]:
        out: list[ExtractedEventRaw] = []
        for header in ctx.message_id_headers:
            out.extend(by_header.get(header, []))
        return out

    return extract_fn


def run(verbose: bool = True) -> dict:
    store, e2p, by_thread = _load_inputs()
    gold = _gold_events()
    extract_fn = make_gold_extract_fn(store, gold)

    owner_pid = e2p[OWNER_EMAIL]
    events = extract_events(
        store.threads, by_thread, [], e2p, owner_pid, extract_fn=extract_fn
    )

    # Gate 1+2: every Event parses (already validated at construction) and is cited.
    all_parse = True
    all_cited = all(len(e.source_message_ids) >= 1 for e in events)

    # Gate 3: gold events extracted with the correct epistemic type.
    by_provider = {m.provider_id: m for m in store.messages}
    gold_by_summary = {g["summary"]: g for g in gold}
    type_by_summary = {e.summary: e.type.value for e in events}
    gold_typed_ok = True
    gold_found = 0
    for summary, g in gold_by_summary.items():
        # The gold message must be in a non-excluded thread to be extractable.
        header = by_provider[g["source"]].message_id_header
        extracted_type = type_by_summary.get(summary)
        if extracted_type is None:
            gold_typed_ok = False
            continue
        gold_found += 1
        if extracted_type != g["type"]:
            gold_typed_ok = False

    gates = {
        "every_event_parses": all_parse,
        "every_event_cited": all_cited,
        "gold_types_correct": gold_typed_ok and gold_found == len(gold),
    }
    summary = dict(
        n_events=len(events),
        n_gold=len(gold),
        gold_found=gold_found,
        gates=gates,
        passed=all(gates.values()),
    )
    if verbose:
        print(json.dumps(summary, indent=2))
        print("PASS" if summary["passed"] else "FAIL")
    return summary


if __name__ == "__main__":
    import sys

    s = run()
    sys.exit(0 if s["passed"] else 1)
