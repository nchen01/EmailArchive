# S45 - Creator Guided Handoff Wizard

Status: spec-only (proposed). NOT implemented. Do not build until the product
lead approves this spec.

This is a docs/spec-only sprint plan. It proposes a frontend-led guided workflow
over the existing creator handoff endpoints. It changes no backend behavior, no
schema, and no `ekc_schemas` contract. It is the next quality-first sprint after
S43 (eval harness) and S44 (privacy/safety gates), and implements roadmap item 4
in `docs/product-roadmap-quality-first.md`.

## Purpose

Make the creator-side handoff flow easier and much harder to misuse. Today a
creator drives create -> scope -> generate -> review/prune -> publish through one
long review surface (`frontend/src/components/HandoffReview.tsx`, ~1450 lines).
It works and every safety invariant holds, but the flow is a single dense page:
scoping, an empty-generation diagnostic, evidence pruning, the S44 safety panel,
and the publish action all coexist, so a first-time creator has to know the right
order and can miss the safety review or publish before pruning.

S45 reframes the same steps as a guided, one-thing-at-a-time wizard so the correct
order is the default path and the safety gate cannot be skipped, while preserving
the current detailed surface as an explicit "Advanced review" mode after
generation. It is quality/usability work, not new capability: no new mailbox
access, no new recipient/admin surface, no new integrations.

## Non-goals

- No recipient-side change. The recipient package view and every
  `/api/handoff/recipient/*` route stay snapshot-only and byte-for-byte unchanged.
- No admin-side change. `/api/admin/*` stays metadata-only and unchanged.
- No calendar / Jira / Linear / Slack / Teams / any new connector.
- No new retrieval, no new claim/evidence generation logic, no change to the S44
  detectors or the publish gate semantics.
- No migration and no `ekc_schemas` change. The wizard is expected to be built
  entirely on the existing endpoints (see the reuse table).
- Not a redesign of return handoff (S34). S45 only ensures a return draft can
  enter the same review/publish steps cleanly.
- No mandatory backend endpoint. One optional, deferred backend addition is
  described and explicitly NOT part of the S45 build.

## Boundary (unchanged invariants)

These invariants are inherited and must remain provably true after S45. The
wizard is a client reorganization of existing calls; it introduces no new data
path that could weaken them.

- Creator reasons only over their OWN authenticated mailbox. The wizard reads the
  same creator-scoped package DTO (`HandoffPackageOut`) that `HandoffReview`
  reads today; it gains no new read scope.
- Recipient access stays package-local snapshot-only. Nothing in the wizard
  touches recipient routes or the frozen snapshot.
- Admin/governance stays metadata-only. Untouched.
- No-citation-no-claim: publishing an empty package is already rejected
  server-side (`/handoff/{id}/publish` returns 409 when evidence count is 0). The
  wizard surfaces this earlier as guidance but relies on the server rejection.
- S44 safety gate: high-severity findings block publish server-side. The wizard
  must never let the creator reach a publish call that would 422 for an
  unresolved/unacknowledged high finding without first showing the finding and
  the resolve-or-acknowledge choice. The server gate remains the source of truth.
- No raw safety-override reason is stored or logged; the audit keeps only
  `reason_provided` + `reason_length` (S44). The wizard sends the reason to the
  existing publish endpoint exactly as `HandoffReview` does today and adds no new
  place that persists it.
- ASCII-only in new source/docs; `->` and hyphens, no box-drawing / em dashes.

## Current pain points (from rich-demo testing)

Observed while driving `scripts/seed_rich_handoff_demo.py` and the S17 runbook
through `HandoffReview.tsx`:

1. One-page density. Create, scope, empty-generation diagnostic, grouped evidence
   with per-item remove, the S44 `SafetyReviewPanel`, and the publish form are all
   present on the same surface. The right order is implicit.
2. Order is discoverable only by trying. A creator can click Generate before
   setting scope, or open the publish form before reading the safety panel. The
   server enforces legality (publish only from `generated`, gate blocks high
   findings), so nothing unsafe ships, but the creator hits avoidable errors.
3. Scope feels blind. `PATCH /handoff/{id}/scope` accepts project/person/thread
   include and exclude lists, but the creator picks scope with no "how much does
   this select" signal until after Generate, so broad scopes are found late.
4. Pruning and safety are visually separate but logically linked. Removing an
   evidence item (`remove` -> re-generate) can clear a safety finding, but the
   two panels do not tell that story, so a creator may acknowledge-override a
   finding that a single prune would have removed.
