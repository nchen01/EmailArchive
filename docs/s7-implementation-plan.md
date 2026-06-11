# S7 Implementation Plan — L2 Hybrid Retrieval

Source decisions: D12 (`docs/decisions.md`).
Prior art: `docs/l2-brain-repo-assessment.md`, `docs/l2-product-decisions.md`.

## Current Implementation Status

- S7.1–S7.11 are implemented, reviewed, and live-validated.
- S7.9 is fully implemented via `services/retrieval/contracts.py` and the quality gates in `hybrid.py`.
- S7.10 retrieval eval passes all 7 hard gates on the fixture mailbox (MRR 1.0, top-1 precision 1.0)
  with both FakeEmbedClient (offline CI) and VoyageEmbedClient (live validation, 2026-06-11).
- S7.11 cover-for-me L2 upgrade is complete: L1+L2 hybrid routing, `_synthesize_l2_hits` for
  L2-only path, citation allow-list enforced, VOYAGE_API_KEY absent → L1-only graceful fallback.
- S7 core retrieval is complete and live-validated. S7.12 hosted Voyage reranker is optional,
  off by default, and not required for MVP; protocol and `NoOpReranker` stub exist in
  `services/retrieval/reranker.py`.
- Live validation (2026-06-11): 15 messages embedded in fixture mailbox (7b968739-...),
  repeat dry-run to_embed=0, eval 7/7 hard gates passed, MRR=1.000, top-1 precision=1.000.

## Scope

S7 adds L2 hybrid retrieval (vector + FTS) to the pipeline and upgrades
cover-for-me from L1-only to L1+L2 hybrid internally. No new surface API.
No "chat with mailbox." Every returned result carries a `message_id_header`
citation. No citation, no claim.

## Out of scope for S7

- Chunk-level splitting (message-level only).
- Attachment embedding.
- Thread-context neighbor expansion (deferred to S8).
- M365 provider.
- Hosted reranker in production (feature-flagged off by default).
- Answer generation changes beyond adding L2 evidence to existing synthesis prompts.
- New UI surfaces (project view and network map are unchanged).

## Architecture

```
Query
  │
  ├─► L1 exact routing (Person / Project / Event entity detection)
  │         │
  │    L1 hits? ──yes──► L1 structured evidence (primary answer)
  │         │                      │
  │        no                      │ (always)
  │         │                      │
  ├─────────┴──────────────────────┘
  │
  └─► L2 hybrid retrieval (always runs — capped supporting evidence;
            │              sole source when L1 has no match)
       vector search (HNSW cosine, voyage-4)
            +
       FTS search (Postgres subject_clean_tsv)
            │
       hybrid merge + deterministic rerank
            │
       evidence quality gate
            │
       hits? ──yes──► cited evidence to synthesis (L1 + L2 combined)
            │
           no──────► "insufficient evidence" (no fabrication)
```

## Task Breakdown

### S7.1 — Migration 0006: `message_embedding` table

File: `alembic/versions/0006_message_embedding.py`

Schema:

```sql
CREATE TABLE message_embedding (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mailbox_id  UUID NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
    message_id  UUID NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    embed_model TEXT NOT NULL,
    embed_dim   INT  NOT NULL,
    content_hash TEXT NOT NULL,   -- SHA-256 of (subject + "\n\n" + clean_text)
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   vector(1024) NOT NULL,
    UNIQUE (message_id, embed_model)
);

CREATE INDEX ON message_embedding
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX ON message_embedding (mailbox_id, embed_model);
```

Notes:
- `UNIQUE (message_id, embed_model)` enables idempotent upsert and multi-model
  coexistence in the future.
- `content_hash` lets the backfill skip messages whose text has not changed.
- `m=16, ef_construction=64` are conservative defaults; tune after eval.

Acceptance:
- Migration applies cleanly with `alembic upgrade head`.
- `alembic downgrade -1` drops the table and index cleanly.
- Unit test: migration round-trip passes.

### S7.2 — SQLAlchemy ORM model + Pydantic mapper

