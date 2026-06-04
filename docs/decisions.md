# Decision log

Resolved decisions, with rationale. When a spec's "Open decisions" list contains an item that
appears here as resolved, **this log wins** — update the spec's wording lazily, but treat these as
binding for the build.

---

## D1 — Build order: logic in-memory first, persistence second
**Decision.** Implement L0 and L1 as pure functions producing `ekc_schemas` objects, validated
in-memory against `fixtures/`, *before* writing the DB layer. Then implement Alembic migrations
(spec 04 tickets 4.1–4.4) + mappers (4.7) and the round-trip/idempotency tests.
**Why.** The acceptance gates (spec 00 §19, spec 01, spec 03 §22) check object correctness, not
persistence, so they run with no database. This unblocks the hard logic (identity resolution,
clustering) without waiting on infra. Don't defer the DB to the very end: validate the round-trip
and dedupe-by-`message_id_header` invariant before a lot of code assumes a persistence behavior.
**Scope.** S1. **Affects.** spec 04 (migrations land mid-S1, not first); AGENTS §5.

## D2 — Providers: Gmail first, M365 stubbed
**Decision.** Ship Gmail as the only real provider for S1. `providers/msgraph.py` implements the
`MailProvider` protocol but raises `NotImplementedError`.
**Why.** 0-to-1 doesn't need M365; the protocol boundary keeps it a drop-in later.
**Scope.** S1. **Affects.** spec 00 §21 (provider priority — resolved); ticket 0.2 deferred.

## D3 — Run synchronously in S1; no queue yet
**Decision.** The pipeline runs synchronously, in-process, for S1. No Redis/worker queue.
**Why.** The queue exists only for real-mailbox rate limits and long fetches; against the fixture
there's no network and no rate limit. Keep the fetch loop behind an interface so it can move onto a
worker later without restructuring.
**Scope.** S1. **Affects.** spec 00 §5 (queue is a real-ingest concern, wired later).

## D4 — `FixtureProvider` is how L0 ingests the fixture
**Decision.** Add `services/ingest/providers/fixture.py` — a third `MailProvider` impl that yields
`RawMessage`s from `fixtures/mailbox.json`. It is the dev/test provider that makes the spec 00 §19
gates runnable.
**Two seams it handles deliberately:**
- `body_text` is wrapped as a single synthetic `text/plain` MIME part, so body normalization
  (quote/signature stripping, §8) still runs — that's the point of `pmsg_001`'s quoted body.
- Attachments carry a precomputed `sha256` (not bytes), so `FixtureProvider` surfaces
  `AttachmentRef`s directly and short-circuits the byte-hashing in §9. The shared-attachment signal
  still holds (T1 and T3 reference the same filename → same sha).
**Scope.** S1+. **Affects.** spec 00 §17 (module layout — added).

## D5 — Fixture must contain every gold-labeled address
**Decision.** Any address in `gold/identities.json` must appear in `mailbox.json`. Fixed: a T13
message from "Jenna Brooks" `<jenna@vertexlabs.com>` now exists, so the `must_not_merge` collision
with Jenna Park is actually exercised (same first name/local-part, different domain/surname).
**Why.** A gold label for an address the resolver never sees is a vacuous test.
**Scope.** done. **Affects.** `fixtures/generate.py` (regenerated; mailbox now 18 messages).

## D6 — S1 stubs: no object store, env-var secrets behind an interface
**Decision.** For S1, `Message.raw_uri = None` (no object store — the fixture has no raw MIME to
archive). OAuth tokens come from environment variables, read through a small `get_token()` interface
so swapping in a secrets manager later is one line.
**Non-negotiable even in dev:** OAuth tokens never touch the app DB or logs (env vars satisfy this).
In the fixture-driven path there are no tokens at all — `FixtureProvider` needs no auth.
**Scope.** S1 (object store + secrets manager are production hardening). **Affects.** spec 00 §16,
§21 (secrets — resolved for S1); spec 04 (object store wiring deferred).

## D7 — Sensitivity keyword matching: word-boundary regex + expanded "pip"
**Decision.** Replace bare `"pip"` in the HR keyword list with `"performance improvement plan"`.
Apply `\b`-anchored word-boundary regex to **all** sensitivity keywords (both PRIVILEGE and HR
lists) instead of plain `k in blob` substring matching.
**Why — two compounding problems with the original spec code:**
1. Plain substring match: `"pip"` inside `"datapipe"` or `"DataPipe contract"` triggers a false
   HR tag on pmsg_003 (vendor SOW) and pmsg_016 (legal message).
2. Even with word boundaries, `\bpip\b` still matches `"pip install the dependency"` — an entirely
   routine engineering phrase. The bare three-letter acronym is too ambiguous in a mixed
   engineering/business mailbox.
**The fix:** `"performance improvement plan"` is unambiguous regardless of word boundaries.
Word-boundary regex is applied to all keywords so the same discipline applies to any future short
token added to either list.
**Broader principle:** avoid bare short acronyms as standalone sensitivity triggers; use the
expanded phrase instead.
**Scope.** S1 (implemented). **Affects.** spec 00 §11 (amended to match); `normalize/sensitivity.py`.

## D8 — `event` citation CHECK: `cardinality()` not `array_length()`
**Decision.** Enforce "no citation, no claim" on `event.source_message_ids` with
`cardinality(source_message_ids) >= 1`, not `array_length(source_message_ids, 1) >= 1`.
**Why.** `array_length()` returns NULL for an empty array (not 0), so `NULL >= 1` is NULL
(falsy), and the CHECK passes — an empty array slips through. `cardinality()` returns 0 for an
empty array, so the constraint correctly rejects it. This is a real spec bug.
**Scope.** S2 (migration 0005). **Affects.** spec 04 §5 (event table, to be amended); `0004_l1_projects.py` uses `cardinality` (correct); `0005_fix_event_citation_check.py` re-adds the constraint correctly on the live DB.

## D9 — Frontend graph library: `react-force-graph-2d`
**Decision.** Use `react-force-graph-2d` (backed by D3 force simulation) for the network-map
canvas. The umbrella `react-force-graph` package (which bundles 2D, 3D, and VR renderers) is
not installed — only the 2D variant. Record as a library choice — not revisit unless a
performance threshold is hit.
**Why over sigma.js:** simpler React integration, no separate renderer setup, force-directed
layout is automatic, and at fixture-scale (10 nodes) and expected production scale (hundreds of
nodes) D3 force performs fine. Sigma.js is the upgrade path if the graph grows to thousands of
nodes (benchmark first).
**Scope.** S2 frontend. **Affects.** spec 05 §9 open decision (resolved).

## D10 — S4 event extraction is L1 materialization backed by an injected LLM fn
**Decision.** Event extraction (`services/enrich/events.py`, spec 01 §7) lives in the L1 enrich
service and produces persisted `Event` objects — structurally identical to how identity resolution
produces `Person` objects. The LLM dependency is fully injectable (`extract_fn` parameter, same
pattern as `embed_fn`/`nlp` in clustering), so all tests remain deterministic and offline. This is
the one sanctioned exception to AGENTS §3 #9 ("the LLM appears only in L3").
**Why not L3:** extraction runs offline/batch and materializes first-class objects that L2 and L3
later query. Doing it lazily at query time would be slower, more expensive, and would break the
grounding discipline (cited Events must be present before synthesis runs).
**Invariant preserved:** `extract_fn` is the seam — L1 orchestrates + persists; the network call
stays behind the injectable interface; tests never call the Anthropic API.
**Scope.** S4. **Affects.** `services/enrich/events.py`; AGENTS §3 #9 (exception now documented).
