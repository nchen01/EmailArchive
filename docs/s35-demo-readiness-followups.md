# S35 Demo-Readiness Follow-ups

Captured from the product-lead manual retest of the rich demo mailbox
(`5faa306a-...`) on PR #55. PR #55's own scope is the rich-seed crash fix +
docs honesty (already shipped: the Cover-for-me 502/KeyError fix and the
corrected quickstart). Everything below is deliberately deferred to follow-up
PRs so #55 stays a tight, mergeable safety fix.

## Diagnosis carried into these follow-ups

The retest confirmed the Atlas 502/KeyError is gone and project-name Cover-for-me
prompts now return evidence. Two product-quality gaps remain on the Cover-for-me
surface, both rooted in the same place:

- **The L1 answer ignores the user's question.** For an L1 project/contact
  match, `services/synthesis/cover_for_me.py` calls the model with a *fixed*
  prompt — `_PROJECT_QUERY` ("Summarize what has been done on this project") at
  line 310, `_CONTACT_QUERY` at line 435 — and discards the user's actual
  `query`. Only the L2-only path (line 466) passes the real question. So
  "What is blocked for Harbor Billing Migration?" is answered as a generic
  activity summary; the user must infer which parts are blockers. The synthesis
  path exists and works (Anthropic-backed, claims rendered as a cited list) — it
  just is not intent-aware.
- **A successful L1 answer looks failed.** `retrieval_status` describes only the
  L2 operational state, but the frontend `RetrievalStatusNote`
  (`frontend/src/components/CoverForMe.tsx:94`) renders `no_embeddings` as a
  warn-colored "run embed_backfill.py" note *regardless* of whether L1 already
  produced a full cited answer. The backend comment at
  `cover_for_me.py:147-151` is explicit that the UI should use `result.state`
  for answer quality and `retrieval_status` only for the L2 state.

## Recommended sequencing

- **A. Relationship Map readability** (frontend-only). Collapse to org/domain
  groups by default with per-group expanders, cap initially rendered edges with
  "show N more", and add a degree/weight threshold control for live density.
- **B. Creator Handoff review ergonomics** (frontend-only). Sticky Publish/
  Regenerate rail (extend the S17.20 pattern), claims grouped by project into
  collapsible sections with counts, and a project/text filter + jump index for
  dense (~68-claim) packages.
- **C. Cover-for-me answer synthesis/formatting over L1 evidence** (this doc's
  focus — see below). No schema/migration.

Deferred separately because they need backend/package-snapshot changes:

- **D. Recipient coverage areas grouped by frozen package project labels.**
  Requires snapshotting project labels onto `RecipientClaimOut` at generate time
  (today it carries only a bare `project_id`). Preserves snapshot-only.
- **E. Recipient Ask next-step to-do answers.** Recipient-Ask behavior change.

## Follow-up C — Cover-for-me answer quality (first-class) — ✓ IMPLEMENTED

> Status: shipped in the `codex/s35-cover-for-me-answer-quality` PR. L1
> project/contact synthesis now passes the user's real, intent-shaped question
> (`services/synthesis/intent.py`), and the frontend no longer shows the L2
> warning banner over a successful L1 answer. No schema/migration/DTO change.


Cover-for-me must answer the question first and cite evidence second, instead of
returning evidence for the user to interpret.

Desired feel, for "What is blocked for Harbor Billing Migration?":

> These are the things currently blocked in Harbor Billing Migration:
> 1. Invoice preview mismatch needs confirmation from Billing Ops. [cite]
> 2. Failed-card retry schedule is waiting on Stripe/support alignment. [cite]
> 3. Usage export reconciliation still needs validation before migration. [cite]

### Minimal change (no schema, no migration)

1. **Pass the user's real `query` into the L1 project/contact synthesis** rather
   than the fixed `_PROJECT_QUERY`/`_CONTACT_QUERY`, and add light intent framing
   to the system prompt so the model shapes the answer to the question:
   `blocked` → blockers list, `next steps` → to-do list, `status` → concise
   status summary, `what changed` → change summary, else → concise summary. The
   existing "no citation, no claim" allow-list enforcement stays exactly as-is.
2. **Stop rendering the L2 warning over a successful L1 answer.** When
   `claims.length > 0`, suppress or soften the `no_embeddings` note (e.g.
   "Answered from structured records; semantic retrieval is not enabled") so a
   working answer never looks failed.

Both are prompt + rendering changes. `CoverForMeResponse` already carries
`result.claims`, `supporting_evidence`, and `retrieval_status`; no DTO field is
added and no contract changes.

### Acceptance (Cover-for-me answer behavior)

1. Project/contact questions with structured L1 evidence return a concise
   synthesized answer first.
2. Citations/evidence show underneath or inline.
3. Intent-shaped formats: blocked → blockers list; next steps → to-do list;
   status → status summary; what changed → change summary; what needs attention
   → attention list.
4. The user never has to infer the answer from raw evidence cards.
5. Grounding preserved: no claim without citation.
6. No synthesis provider → graceful, clear copy, never a 502/KeyError.
7. Embeddings missing but L1 evidence present → the UI must not look failed:
   remove/soften the yellow warning, or clarify that semantic retrieval is
   unavailable while structured evidence was still used.
8. No sensitive/noise content leaks.
9. No live mailbox/source/Gmail links appear where not already allowed.