Files:
- `services/db/models.py` — add `MessageEmbedding` ORM class.
- `services/db/mappers.py` — add `embedding_to_row` / `row_to_embedding`.
- `packages/ekc_schemas/models.py` — add `MessageEmbeddingRecord` Pydantic model.

`MessageEmbeddingRecord` fields:
```python
message_id:   UUID
mailbox_id:   UUID
embed_model:  str
embed_dim:    int
content_hash: str
embedded_at:  datetime
embedding:    list[float]
```

Acceptance:
- Round-trip test: create a record, persist, reload, compare.
- `embedding` round-trips without float precision loss beyond pgvector tolerance.

### S7.3 — Embedding client seam

File: `services/retrieval/embed_client.py`

Protocol:

```python
class EmbedClient(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    @property
    def model(self) -> str: ...
    @property
    def dim(self) -> int: ...
```

Implementations:
- `VoyageEmbedClient` — wraps `voyageai.Client`. Uses `input_type="document"`
  for `embed_documents` and `input_type="query"` for `embed_query`.
  Reads `VOYAGE_API_KEY` from env. Never logs text content.
- `FakeEmbedClient` — deterministic test embedder. Returns seeded unit vectors
  based on a hash of the input text. Allows offline tests.

`VoyageEmbedClient` logging discipline:
- Log: model name, batch size, latency, token count (if returned by API).
- Never log: text content, subject, query string.

Acceptance:
- All tests use `FakeEmbedClient`. No test touches the Voyage API.
- `VoyageEmbedClient` has an integration test, skipped unless
  `VOYAGE_API_KEY` is set.
- The client raises `EmbedError` (not a raw HTTP exception) on failure.

### S7.4 — `RetrievalParams`

File: `services/retrieval/params.py`

```python
@dataclass
class RetrievalParams:
    embed_model:          str   = "voyage-4"
    embed_dim:            int   = 1024
    vector_top_k:         int   = 20
    fts_top_k:            int   = 20
    rerank_top_k:         int   = 10
    min_vector_score:     float = 0.60
    min_fts_score:        float = 0.0    # raise in eval if FTS noise is high
    vector_weight:        float = 0.6
    fts_weight:           float = 0.4
    recency_weight:       float = 0.05   # kept small so recency never dominates
    include_noise:        bool  = False
    include_sensitive:    bool  = False
    enable_reranking:     bool  = False   # also gated by ENABLE_RERANKING env
    project_boost:        float = 0.15
    person_boost:         float = 0.10
    recency_half_life_days: int = 180
```

All fields are dataclass defaults — injectable in tests, not hardcoded in logic.

### S7.5 — Idempotent backfill script

File: `scripts/embed_backfill.py`

CLI:
```text
python scripts/embed_backfill.py --mailbox-id <uuid> [--batch-size 64]
    [--model voyage-4] [--dry-run] [--confirm]
```

Behavior:
- Selects messages where `noise=false AND sensitivity = '{none}'` (default).
  Both gates are required: D12d excludes sensitive messages from embedding
  by default; there is no `--include-sensitive` override in S7 because no
  permission layer exists to govern it yet (see Q3).
- Skips messages already in `message_embedding` with matching `content_hash`.
- Embeds in batches of `--batch-size`.
- Upserts with `ON CONFLICT (message_id, embed_model) DO UPDATE`.
- Logs progress (message count, batch, latency) without logging body content.
- `--dry-run`: print counts and estimated API cost, persist nothing.

Privacy enforcement:
- Only `subject + "\n\n" + clean_text` is sent to the embedding API.
- Structured address fields (sender email, recipients) are never sent.
- See D12d incidental-PII caveat: body/subject may contain names or addresses
  as natural prose; S7 does not scrub them.

Acceptance:
- Re-running on an already-embedded mailbox produces zero new API calls
  (all content_hashes match).
- `--dry-run` exits 0 and prints counts without any DB write.
- Offline test uses `FakeEmbedClient`.

### S7.6 — Vector retrieval

