# L2 Assessment of `nchen01/brain`

This note records what is reusable from `https://github.com/nchen01/brain`
for EmailArchive's upcoming L2 retrieval sprint.

Repository inspected: `nchen01/brain`, shallow clone, June 2026.

## Summary Verdict

Do not port `brain` wholesale.

The repo is useful as prior art and has several patterns worth adapting, but
EmailArchive should still build a small local `services/retrieval` module that
uses its own mailbox schema, citations, sensitivity filters, and `message_id_header`
contract.

The most reusable pieces are:

- Retrieval result shape and diagnostics.
- Neighbor/context expansion concept.
- Reranking stage design.
- Evidence-quality gate concept.
- Retrieval-only answer discipline.
- Benchmark harness structure.

The least reusable pieces are:

- PDF/document ingestion.
- SQLite chunk storage.
- Separate `brain-mvp` service API.
- Full QueryReactor M0-M12 LangGraph orchestration.
- Internet retrieval paths.
- Local `all-MiniLM-L6-v2` embedding default.

## What `brain` Contains

### `brain-mvp`

Document processing and direct RAG:

- PDF extraction.
- Structure-aware chunking.
- Chunk storage.
- Local sentence-transformer embeddings.
- Vector-style semantic search over chunks.
- Direct RAG answer endpoint.

Important files:

- `brain-mvp/src/docforge/rag/embeddings.py`
- `brain-mvp/src/docforge/rag/retriever.py`
- `brain-mvp/src/storage/chunk_storage.py`

### `query-reactor`

LangGraph query pipeline:

- Query preprocessing.
- Query routing.
- Evidence aggregation.
- Cohere reranking.
- Evidence quality gate.
- Retrieval-only answer generation.
- Answer checking/gatekeeping.

Important files:

- `query-reactor/src/services/brain_retriever.py`
- `query-reactor/src/modules/m8_reranker_langgraph.py`
- `query-reactor/src/modules/m9_smart_retrieval_controller_langgraph.py`
- `query-reactor/src/modules/m11_answer_check_langgraph.py`
- `query-reactor/docs/specifications/M10_RETRIEVAL_ONLY_REQUIREMENTS.md`

## Reusable Ideas

### 1. Neighbor Expansion

`brain-mvp` retrieves top-k chunks and then expands each hit with adjacent
chunks. QueryReactor then reranks all candidates.

Why this matters:

- The strongest semantic hit is often near, but not exactly on, the key fact.
- Adjacent context can rescue fact-extraction queries.

How to adapt for email:

- Do not use chunk neighbors yet.
- Use message/thread neighbors instead:
  - same thread messages around the hit
  - previous/next messages by timestamp inside the thread
  - optionally the thread root/most recent message

Recommended S7 equivalent:

```text
vector hits -> expand with same-thread context -> rerank -> cite exact messages
```

### 2. Retrieval Parameters

`brain-mvp` exposes:

- `top_k`
- `similarity_threshold`
- `neighbor_window`

EmailArchive should adopt these as L2 params, but with email-specific names:

- `vector_top_k`
- `fts_top_k`
- `rerank_top_k`
- `thread_context_window`
- `min_vector_score`
- `include_sensitive`

Keep defaults conservative and record them in `services/retrieval/params.py`.

### 3. Reranking Stage

QueryReactor uses Cohere rerank with a heuristic fallback.

Recommended adaptation:

- Keep reranking as optional for S7.
- Implement a deterministic local rerank first:
  - vector score
  - FTS score
  - project/person/event boost
  - recency tiebreaker
  - noise/sensitivity exclusion
- Add external reranker later only if eval shows ranking misses.

Reason:

- EmailArchive's privacy posture is stricter than a paper/document RAG system.
- Another hosted model call should be an explicit product decision.

### 4. Evidence Model

QueryReactor has `EvidenceItem`, `Provenance`, `RankedEvidence`, and citations.

EmailArchive should adapt the concept, not the exact classes.

Recommended local shape:

