# S8 Implementation Plan — Real-Mailbox Demo Readiness

**Goal:** prove that a user can run this system on a real Gmail mailbox, ask
cover-for-me questions, understand the cited evidence, and encounter clean
failure messages instead of silent wrong answers or confusing stack traces.

**Why before S7.12 reranking:** retrieval quality is green on the fixture.
Ranking misses are only diagnosable once real-mailbox usage shows them.
The higher product risk right now is operational — keys, rate limits,
missing embeddings, and opaque citation chips.

Source decisions: D12 (L2 retrieval ownership, embedding model, privacy).
S7 prerequisite: S7.1–S7.11 complete and live-validated.

---

## Current state going into S8

- Fixture mailbox (18 messages) embedded and eval-green (7/7 gates, MRR=1.0).
- Real Gmail mailboxes in dev DB: `puluo1938@gmail.com` (460 messages),
  `johncartergpt2024@gmail.com` (2162 messages) — neither backfilled yet.
- `CitationChips` renders raw `message_id_header` strings (e.g. `atlas-1@acme.com`).
  Subject, date, and snippet are not shown to the user.
- `CoverForMeResponse` has no field for retrieval status; failure modes are
  undifferentiated (`state` is free-text).
- No preflight tooling; silent failures (rate-limit hits, missing keys, missing
  embeddings) degrade to "insufficient evidence" with no diagnostic signal.

---

## Task Breakdown

### S8.1 — Real-mailbox backfill validation

**File:** `scripts/embed_backfill.py` (existing — no code change; this is a
validation task).

Run the backfill against the smoke Gmail mailbox and record results:

```bash
# Dry run first — check counts and cost estimate.
python scripts/embed_backfill.py \
  --mailbox-id <smoke-mailbox-id> --dry-run

# Live run.
python scripts/embed_backfill.py \
  --mailbox-id <smoke-mailbox-id> --confirm

# Idempotency check — must print to_embed=0.
python scripts/embed_backfill.py \
  --mailbox-id <smoke-mailbox-id> --dry-run
```

Report:
- Total messages, embeddable (noise=false, sensitivity='none'), excluded, embedded.
- Any encoding errors, oversized clean_text batches, or model errors.
- Actual API cost vs. estimate.
- Idempotency confirmed (second dry-run shows to_embed=0).

**Acceptance:**
- Backfill completes without error on real mailbox.
- Excluded count matches expected noise + sensitive counts for that mailbox.
- Re-run produces zero new API calls.

**Chosen smoke mailbox:** decide before starting S8.1. Use whichever is the
smaller of the two real mailboxes; 460 messages is the recommended starting
point (`puluo1938@gmail.com`).


### S8.2 — API: supporting evidence in CoverForMeResponse

Files:
- `services/api/schemas/cover_for_me.py`
- `services/api/routers/cover_for_me.py`
- `services/synthesis/cover_for_me.py` (minor — return l2_hits to router)
- `frontend/src/api/types.ts`
- `frontend/src/components/CoverForMe.tsx`

**Problem:** `CitationChips` shows raw message_id_header strings. The user
cannot tell what a claim is citing without opening their email client.

**API change (backward-compatible additive):**

Add `supporting_evidence: list[EvidenceMessage]` to `CoverForMeResponse`.

```python
class EvidenceMessage(BaseModel):
    message_id_header: str
    subject: str
    date: str            # ISO 8601
    snippet: str         # first 200 chars of clean_text

class CoverForMeResponse(BaseModel):
    query: str
    routed_to: str | None
    result: SynthesisResult
    supporting_evidence: list[EvidenceMessage] = []  # new
```

**Source rule: derive from the final claims' `source_message_ids`, not from all
L2 hits.**  A retrieved message that the model did not cite must not appear in
`supporting_evidence` — exposing uncited retrieval hits as if they supported the
answer would violate "no citation, no claim".

Population:
1. Collect the union of all `source_message_ids` across `result.claims`.
2. For any header in that set that came from an L2 hit (already in memory as
   `l2_hits`), use the hit's subject/ts/snippet directly — no extra DB query.
3. For any header that is L1-sourced (not in `l2_hits`), do one lightweight DB
   lookup: `SELECT subject, ts, clean_text FROM message WHERE message_id_header IN (...)`.
4. Build one `EvidenceMessage` per unique cited header.

This means `supporting_evidence` may be shorter than `l2_hits` when the model
cited only a subset of the retrieved messages, and it correctly includes L1-cited
messages when those were the source of a claim.

**Frontend change (`CitationChips`):**

Replace raw header chip with `subject (date)` label. Add a tooltip or expand
panel for the snippet. Keep the header as a `title` attribute for copy-ability.

```tsx
// Before: <span title={id}>{id}</span>
// After:
<span title={id} className="citation-chip">
  {evidence?.subject ?? id} · {evidence?.date}
</span>
```