File: `services/retrieval/vector.py`

```python
def vector_search(
    session,
    mailbox_id: UUID,
    query_embedding: list[float],
    params: RetrievalParams,
) -> list[RetrievalHit]: ...
```

SQL pattern:
```sql
SELECT m.id, m.message_id_header, m.thread_id, m.subject, m.clean_text,
       m.ts, m.sensitivity, m.noise,
       1 - (me.embedding <=> :qvec) AS vector_score
FROM message_embedding me
JOIN message m ON m.id = me.message_id
WHERE me.mailbox_id = :mid
  AND me.embed_model = :model
  AND m.noise = false           -- unless include_noise
  AND m.sensitivity = '{none}'  -- unless include_sensitive
ORDER BY me.embedding <=> :qvec
LIMIT :k;
```

Acceptance:
- Returns `RetrievalHit` list sorted by descending `vector_score`.
- Noise and sensitivity filters apply correctly.
- Works with `FakeEmbedClient` in offline tests.

### S7.7 — FTS / BM25 retrieval

File: `services/retrieval/fts.py`

The existing `message.clean_text_tsv` generated column covers `clean_text`
only. `subject` is not indexed for FTS. Migration 0006 adds a combined
generated column covering both:

```sql
-- Added in migration 0006 alongside message_embedding
ALTER TABLE message ADD COLUMN IF NOT EXISTS subject_clean_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(subject, '') || ' ' || coalesce(clean_text, ''))
    ) STORED;
CREATE INDEX ix_message_subject_clean_fts ON message USING gin (subject_clean_tsv);
```

Query translation: use `websearch_to_tsquery` for user-typed queries (handles
AND/OR/phrase/negation naturally without raising on malformed input). Fall back
to `plainto_tsquery` for programmatic queries where `websearch_to_tsquery`
semantics are unwanted.

```python
def fts_search(
    session,
    mailbox_id: UUID,
    query: str,
    params: RetrievalParams,
) -> list[RetrievalHit]: ...
```

Acceptance:
- Uses `subject_clean_tsv` and `websearch_to_tsquery`.
- Returns results with `fts_score` populated and `vector_score=None`.
- Handles empty or stop-word-only queries gracefully (returns empty list).
- Noise and sensitivity filters match the same defaults as vector search.

### S7.8 — Hybrid merge and deterministic rerank

File: `services/retrieval/hybrid.py`

Merge strategy:
1. Collect vector hits and FTS hits; deduplicate by `message_id`.
2. Normalize scores to [0, 1] within each pool before combining.
3. For each candidate compute:
   ```
   relevance = (vector_score * vector_weight) + (fts_score * fts_weight)
   boost     = project_boost + person_boost          (additive, not multiplicative)
   recency   = recency_weight * exp(-age_days / half_life_days)
   score     = relevance + boost + recency
   ```
   All weights (`vector_weight`, `fts_weight`, `recency_weight`) and boost
   values (`project_boost`, `person_boost`) are explicit fields in
   `RetrievalParams`, not inline constants. Default values:
   `vector_weight=0.6`, `fts_weight=0.4`, `recency_weight=0.05`, so
   recency cannot dominate relevance.
4. Sort descending by combined score; take top `rerank_top_k`.
5. If `params.enable_reranking` is True **and** `ENABLE_RERANKING` env is set,
   call the Voyage reranker as a post-processing step.

**Quality gate for non-vector paths:**
- Vector-only: discard hits below `min_vector_score`.
- FTS-only or hybrid with no vector component: discard hits below
  `min_fts_score` (new param, default `0.0` — accept any FTS hit, but
  the gate exists so it can be raised in eval if noise is high).
- After all filtering: if zero candidates remain → `InsufficientEvidence`.

Acceptance:
- Deterministic: same inputs always produce same ranked list.
- Reranker path is tested with a mock; never calls the API in unit tests.
- Boost and recency computations have isolated unit tests.
- Recency cannot push a low-relevance message above a high-relevance one
  (unit test: old high-relevance > recent low-relevance).

