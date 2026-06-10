# L2 Product Decisions and Drift Guardrails

> **Superseded by D12.** The decisions in this document were the pre-S7
> options analysis. The locked decisions are in `docs/decisions.md` (D12)
> and `docs/s7-implementation-plan.md`. In particular, the recommended
> embedding model here (`text-embedding-3-small`, 1536-dim) was not
> adopted — S7 uses Voyage AI `voyage-4` at 1024 dimensions. Read this
> doc for context and rationale history only.

This note records the product decisions needed before implementing L2 retrieval.
It is written for the product lead and future engineers so the project does not
drift from the existing L0 -> L1 -> L2 -> L3 architecture.

## Current State

S0 through S6 are complete. The current MVP has:

- L0 Gmail ingest, normalization, noise and sensitivity tagging.
- L1 identity resolution, relationship graph, roles, project clustering, and events.
- S4 synthesis for project and contact summaries.
- S5 cover-for-me as a bounded L1-only query over Person, Project, Event, Edge, and Thread.
- S6 live quality tooling validated against a mixed Gmail inbox plus injected smoke dataset.

L2 is not built yet. The `message_embedding` table is intentionally deferred
until the embedding model and dimension are chosen.

## Already Decided

- L2 uses Postgres plus pgvector first, not a separate vector DB.
- L2 retrieval must preserve citations via `Message.message_id_header`.
- Cover-for-me keeps the same surface API when upgraded from L1-only to hybrid L1 + L2.
- `message_embedding` is the next storage migration, expected as migration `0006`.
- Retrieval defaults must honor noise and sensitivity filters.
- The differentiated product value remains L1 structure; L2 is recall plumbing, not the product thesis.

## Decisions To Make Before Coding

### 1. Embedding Model and Dimension

Pick one model and one dimension for the MVP. The recommended default is:

- `text-embedding-3-small`
- 1536 dimensions
- cosine similarity
- `message_embedding.embedding vector(1536)`

Reasoning:

- It fits pgvector `vector` HNSW limits cleanly.
- It avoids the complication of 3072-dimensional embeddings.
- It is likely good enough for the first retrieval eval.
- It gives a clear migration/backfill target.

Avoid silently using the current local `sentence-transformers/all-MiniLM-L6-v2`
default unless the team explicitly decides to self-host embeddings.

### 2. Privacy Posture

Decide whether sending `Message.clean_text` to a hosted embedding API is acceptable
for demo and future customer data.

If yes, record the provider/model/data handling in `docs/decisions.md`.

If no, use a local embedding model and accept the extra deployment, quality, and
hardware work.

### 3. Content To Embed

Recommended S7 scope:

- Embed message-level text only.
- Use subject plus clean body text.
- Do not embed raw MIME.
- Do not embed attachments in S7.
- Do not introduce paragraph-level chunking yet.

Message-level chunks keep citation and deletion semantics simple.

### 4. Retrieval Behavior

Recommended behavior:

- L1 exact entity routing remains first.
- L2 is used as fallback when no known Person or Project entity matches.
- L2 may also provide supporting evidence for matched project/person answers.
- Low-confidence retrieval should return insufficient evidence, not bluff.

### 5. Hybrid Ranking Policy

Recommended defaults:

- Exclude `noise=true`.
- Exclude non-`none` sensitivity by default.
- Boost messages linked to matched projects, people, and events.
- Use recency as a tie-breaker, not as proof of importance.
- Return enough metadata for debugging: vector score, FTS score, filters applied, and source message headers.

### 6. Retrieval Eval

Do not judge L2 by whether embeddings “look plausible.” Define an eval before
or alongside implementation.

Minimum eval checks:

- Top-k contains at least one expected relevant message.
- No sensitive messages appear under default filters.
- No noise-heavy result set for project-relevant queries.
- Every returned citation header exists in the DB.
- The system returns insufficient evidence when retrieval is weak.

### 7. Retrieval Ownership

There is a docs conflict:

- `AGENTS.md` says an existing query router owns L2 and not to build retrieval.
- The current repo does not have a local `services/retrieval` implementation.
- Recent docs say the next step is to proceed to L2.

Decision needed:

- If an external router exists, document how to integrate it.
- If not, explicitly decide to build a minimal local `services/retrieval` module.

Recommendation: build a minimal local retriever now and update `AGENTS.md` plus
`docs/decisions.md` so future engineers do not wait for a nonexistent router.

## Recommended D12

Add a new decision:

**D12 — L2 embedding model and retrieval ownership**

Decision:

- Use `text-embedding-3-small` at 1536 dimensions for S7.
- Store message embeddings in Postgres via pgvector.
- Implement a minimal local `services/retrieval` hybrid retriever.
- Keep external router integration deferred until its contract exists.

Scope:

- S7 only.
- Future model changes require a new migration and backfill.

## Recommended S7 Build Order

1. Add D12 and an S7 implementation plan.
2. Add migration `0006_message_embedding.py`.
3. Add SQLAlchemy `MessageEmbedding`.
4. Add an embedding client seam with injectable test embeddings.
5. Add an idempotent backfill script for mailbox embeddings.
6. Add vector retrieval plus Postgres FTS retrieval.
7. Add deterministic hybrid merge and rerank.
8. Add retrieval eval fixtures and hard gates.
9. Upgrade cover-for-me to use L1 routing first and L2 fallback/support second.
10. Update docs and UI copy around insufficient evidence.

## Things To Change Before S7

- Update README status from S6 pending to S6 complete.
- Resolve the `AGENTS.md` contradiction about external query router ownership.
- Align clustering embedding defaults with the chosen L2 model, or keep production clustering explicitly disabled.
- Decide whether `message_embedding` stores `embed_model`, `embed_dim`, `content_hash`, and `embedded_at`.
- Keep `text-embedding-3-large` out of the MVP unless the team intentionally shortens dimensions or uses `halfvec`.

## Product Guardrail

Do not let L2 become “chat with mailbox.”

The product promise is grounded handoff intelligence. L2 should improve recall
for cited evidence. It should not weaken the rule:

**No citation, no claim.**