**Acceptance:**
- `CoverForMeResponse` always includes `supporting_evidence` (may be empty
  for L1-only paths where claims cite L1 message headers).
- `supporting_evidence` contains exactly the messages cited in `result.claims`
  — no more, no fewer. Uncited retrieval hits are never exposed.
- UI shows subject + date in citation chips; hover/tooltip shows snippet.
- Old clients that ignore the new field are unaffected.


### S8.3 — Operational preflight

File: `scripts/preflight.py`

A CLI tool that checks all prerequisites before running the cover-for-me
pipeline. Exit 0 on success; exit 1 with a clear itemised failure list.

```text
python scripts/preflight.py [--mailbox-id <uuid>]
```

Checks:

| Check | Pass condition | Failure message |
|---|---|---|
| VOYAGE_API_KEY | Set and non-empty | "VOYAGE_API_KEY not set — L2 retrieval disabled" |
| ANTHROPIC_API_KEY | Set and non-empty | "ANTHROPIC_API_KEY not set — synthesis will return 503" |
| DB reachable | Connection succeeds | "Cannot connect to DATABASE_URL" |
| DB at head | alembic current == head | "DB is not at migration head — run alembic upgrade head" |
| Embeddings present | ≥1 row in message_embedding for the mailbox (if --mailbox-id given) | "No embeddings found for this mailbox — run embed_backfill.py" |
| ENABLE_RERANKING | Not '1' (reranking off by default) | "ENABLE_RERANKING=1 — hosted reranker active (S7.12 not yet validated)" |
| Voyage rate limits | Print informational note; not a hard gate | "Free-tier: 3 RPM / 10K TPM. Add payment method at dashboard.voyageai.com for production." |

Also expose as `GET /api/preflight` so the frontend can call it on mount and
display a configuration banner when keys or embeddings are missing.

**Acceptance:**
- `python scripts/preflight.py --mailbox-id <uuid>` exits 0 on a correctly
  configured dev environment.
- Missing VOYAGE_API_KEY → exit 1 with a message pointing to `.env.example`.
- `GET /api/preflight` returns a JSON object with each check's status.


### S8.4 — Graceful failure UX

Files:
- `services/api/schemas/cover_for_me.py`
- `services/api/routers/cover_for_me.py`
- `frontend/src/components/CoverForMe.tsx`
- `frontend/src/hooks/useCoverForMe.ts`

**Problem:** five distinct failure modes currently collapse into two states
("insufficient evidence" or generic error), making it impossible for a user
or operator to diagnose what went wrong.

**API change:** add `retrieval_status` to `CoverForMeResponse`.

```python
from typing import Literal

RetrievalStatus = Literal[
    "active",              # L2 retrieval ran and returned hits
    "active_l1_only",      # L2 ran but returned no hits; L1 answered
    "disabled_no_key",     # VOYAGE_API_KEY absent — L1-only, expected
    "degraded_rate_limit", # Voyage rate-limit hit — L2 skipped this request
    "no_embeddings",       # embed_model rows not found for this mailbox
    "unavailable",         # Voyage client construction failed
]

class CoverForMeResponse(BaseModel):
    query: str
    routed_to: str | None
    result: SynthesisResult
    supporting_evidence: list[EvidenceMessage] = []
    retrieval_status: RetrievalStatus = "active"   # new
```

Change `_run_l2` to return `(RetrievalStatus, list[RetrievalHit])` instead of just
`list[RetrievalHit]`.  Map each outcome:
- `embed_client is None` → `("disabled_no_key", [])`
- `EmbedError` wrapping `RateLimitError` → `("degraded_rate_limit", [])`
- `EmbedError` wrapping other → `("unavailable", [])`
- Result has hits → `("active", hits)`
- Result is empty or `InsufficientEvidence`:
  - Count `message_embedding` rows for this mailbox + model (cheap, indexed).
  - Count == 0 → `("no_embeddings", [])`
  - Count > 0 → `("active_l1_only", [])`   # hits exist but scored below threshold

**Frontend change:**

Map each `retrieval_status` to a distinct indicator in the UI:

| Status | Indicator |
|---|---|
| `active` | "Searched [N] messages" badge |
| `active_l1_only` | "Searched structured data only" note |
| `disabled_no_key` | "Evidence search not configured" muted note |
| `degraded_rate_limit` | "Evidence search temporarily limited" amber warning |
| `no_embeddings` | "Message embeddings not found — run backfill" amber warning |
| `unavailable` | "Evidence search unavailable" amber warning |

Also handle the Anthropic 503 path more specifically: the current
`notConfigured` flag fires on any 503; add `503_detail` or parse the detail
field to distinguish "ANTHROPIC_API_KEY not configured" from transient errors.

**Acceptance:**
- No VOYAGE_API_KEY → `retrieval_status = "disabled_no_key"`, response
  still returns valid L1 answer, no crash.
- Voyage rate-limit hit → `retrieval_status = "degraded_rate_limit"`, clean
  L1 fallback, warning logged server-side.