### S7.9 — `RetrievalHit` shape and evidence quality gate

File: `services/retrieval/contracts.py`

```python
@dataclass(frozen=True)
class RetrievalHit:
    message_id:        UUID
    message_id_header: str        # citation key
    thread_id:         UUID
    project_ids:       list[UUID]
    person_ids:        list[UUID]
    ts:                datetime
    subject:           str
    snippet:           str        # first 300 chars of clean_text
    vector_score:      float | None
    fts_score:         float | None
    rerank_score:      float
    source:            Literal["vector", "fts", "hybrid"]
    sensitivity:       list[str]
    noise:             bool
```

Evidence quality gate (in `services/retrieval/hybrid.py`):
- No hits → `InsufficientEvidence`
- All hits filtered out by noise/sensitivity → `InsufficientEvidence`
- All hits below `min_vector_score` (vector-only path) → `InsufficientEvidence`
- Otherwise → list of `RetrievalHit`

`InsufficientEvidence` is a typed return value, not an exception.

### S7.10 — Retrieval eval

Files:
- `services/retrieval/eval/fixtures.py`
- `services/retrieval/eval/run_eval.py`

Fixture shape per query:

```python
@dataclass
class RetrievalCase:
    query:                    str
    expected_headers:         list[str]   # must appear in top-k
    forbidden_headers:        list[str]   # must not appear
    expected_route:           Literal["l1_exact", "l2_fallback", "hybrid"]
    allow_sensitive_in_result: bool = False
```

Hard eval gates (exit nonzero if any fail):
- Every `expected_header` appears in the top-10 results.
- No `forbidden_header` appears at any rank.
- No sensitive message appears in results when `include_sensitive=False`.
- No noise message appears in results.
- Every returned `message_id_header` exists in the DB.
- `InsufficientEvidence` is returned for a deliberately unanswerable query.

Soft targets (reported, not blocking):
- Mean reciprocal rank (MRR) ≥ 0.6 across eval cases.
- Top-1 precision ≥ 0.5.

Eval runs against the smoke dataset mailbox (S6 smoke dataset + embeddings).

### S7.11 — Cover-for-me L2 upgrade

File: `services/api/routers/cover_for_me.py`

The endpoint signature (`POST /api/cover-for-me/{mailbox_id}`) and response
schema are **unchanged**. The internal query path changes:

Before S7:
```
query → L1 entity detection → structured evidence → synthesis
```

After S7:
```
query → L1 entity detection
          │
          ├──── always ────► L2 hybrid search (capped supporting evidence)
          │
     L1 hits? ──yes──► L1 primary evidence + L2 supporting evidence
          │                         │
          no ──────────────────────►│ L2 becomes the sole source
                                    │
                              synthesis with all cited evidence
```

Routing rule (locked, resolves Q5):
- L2 **always** runs alongside L1 once embeddings exist.
- L1 determines the *answer type* (person lookup, project state, etc.).
- L2 contributes capped supporting evidence (≤ `rerank_top_k` hits) regardless
  of whether L1 matched anything.
- When L1 returns zero structured hits, L2 becomes the primary source.
- "Insufficient evidence" fires only when both L1 and L2 return nothing.

Acceptance:
- Existing S5 tests pass unchanged (no API contract change).
- New tests: L2-only path (no L1 entity match) returns cited messages.
- "Insufficient evidence" path still returns correct response when both L1
  and L2 produce no usable results.

### S7.12 — Optional Voyage reranker integration

File: `services/retrieval/reranker.py`

Protocol:
```python
class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[RetrievalHit]) -> list[RetrievalHit]: ...
```

Implementations:
- `VoyageReranker` — calls `voyage-rerank-2.5`. Gated by
  `ENABLE_RERANKING=1` env var **and** `params.enable_reranking=True`.
- `NoOpReranker` — returns input unchanged; used when flag is off.

The reranker receives `query` and `[hit.snippet for hit in candidates]`.
It does NOT receive full `clean_text` — snippets only (privacy boundary).

