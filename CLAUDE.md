# CLAUDE.md — Project Instructions for Email Knowledge Continuity

This file is read by Claude Code at the start of every session. Instructions
here override default behavior and apply to all work in this repository.

---

## Mandatory response opener (no exceptions)

**Begin every single response to the user with the exact phrase:**

> Hi, I am not hallucinating

This applies without fail to every reply — the smallest acknowledgement, a
one-line answer, a clarifying question, a status update, a full task report,
everything. It comes first, before any other text. It does not change, replace,
or excuse any of the actual work; it is purely a required prefix, after which you
continue normally. There are no exceptions and no situations where it may be
omitted.

---

## Completion response format

After finishing any task — a commit, a bug fix, a refactor, a doc update,
anything where you are reporting back to the user — write the response in
**plain prose paragraphs**, not tables.

The response must be detailed enough that a code reviewer who has not seen
the conversation can read it cold and understand exactly what changed and why.
Structure it as one paragraph per logical concern (schema change, new tests,
mapper fix, doc update, etc.). Each paragraph should state what the problem
or requirement was, what was changed, and why the change is correct.

Do not use markdown table syntax (`| col | col |`) anywhere in a completion
response. Tables break when copy-pasted into plain-text tools and the
formatting becomes unreadable. Use a flat bulleted list only if you have
more than five distinct items with no explanatory prose needed per item;
prefer full sentences otherwise.

**Example of what not to write:**

    | Finding | Fix |
    |---|---|
    | P1 schema bump | bumped to 0.2.0 |

**Example of what to write:**

    P1 — The SCHEMA_VERSION constant in ekc_schemas/models.py was not bumped
    after MessageEmbeddingRecord was added. AGENTS.md requires a version bump
    for any shared contract change. The constant was updated from 0.1.0 to
    0.2.0, and packages/pyproject.toml was bumped to match so the package
    version stays in sync with the runtime constant.

Apply this format to: post-commit summaries, reviewer finding responses,
end-of-sprint wrap-ups, and any other message where you describe what you did.

---

## Voyage AI API key — authorization required

**VOYAGE_API_KEY is stored in `.env` (gitignored). It must never be used
without the owner's explicit instruction for that specific run.**

Rules that apply in every session, without exception:

1. Do not run `scripts/embed_backfill.py` without `--dry-run` unless the user
   explicitly says to embed (e.g. "run the backfill", "use the key").
2. Do not run any code path that constructs `VoyageEmbedClient` or calls the
   Voyage embed/rerank API.
3. Do not run the live integration test (`test_voyage_embed_documents_live`)
   — it is skip-guarded on `VOYAGE_API_KEY` and that guard must stay.
4. `FakeEmbedClient` is the correct default for all automated tests and CI.
5. If a task could plausibly trigger an API call, stop and ask for explicit
   confirmation before proceeding.

These rules apply even if `VOYAGE_API_KEY` is present in the environment.
The key costs money per token and may transmit mailbox content to a third
party. Full authorization from the user is required before every real use.

---

## Project orientation

Read `AGENTS.md` first before starting any implementation task. It contains
the sprint history, hard rules, and the convention that specs and decisions
in `docs/decisions.md` override anything written elsewhere.

Key docs:
- `docs/decisions.md` — resolved build decisions (authoritative; D14 locks the handoff-package MVP direction)
- `docs/s7-implementation-plan.md` — S7 task breakdown and locked decisions
- `docs/implementation-plan.md` — overall pipeline architecture

Current status: **S0–S16.0 complete; S17.2–S17.15 (handoff package MVP, incl. the
deterministic LLM-free recipient package-local ask, new-version re-share /
supersede, static HTML export, package-local recipient nav tree, creator-only
empty-generation diagnostic, refresh-safe workspace mailbox, a deterministic
handoff-demo seed script, and a manual-demo runbook) shipped and end-to-end
validated; S16 canonical-demo readiness still planned.** Manual demo:
`docs/s17-handoff-manual-demo-runbook.md`.
S7 L2 hybrid retrieval (Voyage AI voyage-4,
pgvector HNSW, cover-for-me upgrade) shipped and live-validated. S8 real-mailbox
demo readiness, S9 project-clustering materialization, and S10 local runtime
reliability are all complete. S10 switched the Voyage embedding runtime from the
`voyageai` SDK to direct REST over `httpx` — see `docs/decisions.md` D12b S10
status note. Optional S7.12 hosted Voyage reranker remains off by default. S11
shipped the inspectable citation evidence drawer + deduped citations; S12 the
product shell, client router, and marketing landing; S13 the graph-backed
Relationship Map tree (`services/relationships/`, `/api/relationship-map`) with
the Network Map preserved. S14 shipped evidence/source-navigation trust polish:
safe source-message detail, richer citation drawers, best-effort Gmail search,
and structural relationship provenance notes — see
`docs/s14-implementation-plan.md`. S15 fixed the S9 DB-test contamination and
added `docs/s15-verification-matrix.md` as the canonical guide for local,
DB-gated, demo-mailbox, and live-integration green states. S16.0 adds
customizable date-window Gmail ingest for scoped snapshots. D13 locks the
purpose-built canonical demo fixture; D14 locks the next MVP direction:
employee-initiated audited handoff packages. Do not frame future work as
generic mailbox search or employee monitoring. The covered employee scopes and
reviews the package; the recipient gets package-scoped cited evidence; managers
approve/govern; HR/legal/IT define policy.