- No embeddings → `retrieval_status = "no_embeddings"`, distinct UI state.
- No hits from either L1 or L2 → `state = "insufficient evidence"` (unchanged).
- ANTHROPIC_API_KEY absent → 503 with descriptive detail (unchanged behavior,
  improved message text).


### S8.5 — Real-mailbox smoke eval

Files:
- `services/retrieval/eval/smoke_fixtures.py` (new)
- `services/retrieval/eval/run_smoke_eval.py` (new, or extend `run_eval.py`
  with `--fixture smoke`)

**Problem:** the S7.10 eval proves the pipeline on a hand-crafted synthetic
fixture. It does not prove retrieval quality on real mail with real content,
real noise, and real sensitivity patterns.

A smoke eval is not a gold-label eval. It is a regression harness: 5–10
manually curated cases, each with:
- A natural-language query
- One or more `expected_headers` (real RFC 5322 Message-IDs, angle-brackets
  stripped, from the smoke mailbox)
- One or more `forbidden_headers` (sensitive or noise messages that must never
  appear)
- An `expected_route` (l1_exact, l2_fallback, or hybrid)

The smoke eval runs exactly like the fixture eval (`run_eval.py`) but against
the smoke mailbox and with `VOYAGE_EVAL_PARAMS`. The same 7 hard gates apply.

```bash
python -m services.retrieval.eval.run_eval \
  --mailbox-id <smoke-mailbox-id> \
  --embed-client voyage \
  --fixture smoke \   # selects SMOKE_EVAL_CASES and VOYAGE_EVAL_PARAMS
  --verbose
```

**How to build the smoke eval cases:**

1. After S8.1 backfill completes, run a few exploratory cover-for-me queries
   against the smoke mailbox with L2 enabled.
2. For each query that returns good L2 results, record the message_id_headers
   of the top-1 expected message.
3. For any sensitive message that appeared in the DB, record it as a
   forbidden_header.
4. Aim for coverage across: project-like threads, person-to-person discussions,
   and at least one query that should return InsufficientEvidence.

**Acceptance:**
- Smoke eval runner exits 0 (all hard gates pass) against the smoke mailbox
  with `--embed-client voyage`.
- No sensitive or noise message appears in any result.
- Report: MRR and top-1 precision across cases with expected_headers.

---

## Definition of Done

S8 is complete when:

- Real-mailbox backfill completes and is idempotent (S8.1).
- `CoverForMeResponse` includes `supporting_evidence` and `retrieval_status`
  fields; frontend renders subject/date citation chips (S8.2, S8.4).
- `python scripts/preflight.py --mailbox-id <uuid>` exits 0 on a correctly
  configured env; `GET /api/preflight` returns structured status (S8.3).
- Each of the five failure modes produces a distinct, user-readable state
  rather than a silent fallback or stack trace (S8.4).
- Smoke eval passes all hard gates against the real mailbox with voyage-4
  embeddings (S8.5).
- Full offline test suite remains green (381 passed, 79 skipped baseline).
- Frontend build passes.

---

## Dependency Chain

```
S8.1 (real-mailbox backfill)
  └── S8.5 (smoke eval cases — curated after backfill)

S8.2 API (supporting_evidence schema)
  └── S8.2 frontend (citation chips with subject/date)

S8.3 (preflight script + API endpoint)   [parallel with S8.2]

S8.4 API (retrieval_status enum)
  └── S8.4 frontend (distinct UX per status)

S8.2 and S8.4 share the same response schema file — implement together in one PR.
```

---

## Resolved Decisions

**Q1 — Smoke mailbox: `puluo1938@gmail.com` (460 messages). ✓ resolved**
Right size and has real-mailbox messiness. `johncartergpt2024@gmail.com`
(2162 messages) is kept as a later second-pass validation mailbox, not the S8 target.

**Q2 — `no_embeddings` detection: per-request COUNT, lazy (after empty results). ✓ resolved**
Implement as a DB COUNT query run only when both vector and FTS searches return
empty results. Never cache at startup — a mid-session backfill must be reflected
immediately without a restart. The COUNT is cheap on an indexed table and only
fires on the unhappy path.

Implementation: in `_run_l2`, when `hybrid_search` returns `InsufficientEvidence`
or an empty list, follow up with:
```sql
SELECT COUNT(*) FROM message_embedding
WHERE mailbox_id = :mid AND embed_model = :model
```
If COUNT is 0, return `("no_embeddings", [])` instead of `("active_l1_only", [])`.
`_run_l2` returns a `(retrieval_status, hits)` pair rather than `list[RetrievalHit]`
alone.

**S8.2 + S8.4 together: implement in one PR. ✓ resolved**
Both tasks modify `CoverForMeResponse` in the same schema file. Splitting into two
PRs would require two additive schema migrations with intermediate states that are
only half-correct. One PR adds `supporting_evidence`, `retrieval_status`, and the
frontend changes for both at once.
