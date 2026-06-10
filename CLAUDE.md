# CLAUDE.md — Project Instructions for Email Knowledge Continuity

This file is read by Claude Code at the start of every session. Instructions
here override default behavior and apply to all work in this repository.

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

## Project orientation

Read `AGENTS.md` first before starting any implementation task. It contains
the sprint history, hard rules, and the convention that specs and decisions
in `docs/decisions.md` override anything written elsewhere.

Key docs:
- `docs/decisions.md` — D1–D12 resolved build decisions (authoritative)
- `docs/s7-implementation-plan.md` — S7 task breakdown and locked decisions
- `docs/implementation-plan.md` — overall pipeline architecture

Current sprint: **S7 — L2 hybrid retrieval** (Voyage AI voyage-4, pgvector
HNSW, cover-for-me upgrade). S7.1 and S7.2 are complete. Next: S7.3 embed
client seam.