5. Publish confirmation is thin. The one-time capability code is shown once, but
   the final step does not consolidate recipient, expiry, claim/evidence counts,
   removed-evidence count, and safety status into one deliberate "you are about to
   freeze and share" summary.
6. Return handoff is a separate entry (`ReturnHandoff.tsx`) that does not visually
   share the review/publish steps, so its post-generation review feels like a
   different product than a forward handoff.

## Proposed wizard

A linear, resumable, five-step wizard is the primary creator path. Each step does
one thing, shows only what that step needs, and gates forward progress on the
server-legal precondition for the next step. The wizard is a new presentational
shell (proposed `frontend/src/components/handoff/HandoffWizard.tsx` plus small
step components); the existing `HandoffReview` becomes the Advanced mode body,
reused, not rewritten.

Steps:

1. Start. Choose forward handoff (from the creator's mailbox) or return handoff
   (S34, from the coverer's mailbox back to the original employee). Forward calls
   `POST /handoff/{mailbox_id}` to create a draft; return calls
   `POST /handoff/{original_package_id}/return-draft`. Both yield a `draft`
   package the rest of the wizard drives identically.
2. Scope. Set include/exclude project/person/thread lists via
   `PATCH /handoff/{id}/scope`, grouped and labeled using the S37 project grouping
   (`frontend/src/utils/handoffGroups.ts`). Show client-side guidance about
   breadth from data already on the DTO / already-loaded project list (see "Scope
   preview" below) without any new endpoint. Empty scope and very broad scope get
   distinct inline states (see UX states).
3. Generate and review. `POST /handoff/{id}/generate`. Show claims grouped by
   real project identity (S37), each with its collapsed evidence and a per-item
   Remove (prune = add to `excluded_message_id_headers` then re-generate, exactly
   as `HandoffReview.removeEvidence` does today) and a Restore-all
   (`excluded_message_id_headers: []` then re-generate). The empty-generation
   diagnostic (`generationEmptyMessage`) renders here when generation returns no
   claims. A visible "Advanced / full evidence review" toggle opens the existing
   detailed surface for power users.
4. Safety review. Render the S44 findings already present on the DTO
   (`pkg.findings`, via the existing `SafetyReviewPanel`) as a required step. If
   any high-severity finding is present, the step is not "complete": the creator
   must either go back and prune/regenerate until the finding clears, or explicitly
   acknowledge each high finding with the required reason. The wizard collects the
   S44 `safety_ack` here so the publish step is a single confirm. When there are no
   high findings, this step is an informational pass-through the creator still sees
   once.
5. Publish. A consolidated confirmation: recipient, expiry, claim count, evidence
   count, removed-evidence count, safety status (clean / N high findings
   acknowledged), and a clear note that the one-time link is shown exactly once.
   Calls `POST /handoff/{id}/publish` with `recipient_email`, `expires_in_days`,
   and `safety_ack` only when high findings were acknowledged. On success, show the
   one-time capability code / share fragment (reusing the existing transient-result
   handling that survives the same-id published re-render) and the post-publish
   options (new-version, revoke, export.html) as they exist today.

Forward-progress gating (client-side, mirroring server legality):

- Scope step requires a draft package (from step 1).
- Generate step requires a saved scope; Review requires a `generated` status.
- Safety step is always shown after a non-empty generation and blocks Publish
  while any high finding is unresolved and unacknowledged.
- Publish is only reachable from `generated` with at least one evidence row and a
  satisfied safety step, matching the server's 409/422 conditions so the creator
  does not hit an error the wizard could have prevented.

The wizard is resumable: it derives its current step from the package status and
contents (`draft` -> scope; `generated` with claims -> review/safety;
`published`/`revoked` -> read-only summary), so a refresh (the S17 refresh-safe
workspace mailbox behavior) lands the creator on the correct step.

## UX states

- Empty scope: scope step with nothing included. Inline guidance that a package
  needs at least one project/person/thread; Generate disabled with a reason.
- Broad scope: scope selection that client-side guidance flags as large (e.g. many
  projects or a high included-thread estimate from already-loaded data). Non-
  blocking caution ("this looks broad; you will review and prune everything it
  selects") plus a jump to narrow it. No new endpoint; purely advisory.
- Generated package: claims grouped by S37 project identity, per-group filter,
  collapsed evidence, per-item Remove, Restore-all, and the Advanced-review toggle.
- Empty generation: the S17.13 creator-only diagnostic
  (`no_events_extracted` / `no_events_in_window` / `all_events_excluded_by_policy`
  / generic) with a "widen scope" path back to step 2.
- High safety findings: safety step shows each finding
  (category/severity/explanation/ref only, never matched text) with two exits:
  prune-and-regenerate (preferred; may clear the finding) or acknowledge-with-
  reason. Publish stays blocked until every high finding is resolved or acked.
  The step explicitly suggests trying a prune before acknowledging.
- Frozen package (published or revoked): read-only summary. No scope/generate/
  prune/publish controls (they are already rejected server-side for non-mutable
  status). Shows recipient, expiry, counts, safety status, and the post-publish
  actions (new-version, revoke, export.html) that remain available.
- Return handoff: step 1 return branch creates a `return_delta` draft; steps 2-5
  are identical, with return-mode framing (what changed while you were away) reused
  from `ReturnHandoff.tsx` / the existing return copy. Recipient defaults to the
  original creator (server already applies this at publish when the field is blank).
- Publish success: the one-time capability code / share fragment shown once, with a
  clear "this link will not be shown again" note and copy control, plus the
  post-publish action set.

## Data / API reuse

Every step maps to an endpoint that already exists. No new required endpoint.

- Create forward draft: `POST /api/handoff/{mailbox_id}` -> `HandoffPackageOut`.
- Create return draft (S34): `POST /api/handoff/{original_package_id}/return-draft`
  -> `HandoffPackageOut`.
- Return context (S34): `GET /api/handoff/{package_id}/return-context`.
- Update scope: `PATCH /api/handoff/{package_id}/scope` (include/exclude
  project/person/thread lists, excluded message-id headers) -> `HandoffPackageOut`.
- Generate: `POST /api/handoff/{package_id}/generate` -> `HandoffPackageOut`.
- Read package: `GET /api/handoff/{package_id}` -> `HandoffPackageOut`.
- Prune evidence: reuse the scope patch (`excluded_message_id_headers`) then
  generate, exactly as `HandoffReview.removeEvidence` / `restoreAllEvidence`.
- Safety findings: read `HandoffPackageOut.findings` (S44 `SafetyFindingOut`
  already on the creator DTO); render via the existing `SafetyReviewPanel`.
- Publish: `POST /api/handoff/{package_id}/publish` with `recipient_email`,
  `expires_in_days`, and optional `safety_ack` (reason + acknowledged finding ids)
  -> `PublishResponse` (one-time code).
- Post-publish: `POST /handoff/{id}/new-version`, `POST /handoff/{id}/revoke`,
  `GET /handoff/{id}/export.html` -- unchanged, surfaced from the frozen summary.

Client utilities reused: `frontend/src/utils/handoffGroups.ts` (S37 project
grouping), the existing API client wrappers in `frontend/src/api/` (`createHandoff`,
`updateHandoffScope`, `generateHandoff`, `publishHandoff`, etc.), and the
`SafetyFinding` / `SafetyReviewPanel` types and component from S44.

## Proposed backend additions (optional, deferred -- NOT part of S45)

None are required for S45, and none are to be built in this sprint. Recorded only
so a future sprint can decide deliberately:

- Optional, deferred: a safe creator-side scope preview/count endpoint, e.g.
  `GET /api/handoff/{package_id}/scope-preview` returning aggregate counts
  (estimated threads/messages/claims in the current scope) as safe metadata only,
  no bodies. This would replace the S45 client-side breadth guidance with a real
  count for the "broad scope" state. It is explicitly out of scope for S45: the
  S45 wizard ships with client-side guidance from already-loaded data. If a future
  sprint builds it, it must be creator-owned-package scoped, metadata-only (counts,
  never content), and add no recipient/admin exposure.

Any other backend addition proposed during S45 implementation must be justified
narrowly against the "frontend-led over existing endpoints" requirement and,
absent that justification, rejected.

## Privacy / security invariants (must hold after S45)

- Recipient snapshot-only unchanged: no wizard code path calls a recipient route
  or reads the frozen snapshot; the recipient view is untouched.
- Admin metadata-only unchanged: no wizard code path touches `/api/admin/*`.
- Creator-scope only: the wizard reads only the creator-owned package DTO it
  already has access to; it opens no new mailbox read scope and no cross-mailbox
  filter (return handoff still uses provenance ids only, never as live filters,
  per S34).
- Safety gate cannot be skipped: the wizard's step gating plus the unchanged
  server publish gate together guarantee a high finding is always seen and either
  resolved or acknowledged-with-reason before publish. The server remains the
  enforcement point; the wizard only prevents avoidable errors.
- No raw-reason leak: the override reason flows only through the existing publish
  endpoint; the wizard persists/logs nothing itself.
- No new content surface: findings render as category/severity/explanation/ref
  only (never matched text), matching S44; evidence shown is the same creator-DTO
  evidence `HandoffReview` already shows.
- ASCII-only in all new source and docs.

## Acceptance criteria

- A first-time creator can complete forward create -> scope -> generate/review/
  prune -> safety -> publish through the wizard without needing to know the order,
  and without hitting a preventable server error (no premature generate/publish).
- The existing detailed review remains reachable as an "Advanced review" mode from
  the review step and is functionally unchanged.
- The wizard is the primary/default creator entry for creating and reviewing
  packages; the advanced surface is a post-generation mode, not the default.
- A high-severity S44 finding cannot be bypassed: the wizard blocks Publish until
  the creator prunes/regenerates it away or acknowledges every high finding with a
  reason, and the publish call is identical to today's (server gate still enforces).
- The publish confirmation step shows recipient, expiry, claim count, evidence
  count, removed-evidence count, safety status, and the one-time-link-shown-once
  note before the creator confirms.
- Return handoff (S34) enters the same scope/review/safety/publish steps and
  publishes with the original creator as default recipient, with no return-specific
  regression.
- Recipient routes/DTOs, admin routes/DTOs, `ekc_schemas`, and the DB schema are
  byte-for-byte unchanged; Alembic head stays `0014_handoff_claim_project_label`.
- `npm.cmd --prefix frontend run build` is clean; no non-ASCII in new files.

## Manual test plan

Compact demo (S17 handoff-demo seed):

1. Seed via the S17 handoff-demo script; open the creator wizard on the
   handoff-demo mailbox.
2. Start forward -> set a single-project scope -> generate -> confirm claims appear
   grouped by project.
3. Remove one evidence item -> confirm it disappears and the removed-evidence count
   increments; Restore-all -> confirm it returns (minus policy exclusions).
4. Advance to safety (expect clean) -> publish -> confirm the summary counts and
   the one-time code shown once.

Rich demo (`scripts/seed_rich_handoff_demo.py`) -- project filtering + high-risk
safety:

1. Open the wizard on the rich mailbox; set a broad scope spanning multiple
   projects -> confirm the broad-scope caution and S37 grouping/filtering in
   review.
2. Generate a package whose snapshot includes a high-severity S44 case (e.g. a
   pasted credential/secret-worded claim) -> confirm the safety step shows the
   high finding and blocks Publish.
3. Resolve by pruning the flagged evidence and regenerating -> confirm the finding
   clears and Publish unblocks; then separately verify the acknowledge-with-reason
   path also unblocks and that publish still succeeds (reason not surfaced back).
4. Publish -> confirm safety status reads "N high findings acknowledged" or "clean"
   accordingly.

Return handoff (S34):

1. From a published forward package, start a return draft -> confirm it enters the
   same scope/review/safety/publish steps with return-mode framing.
2. Publish with a blank recipient -> confirm it defaults to the original creator
   and the original package is unaffected.

Regression:

- Recipient package view unchanged (open an existing recipient link; byte-identical
  behavior).
- Admin console unchanged.
- Frontend build clean; `git diff --check` clean; no migration.

## Open questions and recommended defaults

Encode these defaults unless the product lead objects:

- Primary flow: wizard-first for creating and reviewing packages. (Default: yes.)
- Advanced mode: keep the current detailed review as an "Advanced review" /
  "Full evidence review" mode after generation, not removed. (Default: yes.)
- Scope preview: ship S45 with client-side breadth guidance from already-loaded
  data; do NOT build the backend scope-preview endpoint in S45; record it as
  optional/deferred. (Default: client-side only.)
- Safety gate: high-severity findings must be resolved or acknowledged before final
  publish; the wizard cannot let the creator accidentally skip the safety step.
  (Default: yes, hard.)
- Publish summary: the final step must show recipient, expiry, claim/evidence
  counts, removed-evidence count, safety status, and the one-time-link-shown-once
  note. (Default: yes.)
- Prune-before-acknowledge: the high-finding step recommends trying a prune before
  acknowledging. (Default: recommend, not force.)

Open (for product lead):

- Should the wizard fully replace `HandoffReview` as the route target, or mount
  alongside it behind a toggle for one release before it becomes default?
  (Recommended: mount alongside, wizard default, one release, then retire the
  standalone entry if telemetry/demo feedback is positive.)
- Do we want a persisted "wizard step" hint, or derive step purely from package
  status/contents? (Recommended: derive, no persistence, no schema.)
- Is the client-side broad-scope heuristic sufficient for the compact and rich
  demos, or is the deferred count endpoint needed sooner? (Recommended: client-side
  for S45; revisit only if a demo shows it is misleading.)

Stop after this spec PR. Do not implement S45 until the product lead approves.