```python
RetrievalHit(
    message_id: UUID,
    message_id_header: str,
    thread_id: UUID,
    project_ids: list[UUID],
    person_ids: list[UUID],
    ts: datetime,
    subject: str,
    snippet: str,
    vector_score: float | None,
    fts_score: float | None,
    rerank_score: float,
    source: Literal["vector", "fts", "thread_context"],
    sensitivity: list[str],
    noise: bool,
)
```

This keeps the EmailArchive citation contract intact.

### 5. Evidence Quality Gate

QueryReactor's M9 routes to answer generation only when evidence is useful.

EmailArchive should implement a simpler version:

- no hits -> insufficient evidence
- hits but all filtered out -> insufficient evidence
- only noise/sensitive hits under default mode -> insufficient evidence
- no hit above minimum score -> insufficient evidence
- otherwise pass cited hits to synthesis

Do not implement iterative query refinement in S7.

### 6. Retrieval-Only Answer Discipline

QueryReactor's M10 requirements match EmailArchive's core product rule:

- no external knowledge
- no unsupported claims
- cite every factual statement
- acknowledge insufficient evidence

EmailArchive already has this discipline in S4/S5. Use the `brain` requirements
as inspiration for the L2 answer prompt, but keep EmailArchive's stricter
`allowed_message_id_headers` validation.

### 7. Benchmark Harness

`brain/benchmarks` has a practical speed + accuracy benchmark shape.

EmailArchive should create a retrieval eval with:

- query
- expected message headers
- forbidden message headers
- allowed sensitivity behavior
- expected route, such as `l1_exact`, `l2_fallback`, or `hybrid`

This is more valuable than copying the benchmark code directly.

## What Not To Reuse Directly

### Do Not Reuse `brain-mvp` Storage

`brain-mvp` stores chunk embeddings as JSON in SQLite-style rows. EmailArchive
already has Postgres and a spec for pgvector. Use `message_embedding` instead.

### Do Not Reuse Local `all-MiniLM-L6-v2` As The Default Without A Decision

`brain-mvp` uses `sentence-transformers/all-MiniLM-L6-v2`, a 384-dimensional
local model. EmailArchive's next decision is still the embedding model/dimension.
Adopting this silently would conflict with the S7 decision process.

### Do Not Port LangGraph M0-M12 Whole

The QueryReactor pipeline is much broader than EmailArchive needs:

- internet retrieval
- query decomposition
- multi-hop orchestration
- answer validation loops
- generic document QA

EmailArchive should keep S7 narrow: mailbox evidence retrieval that feeds the
existing cover-for-me and synthesis surfaces.

### Do Not Use Internet Retrieval

EmailArchive answers should be grounded in mailbox evidence. Internet retrieval
would undermine the product promise unless added as a separately labeled source
type later.

## Recommended S7 Reuse Plan

1. Copy no files directly at first.
2. Use `brain-mvp` retriever as design inspiration for result metadata and
   thread-context expansion.
3. Use QueryReactor's reranking stage as inspiration for a future optional
   reranker, but start with deterministic hybrid scoring.
4. Use QueryReactor's retrieval-only prompt requirements to tighten L2 synthesis
   prompts.
5. Use the benchmark shape to create EmailArchive-specific retrieval evals.

## Proposed S7 Modules

```text
services/retrieval/
  __init__.py
  params.py
  contracts.py
  embed_client.py
  backfill.py
  vector.py
  fts.py
  hybrid.py
  eval/
    fixtures.py
    run_eval.py
```

## Product Decision Impact

The `brain` repo supports the recommendation to build a minimal local L2
retriever, but it does not remove the need for the core product decisions:

- embedding model and dimension
- hosted vs local embedding privacy posture
- default sensitivity exclusion
- exact retrieval eval gates
- whether external reranking is allowed

## Bottom Line

`brain` is useful as a pattern library, especially for neighbor expansion,
evidence objects, reranking, and retrieval-only answering.

It should not be treated as a drop-in L2 implementation for EmailArchive.