Logging: model, candidate count, latency. Never log snippets or query text.

## Dependency Chain

```
S7.1 (migration)
  └── S7.2 (ORM model)
        └── S7.3 (embed client)
              ├── S7.4 (params)
              ├── S7.5 (backfill)
              ├── S7.6 (vector retrieval)
              ├── S7.7 (FTS retrieval)
              │     └── S7.8 (hybrid merge)
              │               └── S7.9 (contracts + quality gate)
              │                         ├── S7.10 (eval)
              │                         └── S7.11 (cover-for-me upgrade)
              └── S7.12 (optional reranker — parallel with S7.6-S7.11)
```

## Locked Decisions (formerly open questions)

All seven questions are resolved. No open questions remain for S7.

**Q1 — Voyage AI DPA:** Hard gate before any real customer mailbox; not before
smoke/demo use. Confirm DPA coverage before the first non-demo deployment.
S7 coding against the smoke dataset is unblocked.

**Q2 — API key management:** `VOYAGE_API_KEY` via env var is acceptable for
dev/demo (same pattern as `GMAIL_TOKEN`). Secrets manager required before any
customer data. Decide the specific secrets backend (AWS/GCP) before the first
non-demo deployment; that decision is out of S7 scope.

**Q3 — Sensitive embedding override:** No override in S7. Sensitive messages
(`sensitivity != ['none']`) are excluded from embedding and retrieval by
default. Revisit after a permission model exists. S7.5 backfill reflects this.

**Q4 — Thread-context neighbor expansion:** Deferred to S8 unless S7 retrieval
eval fails badly. S7 retrieves at message level only.

**Q5 — L1/L2 routing:** Resolved in S7.11 routing rule. Always hybrid once
embeddings exist: L1 primary, L2 capped supporting evidence on every query,
L2 sole source when L1 has no match.

**Q6 — voyage-4 dimension:** Locked at 1024 (default) for S7. The SDK
parameter is `output_dimension` (singular — confirmed in Voyage docs). Optional
truncation to 256/512/2048 is available but not used in S7. Revisit at scale.

**Q7 — Backfill scope:** Embed all non-noise, non-sensitive messages. Not
project-only. The retrieval surface should not be limited to pre-classified
project mail; unexpected relevance is part of the value.

## Proposed Changes to Existing Specs Before Code Starts

| Doc | Change |
|---|---|
| `docs/decisions.md` | D12 added. ✓ done |
| `AGENTS.md` §6 | "external query router" note rescinded; `services/retrieval` now local. ✓ done |
| `README.md` | Status updated to S7.1–S7.10 complete, S7.11 next. ✓ done |
| `packages/ekc_schemas/models.py` | `MessageEmbeddingRecord` added; `SCHEMA_VERSION=0.2.0`. ✓ done (S7.2) |
| `services/db/models.py` | `MessageEmbedding` ORM class added. ✓ done (S7.2) |
| `services/db/mappers.py` | `embedding_to_row` / `row_to_embedding` added. ✓ done (S7.2) |
| `spec 04` ticket 4.5 | Resolved by D12b. Migration 0006 applied. ✓ done (S7.1) |
| `docs/implementation-plan.md` | L2 moved from Deferred to Implemented/in-progress. ✓ done |

## Definition of Done

S7 is complete when:

- Migration 0006 applies and reverts cleanly.
- `embed_backfill.py` runs without error on the smoke-dataset mailbox.
- `RetrievalHit` citations are all verifiable in the DB.
- Retrieval eval hard gates pass (expected headers in top-10, no sensitive
  leakage, no noise, `InsufficientEvidence` on unanswerable query).
- Existing full suite remains green. Current reported baseline after S7.10: 437 passed, 1 skipped.
- Cover-for-me endpoint returns L2-backed cited evidence on a query that
  has no L1 entity match.
- Frontend build is unchanged (no new UI in S7).
- D12 privacy posture is enforced in code (embed client never logs text;
  sensitivity filter is on by default).
