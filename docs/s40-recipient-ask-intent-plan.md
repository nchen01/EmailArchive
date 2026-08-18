# S40 - Recipient Package-local Ask Answer Quality (intent shaping)

Status: implemented.

## Problem

Recipient "Ask about this handoff" was a pure term-overlap retrieval, so
"What is the status of Nexus Auth Platform?" and "What are the next steps for
Nexus Auth Platform?" returned essentially the same ranked answer. After S39
grouped the package by frozen project labels, Ask should answer by intent instead
of returning one generic overlap answer.

## Constraints (unchanged from S17.9 / S39)

- Deterministic and package-local. No LLM, no live mailbox, no Project / Event /
  Message / L0 / L1 / L2 lookup from recipient routes.
- No Gmail/source link, source header, mailbox id, token, vault ref, or excluded
  content.
- Oracle safety: a no-match / sensitive / unknown query returns the SAME neutral
  no-answer, so Ask can never reveal whether excluded content exists.

## Design

`services/handoff/ask.py` gains deterministic intent shaping over the package's
own snapshot rows (the route still loads only `handoff_claim` + `handoff_evidence`).

Intent classification (`detect_ask_intent`, specific-first):

- "next steps" / "to-do" / "action items" / "follow-ups" / "remaining" ->
  `next_steps` -> keep only open-loop claims.
- "blocked" / "stuck" / "waiting" / "dependency" / "risk" -> `blocked` -> keep
  blocker-kind claims and open loops whose text reads as a blocker; else say no
  blockers were found in the package.
- "decisions" / "outcomes" / "agreed" -> `decisions` -> keep decision claims.
- "status" / "state" / "progress" / "where things stand" -> `status` -> the
  overall state (decisions + open loops together).
- otherwise -> `general` -> the prior term-overlap answer.

Pipeline in `answer_from_package`:

1. Treat each claim's frozen `project_label` as package-local searchable text: a
   claim matches when the query overlaps its text OR its project label, so a claim
   whose text does not repeat the project name ("Rotate remaining service keys"
   under "Nexus Auth Platform") still answers a query that names the project.
2. Detect a named project across ALL visible claims (best frozen-label term
   overlap). Labels exist only on surviving, non-excluded claims, so this is
   snapshot-safe and can never surface an excluded project's label.
3. If a project label matches, scope the candidate claims to that project BEFORE
   intent shaping (regardless of whether each claim's own text repeats the label);
   otherwise the candidates are the term-matched claims.
4. Oracle safety: if the query names no visible project AND matches no claim or
   evidence, return the neutral no-answer BEFORE any shaping (intent-independent).
5. Filter the scoped candidates to the intent's shape, then return the shaped
   claims plus THEIR in-package citations (capped), so every returned evidence row
   sits under a claim it supports - never an orphan wall.

Answer shapes and honesty:

- Every returned claim cites in-package evidence; a claim whose citations are all
  capped out is dropped (no dangling citation).
- "status" and "next steps" produce different claim sets from the same package.
- If a next-steps / blocked / decisions query matches the topic but there are no
  claims of that shape (e.g. only completed work exists), the answer is a truthful
  "no explicit next steps were found ..." - it does NOT restate completed work as
  action items.
- The answered message is intent-shaped (and names the scoped project when one was
  matched); the not-answered message stays the single constant neutral string.

No schema change: `RecipientAskResponse` already carries `message` and per-claim
`source_message_id_headers`; `AskResult` gained internal `intent` + `message`
fields only.

## Frontend

`RecipientPackage.tsx` `AskBox` renders answers through a new `AnswerView`: the
intent message, then one item per claim with its supporting evidence COLLAPSED
under it (a "Show N supporting messages" toggle per claim) instead of a wall. A
"none found" answer shows just the message; a legacy evidence-only answer collapses
its messages under one toggle. Citations remain package evidence cards only.

## Invariants

- Snapshot-only: the ask route reads only `handoff_*` rows; no new live query.
- Oracle safety preserved: the no-answer path is unchanged and constant.
- No Gmail/source link, source header, mailbox id, token, vault ref, or excluded
  content is introduced.
- Admin/Audit is untouched.

## Tests

`tests/test_s40_recipient_ask_intent.py` (DB-free, pure): intent classification;
status vs next-steps differ from the same package; decisions/blocked filter to
kind; a named project scopes out another project's claims via the frozen label;
next-steps with only completed work says "none" and does not restate it; oracle
safety unchanged for unknown/sensitive/empty queries; every returned claim cites
returned evidence. The existing S17 ask regression
(`test_ask_claims_never_cite_evidence_outside_returned_set`) was updated for the
S40 claim-tied evidence selection while keeping its no-dangling-citation guarantee.
S17 / S34 / S29 / S30 suites still pass. Frontend build green.

## Return handoff demo path (Part H clarification, docs-only)

Manual testing created a return handoff from the RICH seed mailbox, which produced
one arbitrary same-day event. That is mechanically valid but is not the canonical
return demo. The canonical return demo runs from `scripts/seed_handoff_demo`, which
already prints the exact steps:

- The seed provisions TWO mailboxes and prints both ids: the original
  `handoff-demo@example.com` (Dana, covered) and the coverer
  `coverer-demo@example.com` (Alex, return).
- Create and publish the ORIGINAL package from Dana's mailbox to
  `coverer-demo@example.com`; note the original package id.
- Create the RETURN handoff from Alex's (coverer) mailbox id via "Create a return
  handoff", pasting the original package id.
- Expected return output is about 4 delta claims (key rotation, SOC2 close, wiki
  migration to Nexus Auth, Northwind SSO), with 1 sensitive and 1 noise excluded.
- Loading the same/rich source mailbox in dev mode may generate a valid package but
  is NOT the intended return demo. Its date window (original `published_at` through
  today) is expected behavior; the small/odd output is because the rich mailbox is
  not the compact seed's coverer mailbox.

No code change is needed for Part H; the seed script's printed guidance already
matches this.
