# S16 — Demo Readiness Plan

**Status:** 🔒 Plan locked (2026-07-12). A **bounded demo-readiness sprint** —
product-narrative and demo-flow polish on top of the S0–S15 engine. **Not** a
real-data quality sprint; **no** core clustering / retrieval / synthesis
algorithm changes.

> **Implementation gate.** Implementation of S16 (tickets S16.1–S16.10) **must
> not begin until this docs PR is merged to `master` or explicitly approved.**
> This plan is committed and pushed **first**; coding then starts from the
> accepted plan. Until then this document is planning-only.

This plan is the output of an S16 design grilling. The pivotal decision is
recorded as **D13** in `docs/decisions.md` (canonical demo runs on a purpose-built
fixture; `puluo` is validation only). Terms in _italics_ on first use are defined
in the [Glossary](#glossary).

## 1. Purpose & win condition

Make the app demonstrably **trustworthy for coverage handoff** to a design
partner / coverage buyer (a manager). The win condition is a single sentence:

> **"I'd trust a stand-in to use this."**

Polish bar = **clarity + evidence + honesty**, low tolerance for hand-waving.
Everything below is judged against that win, not against feature breadth.

## 2. The demo spine — a coverage-handoff capability tour with evidence-trust as the hero

The demo walks one continuous _coverage handoff story_ in seven beats:

1. **This mailbox is ready for handoff.** (Overview readiness strip.)
2. **Here are the projects / people / relationships worth understanding.** (Overview briefing.)
3. **Ask a coverage question.** (Cover-for-me, recommended question.)
4. **Get a cited answer.** (Grounded synthesis.)
5. **Inspect the source evidence.** (S14 evidence drawer / source-message detail.)
6. **Explore the relationship / project structure.** (Relationship Map, Project view.)
7. **Unknown / sensitive questions fail safely.** (Honest no-evidence, no _existence oracle_.)

**Anti-goals (hard constraints):**
- Landing is **not** a generic AI-search pitch.
- Overview is **not** a passive dashboard.
- Evidence is **never** a secondary detail — the evidence trail is the hero,
  woven through the whole journey.

## 3. Scope boundaries

**In scope:** frontend (Landing, Overview, fail-safe copy, aggregate privacy
indicator), docs, demo-fixture generation, the pre-demo validation script, and
**small** backend presentation/support endpoints (the citation-honest fallback
path + operator flag).

**Out of scope (defer to S17+):**
- Core clustering / retrieval / synthesis **algorithm** changes.
- The `puluo`-exposed real-data quality gaps: noise filtering, confidence
  gating, project labeling. Record any newly-found ones as S17 follow-ups.
- `puluo` re-work of any kind (it stays as the validation path, untouched).
- M365, multi-mailbox, offboarding motion, object store, etc. (already deferred).

**Rule of thumb:** S16 fixes only demo-blocking bugs or presentation issues. If
`puluo` surfaces a clustering/noise/labeling weakness, it is an S17 ticket, not
an S16 fix.

## 4. Demo data (see D13)

The canonical demo runs on a purpose-built _demo fixture_ (distinct from the
_test fixture_). `puluo` remains the messy real-mailbox validation path only.

### 4.1 Fixture principles
- **Deterministic** generation (re-seed anytime; reproducible).
- **Authored-to-outcome:** iterate realistic content until the **real** pipeline,
  run end to end (L0 ingest → L1 enrichment → project clustering/materialization
  → embeddings → L2 retrieval → L3 synthesis), yields the intended structure and
  cited answers. Tuning applies to the **input emails** only; the structure and
  answers are still derived by the pipeline, never hardcoded (the citation-honest
  fallback of §5 is the one narrow, allow-list-bound exception).
- **Separate** from `fixtures/mailbox.json` and its gold labels (untouched).
- **Realistic, not fake-perfect** — enough mess to exercise real behavior.
- Does **not** depend on `puluo`'s distribution.

### 4.2 Fixture shape (target)
- 5–8 coherent projects; 40–100 messages total; multi-message threads.
- Recurring people across projects; a few external orgs / vendors / customers.
- Direct exchanges **and** group threads.
- ≥1 **incident** thread (proposed / did / outcome events).
- ≥1 **vendor/legal-sensitive** thread — excluded from evidence.
- ≥1 **HR-sensitive** thread — excluded from evidence.
- A few noise / newsletter messages (to prove noise handling).
- Clear cited decisions / blockers / outcomes.
- Known _canonical demo questions_ with expected cited evidence.

### 4.3 Isolation & identity
- Dedicated demo mailbox identity: **`demo.handoff@acme.corp`**.
- Visible company domain: **`acme.corp`** (chosen to be instantly recognizable
  in source evidence, and distinct from the test fixture's `acme.com`).
- Deterministic prefixed Message-IDs, e.g. **`demo-s16-nexus-auth-001@mail.acme.corp`**.
- Ground truth (expected projects/people/evidence) stored **separately** from
  the fixture and from the existing gold labels.

## 5. Peak execution — pre-warmed live + citation-honest fallback

Beats 3–5 hit live Voyage retrieval + Anthropic synthesis. To be authentic yet
un-fail-able on stage:

- **Pre-warm:** back-fill the demo mailbox's `voyage-4` embeddings once (one
  authorized run — CLAUDE.md Voyage rule).
- **Live at demo time:** synthesis runs live so the buyer sees a real cited
  answer.
- **_Citation-honest fallback_:** a stored answer per _canonical demo question_,
  used only if the live call fails / lags / whiffs. It:
  - fires **only** for canonical demo questions (never arbitrary input);
  - cites **real fixture `message_id_header` values**;
  - does **not** bypass the citation allow-list filtering;
  - does **not** expose sensitive / excluded content;
  - surfaces a **subtle operator/debug label** when active — not a big
    user-facing "fake answer" banner.

- **Pre-demo validation pass** (script) confirms, for each canonical question:
  retrieval returns the expected evidence, live synthesis succeeds, citations
  are valid, the source drawer opens, the no-evidence query behaves, and the
  sensitive query does not leak.

## 6. Fail-safe behavior (unknown & sensitive) — preserve the S14 boundary

The guardrail must be legible **without** becoming an _existence oracle_ (S14
closed the per-message existence oracle deliberately).

- **Unknown query →** "No email evidence was found for this query. Try asking
  about a specific project, person, or decision in this mailbox."
- **Sensitive-only query →** the **same** no-evidence language. Never say (or
  imply) that sensitive / HR / legal / restricted / protected content exists on
  that topic.
- **Mixed query (some safe evidence) →** answer only from the safe evidence.
- **Aggregate privacy posture →** shown **globally** in Status / Readiness:
  "Sensitive categories are excluded from retrieval and evidence by default."
  This is a global posture statement, not a per-query oracle.
- **Evidence drawer →** never exposes excluded messages.

S16 polishes this **language and placement**; it must not weaken the S14 trust
boundary.

## 7. Surface-by-surface UX plan

### 7.1 Landing (`/`)
Lead with the **coverage-handoff pain**, not AI search.
- Primary message: **"When someone steps away, their mailbox should not become a black box."**
- Hierarchy: (1) coverage-handoff pain → (2) what the product creates (a cited
  map of projects, people, decisions, blockers, source evidence) → (3)
  evidence/trust proof → (4) CTA.
- Primary CTA: **"Open the handoff workspace"** / "Start handoff review".
- **Banned phrasings:** "chat with your email", "AI search for your inbox",
  "unlock knowledge with AI", anything that reads as a generic RAG app.

### 7.2 Overview (`/app`) — the _handoff briefing_
Reframe from passive dashboard to a successor's day-one brief. Primary action:
start the briefing / ask the recommended coverage question. Structure:
- **Readiness strip:** mailbox loaded · embeddings present · synthesis ready ·
  evidence available (reuses `/api/preflight`; never blocks on it).
- **Briefing summary:** "Here are the projects, people, and relationships most
  worth understanding."
- **Recommended next question:** one high-confidence Cover-for-me prompt tied to
  the fixture (finalized after the fixture + validation exist — see tickets).
- **Sections:** (1) Projects needing attention, (2) People to know, (3) Recent
  decisions / blockers, (4) Relationship Map entry point, (5) Evidence/trust
  reminder.
- Guided next-step actions live **inside** the briefing frame (not a separate
  walkthrough).

### 7.3 Cover-for-me + evidence (beats 3–5)
- Recommended question runs live (with citation-honest fallback).
- Cited answer → clickable citation chips → S14 evidence drawer (subject /
  sender / date / snippet / Message-ID, copy Message-ID, best-effort Search in
  Gmail). Evidence is foregrounded, not hidden.

### 7.4 Relationship Map / Project view (beat 6)
- Owner tree default; project tree structural edges show provenance notes (S14),
  never fabricated Message-IDs. The fixture's relationship density must make
  these views look meaningful.

### 7.5 Status / Readiness (beat 7 support)
- Host the aggregate privacy indicator ("Sensitive categories are excluded from
  retrieval and evidence by default").

## 8. Dependency-ordered tickets

Ordered so the fixture and validation come **before** anything that needs real
message IDs and real retrieval behavior (canonical questions, the Overview
recommended question).

- **S16.1 — Demo fixture (authored-to-outcome).** Author `fixtures/demo_mailbox.json`
  + a deterministic generator; seed into a dedicated demo mailbox via the real
  pipeline; iterate content until clustering yields the §4.2 shape (5–8 projects,
  incident + HR-sensitive + vendor/legal-sensitive threads, noise, relationship
  density). Store ground truth separately. **Done:** the real pipeline produces
  the target structure deterministically; `fixtures/mailbox.json` + gold labels
  untouched; a seed script creates the demo mailbox.
- **S16.2 — Demo embeddings backfill (authorized).** One authorized `voyage-4`
  backfill for the demo mailbox. **Done:** `preflight --mailbox-id <demo>` reports
  embeddings present; no other mailbox touched.
- **S16.3 — Demo validation script.** Extends the S8 smoke-eval / S15 matrix
  patterns: per canonical candidate question, assert retrieval returns expected
  evidence, live synthesis succeeds, citations valid, drawer opens, no-evidence
  behaves, sensitive query does not leak. This is also the tool used to discover
  which questions retrieve cleanly. **Done:** script runs green against the demo
  mailbox and prints a demo-readiness verdict. **Depends:** S16.1, S16.2.
- **S16.4 — Canonical demo questions + fallback answers.** Using S16.3, finalize
  the small canonical question set and each expected cited evidence set; author
  citation-honest fallback answers citing real fixture `message_id_header`s.
  **Done:** canonical set + fallbacks committed and validated by S16.3.
  **Depends:** S16.1–S16.3.
- **S16.5 — Citation-honest fallback path (backend).** Add the demo-fallback path
  + operator/debug flag to `cover_for_me` per §5 (canonical-only; allow-list
  enforced; no sensitive exposure; subtle operator label). **Done:** live path
  unchanged for real answers; fallback fires only for canonical questions and
  passes the allow-list; operator label visible only in operator/debug context.
  **Depends:** S16.4.
- **S16.6 — Fail-safe language (unknown + sensitive).** Unify the no-evidence
  copy (§6); mixed queries answer safe-only; drawer never exposes excluded.
  **Done:** unknown and sensitive-only queries return identical no-evidence copy;
  no per-topic sensitive confirmation anywhere.
- **S16.7 — Aggregate privacy indicator (Status/Readiness).** Global posture line
  per §6. **Done:** indicator present on Status/Readiness; not per-query.
- **S16.8 — Overview → handoff briefing.** Implement §7.2, wiring the recommended
  question from S16.4. **Done:** Overview shows readiness strip, briefing summary,
  recommended question, the five sections, and the relationship-map entry point.
  **Depends:** S16.4.
- **S16.9 — Landing reframe.** Implement §7.1 (pain-led hierarchy, CTA, banned
  phrasings removed). **Done:** Landing leads with coverage-handoff pain; no
  banned phrasing; CTA opens the workspace.
- **S16.10 — Demo runbook + verification-matrix update.** Add a "demo green" tier
  to `docs/s15-verification-matrix.md` (demo mailbox + validation script + the
  fallback/operator label), plus a short demo runbook (the 7-beat script and the
  pre-demo checklist). **Done:** an operator can run the demo from the doc alone.
  **Depends:** S16.1–S16.9.

**Verification for S16 overall:** backend targeted tests + full DB-gated suite
stay green (no regressions); frontend build passes; the S16.3 validation script
reports demo-ready; and a manual walk of the 7 beats against the demo mailbox
matches this plan. `puluo` remains green as the real-mailbox validation path.

## 9. Open execution items (flow from the above; not blocking decisions)
- Exact canonical questions + the single Overview recommended question — finalized
  in S16.4 (need real fixture IDs + observed retrieval).
- The hero project for the beat-6 drill-down and the specific sensitive-thread
  topics — chosen during fixture authoring (S16.1).
- Operator-label placement and the validation-script output format — S16.3/S16.5.

---

## Glossary

Terms this sprint sharpened. Kept here (not a standalone `CONTEXT.md`) to match
this repo's `docs/`-centric convention.

- **Coverage handoff story** — the canonical demo narrative: a stand-in takes
  over a departed/absent owner's mailbox and, in minutes, learns who to ask,
  what's owned, and each project's state — every answer cited. The product's
  wedge (`implementation-plan.md` §2–§3), and the spine S16 optimizes for.
- **Handoff briefing** — the Overview (`/app`) reframed as a successor's day-one
  brief (readiness verdict + who/what matters + a recommended first question),
  as opposed to a passive stat dashboard. An active surface that tells the user
  what to do next.
- **Demo fixture vs test fixture** — the **demo fixture** (`fixtures/demo_mailbox.json`,
  new in S16) is a purpose-built, authored-to-outcome mailbox for the product
  narrative. The **test fixture** (`fixtures/mailbox.json` + `gold/`, since S0)
  is the small correctness-gate dataset. They are deliberately separate (D13):
  mixing them makes tests story-dependent and the demo test-minimal.
- **Canonical demo question** — one of a small, fixed set of coverage questions
  the demo uses. Each has known expected cited evidence and a citation-honest
  fallback answer. Only these questions are eligible for fallback.
- **Citation-honest fallback** — a stored answer for a canonical demo question,
  used only if the live synthesis call fails/lags. It still cites real fixture
  `message_id_header`s, still passes the citation allow-list and evidence drawer,
  never exposes excluded content, never answers arbitrary input, and shows a
  subtle operator/debug label when active.
- **Existence oracle** — any response that lets a viewer infer that
  sensitive/restricted content exists on a specific topic, even without showing
  it (e.g. "this topic is HR-restricted"). S14 deliberately closed this; S16's
  fail-safe behavior must not reopen it — sensitive-only and unknown queries
  return identical no-evidence responses.
