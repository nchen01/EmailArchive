# S47 - Coverage Contract Per Project

Status: spec-only (proposed). NOT implemented. Do not build until the product
lead approves this spec.

This is a docs/spec-only sprint plan. It designs a per-project "coverage
contract" over the EXISTING frozen handoff package, so a recipient (and the
creator, and a manager) can trust the boundary of what a handoff covers. It is
the next quality-first roadmap item (item 5 in
`docs/product-roadmap-quality-first.md`) after S43 (eval harness), S44 (safety
gates), and S46 (creator guided wizard). It prefers a computed, package-local,
snapshot-only design and, in its recommended MVP, needs no migration and no
`ekc_schemas` change.

## 1. Purpose and non-goals

### Purpose

Today the package is, in effect, a grouped list of claims and their cited
evidence. S39 froze a per-claim `project_label` and S46 groups the creator and
recipient views by it, but neither view states, per project, a clear contract:

- what this handoff is responsible for (the covered projects/areas),
- what it deliberately does NOT cover (the boundary),
- what is settled (decisions),
- what remains open (open loops / next actions),
- what is blocking (blockers),
- what safety/exclusion posture applies,
- and which evidence backs each of those.

S47 turns each project group into an explicit, cited coverage contract so the
boundary is legible at a glance, not reconstructed by reading every claim.

### Non-goals

- Not a new intelligence layer. The contract is assembled from the SAME frozen
  claims/evidence and the S39 `project_label`; no new extraction, retrieval, or
  LLM synthesis. (Better blocker-kind extraction stays the separate, already
  recorded candidate-work item; S47 renders whatever kinds exist.)
- No recipient live-mailbox access. The recipient stays package-local
  snapshot-only; the contract adds no field that could query live Project / Event
  / Message / retrieval tables.
- No admin surface change. Admin stays metadata-only; the contract is not an
  admin content backdoor.
- No calendar / Jira / Linear / Slack / Teams / any new connector.
- No productivity scoring, performance summary, or employee monitoring. A
  contract describes coverage of work items, never a person's output.
- Not a broad free-text narrative. Any prose is generated from cited claims (see
  the recommended defaults); the creator does not hand-write uncited summary text
  in the MVP.
- No change to the S44 detectors or the S44 publish gate semantics; no change to
  the S43 harness's gate definitions (S47 adds new checks, does not weaken old).

## 2. Current baseline (S39 / S40 / S46)

- S39 (migration 0014) froze `handoff_claim.project_label` at generation from the
  creator/coverer-owned mailbox's project table. The recipient DTO already
  surfaces `project_label` per claim (`RecipientClaim.project_label`), and the
  recipient rail groups by it, with the older `coverageAreas` text clustering as a
  pre-S39 fallback.
- S40 added the deterministic, LLM-free recipient package-local Ask
  (`services/handoff/ask.py`), shaped by intent (status / next steps / blocked /
  decisions) using the frozen `project_label` as a scoping signal, over snapshot
  rows only, with an oracle-safe neutral no-answer.
- S46 made the creator flow a wizard (Start -> Scope -> Review -> Safety ->
  Publish) that already groups the creator review by real project identity (S37)
  and enforces the S44 safety step.
- Claim kinds available on `HandoffClaim.kind`: `open_loop`, `decision`,
  `blocker`, `project_state`, `briefing`, `person_note`. The generator currently
  maps event `proposed -> open_loop` and `did`/`outcome -> decision`; `blocker`
  and richer kinds are sparsely produced today (recorded limitation). S47 must
  degrade gracefully when a kind is empty.
- Exclusions are creator-only: `HandoffPackage.exclusion_counts` (aggregate
  categories) are shown to the creator and NEVER to the recipient; the recipient
  sees only a global, package-invariant `RecipientPrivacyPosture` (a constant
  statement, no per-topic counts) precisely so no per-topic existence oracle can
  be built. S47 must preserve this asymmetry (see section 8).

## 3. Proposed per-project coverage contract shape

One contract entry per project group (keyed by the frozen `project_label`; the
`Unassigned / cross-project` and `Other evidence` buckets from S37/S46 remain as
honest fallbacks). Each entry is assembled, not authored:

- `project_label` (string): the S39 frozen label, or an honest fallback.
- `covers` (summary): a short, generated line naming the project and the count of
  covered claims by kind. Text is templated from cited claims only (e.g. "Covers
  Nexus Auth: 3 decisions, 2 open loops, 1 blocker"), never free-typed.
- `decisions` (list): claims of kind `decision`, each with its cited evidence
  refs. "What is settled."
- `open_loops` (list): claims of kind `open_loop` (plus `project_state` framed as
  status), each with evidence. "What remains open / next actions."
- `blockers` (list): claims of kind `blocker`, each with evidence. Empty when the
  generator produced none (rendered as "none flagged", not "no blockers exist").
- `people` (optional list): `person_note` claims relevant to the project, each
  with evidence. Read-only context; never a scored roster.
- `evidence_refs`: the distinct in-package evidence the entry's claims cite (the
  S37 grouping already computes this per group; the contract reuses it, including
  the "Other evidence" bucket so the visible evidence total always reconciles).
- `boundary` (coverage boundary): what the handoff covers vs. deliberately does
  NOT cover, expressed as safe metadata (see section 8 for the recipient/creator
  asymmetry). MVP derives "covers" from the in-scope project set; "does not cover"
  is an explicit creator declaration (deferred storage, section 6) or, absent
  that, a neutral "coverage is limited to the projects listed above" line.
- `safety_posture` (per-entry): a neutral, boolean-only reflection of the global
  posture (scope-limited, sensitive-excluded) - NOT per-project exclusion counts
  on the recipient side (section 8).

The whole-package contract is the ordered list of these entries (named projects
A-Z first, then fallbacks), plus the existing global `privacy_posture`.

## 4. Creator-side UX (in the S46 wizard)

The contract is reviewed inside the existing Review step, not as a new wizard
step, so it stays one flow:

- Each S37 project group in Review gains a compact "Coverage contract" header
  above its claims: the templated `covers` line and the by-kind counts
  (decisions / open loops / blockers / people), each count linking to the claims
  already listed below it. This is a render over data the creator already sees.
- Creator-visible boundary: the creator (and only the creator) sees the
  per-project exclusion posture as safe aggregate categories/counts drawn from the
  existing creator-only `exclusion_counts` (e.g. "2 items withheld by sensitivity
  policy in this scope"), so they understand what the recipient will not get.
  This is already creator-only data; S47 groups it by project where the exclusion
  provenance allows, else shows it package-level as today.
- Confirmation: a lightweight "This coverage looks right" acknowledgement per
  package (not per project) recorded in the wizard state and, at publish, written
  as a safe audit event (e.g. `coverage_contract_confirmed`). Note this is the ONE
  part of S47 that is not purely frontend/API-DTO: writing an audit row at publish
  is a small backend change on the existing publish path, not a migration (it
  reuses the existing `HandoffAuditEvent` table). The event must carry SAFE
  metadata ONLY - `project_count` and per-kind totals (decisions / open loops /
  blockers / people) - and NEVER claim text, evidence text, excluded content,
  recipient tokens/emails, source message-id headers, or live/Gmail links. It
  gives managers an auditable "the creator reviewed the boundary" signal without
  any new content exposure. If we want S47 implementation to stay STRICTLY
  frontend + API-DTO only (no backend write at all), then
  `coverage_contract_confirmed` is marked DEFERRED and the creator confirmation is
  wizard-state only until a later increment; the recommendation (section 6) allows
  the confirmation audit only when it is implemented as safe metadata with no
  migration.
- Editing wording: DEFERRED in the MVP. Because every contract line must stay
  citation-backed, free-text editing risks uncited assertions. The creator edits
  the contract by editing scope/claims (prune evidence, regenerate) as they
  already do; the contract recomputes. A future increment may allow choosing among
  generated phrasings or writing an out-of-scope declaration (section 6), each
  still cited or explicitly marked as a creator statement.
- "What it does not cover": in the MVP the creator can, per package, mark named
  projects/areas as explicitly out of scope (a pick-list from their own project
  labels, deferred to the optional store in section 6); until that ships, the
  boundary line is the neutral "limited to the projects listed" statement.

## 5. Recipient-side UX

The recipient package view (unchanged routes, snapshot-only) renders the contract
per project group, replacing the flat claim list feel with a legible boundary:

- Per project card: `covers` line at top; then labeled sections "Settled
  (decisions)", "Open / next" (open loops + status), "Blockers", and optional
  "People", each item showing its text and an inline "evidence" disclosure that
  reveals the cited snapshot messages (the existing recipient evidence rendering).
- Empty kinds render as neutral "none in this handoff" (never "none exist"), so
  the card never implies a global truth about the mailbox.
- Boundary block: a short, per-project "What this covers / What it does not"
  statement. "Covers" lists the project. "Does not" shows either the creator's
  explicit out-of-scope declaration (safe labels only) or the neutral
  scope-limited line - and NEVER per-project exclusion counts (section 8).
- Global posture: the existing `RecipientPrivacyPosture` line stays, once, at the
  package level (scope-limited, sensitive-excluded, note).
- Return handoff: for `package_type=return_delta` the per-project card is framed
  as "What changed while you were away" per carried project (using the S34
  `HandoffReturnContext.carried_area_labels`), reusing the S46 return framing. It
  is still assembled from the coverer's frozen claims; it does not read or mutate
  the original package's contract.
- Ask stays available and unchanged (S40); the contract is the browse view, Ask
  is the query view. Both are snapshot-only.

## 6. Data model options

The contract is per project, package-local, and must be effectively frozen (the
recipient must see exactly what was published).

- Option A - computed from claims/evidence only (no new storage).
  Assemble the contract at read time (creator DTO and recipient DTO) by grouping
  the already-frozen `HandoffClaim` rows by frozen `project_label`, bucketing by
  `kind`, and reusing the S37 per-group evidence. "Covers" and counts are pure
  functions of frozen rows.
  Pros: no migration, no `ekc_schemas` change, cannot drift from the claims,
  trivially consistent with S43/S44 which already operate on those rows. Because
  claims are frozen at publish, the computed contract is effectively frozen too.
  Cons: cannot represent a deliberate "does NOT cover X" declaration (nothing in
  the claims says what is absent), a creator confirmation timestamp, or a chosen
  phrasing. Those are creator INTENT, not derivable from evidence.

- Option B - frozen snapshot fields on existing rows.
  Add a frozen JSON column (e.g. `handoff_package.coverage_contract`) written at
  generate/publish. Captures intent (out-of-scope declaration, confirmation) in
  one place.
  Pros: single row, frozen at publish, no new table. Cons: a JSON blob is opaque
  to queries/eval, easy to let drift from the claims if regenerated, and mixes
  derived data (duplicated from claims) with intent.

- Option C - new package-local coverage-contract table.
  A `handoff_coverage_contract` table (package_id, project_label, in_scope bool,
  out_of_scope_note/labels, per-kind counts snapshot, confirmed_at), one row per
  project per package, written at generate/publish.
  Pros: explicit, queryable (eval/admin-metadata friendly), package-local, frozen,
  cleanly separates creator INTENT (in/out of scope, confirmation) from derived
  content (which stays computed from claims). Cons: a migration and new ORM/mapper
  surface; more code than needed for the derivable parts.

### Recommendation

Ship the MVP as Option A (computed-only) for everything derivable - covers,
decisions, open loops, blockers, people, evidence, and the neutral boundary line.
This satisfies "package-local and frozen at generation/publish" because it is a
pure function of already-frozen claims, and it keeps S47 a no-migration,
no-`ekc_schemas` change.

Scope of the recommended S47 build, stated precisely:

- The computed coverage contract MVP needs NO migration.
- It DOES add additive creator/recipient DTO fields (the `coverage_contract`
  block, section 7); existing fields are unchanged.
- The one optional exception to "frontend + API-DTO only" is the creator
  confirmation: if S47 includes `coverage_contract_confirmed`, it requires a small
  backend change to write a safe metadata-only `HandoffAuditEvent` on the existing
  publish path - still NO migration (it reuses the existing audit table). That
  audit row must contain only safe metadata (`project_count`, per-kind totals) and
  never claim text, evidence text, excluded content, recipient tokens/emails,
  source message-id headers, or live/Gmail links.
- If S47 must stay strictly frontend + API-DTO with no backend write at all, mark
  `coverage_contract_confirmed` DEFERRED and keep the confirmation in wizard state
  only. The confirmation audit is ALLOWED only if implemented as safe metadata and
  with no migration.

Add creator INTENT (explicit "does not cover" declaration + a per-package
confirmation) as a SEPARATE, deferred increment using Option C - a package-local
`handoff_coverage_contract` table (service DB only). Justify the migration only
when we actually build the out-of-scope declaration, because computed-only
provably cannot represent absence or a creator confirmation: there is no claim
that asserts "project X is not covered", and freezing a confirmation timestamp is
not derivable from evidence. Until then the boundary is the neutral scope-limited
line and confirmation is an audit event (section 4), neither of which needs a
migration. If the migration is built it stays service-DB only (no `ekc_schemas`
contract change, since the recipient/admin DTOs are assembled in the API layer),
and it is frozen at publish like every other handoff row.

## 7. API / DTO implications

- Recommended MVP (Option A): assemble the contract in the API response models
  (`services/api/schemas/handoff.py`) and the recipient/creator serializers. Add a
  `coverage_contract` block to the creator package DTO and the recipient package
  DTO as a derived, additive field:
  - `CoverageContractEntry { project_label, is_fallback, covers_summary,
    decisions[], open_loops[], blockers[], people[], evidence_refs[], boundary,
    safety_posture }`. Each list item is a
    `CoverageContractItem { claim_id, kind, text, source_message_id_headers }` and
    each `evidence_refs` entry is a `message_id_header`. Per this repo's citation
    convention (invariant 2: internal FKs are UUIDs, citations/provenance are
    `message_id_header` values), the item does NOT carry separate evidence UUIDs:
    `source_message_id_headers` and `evidence_refs` are the `message_id_header`
    citations already present in the package's `evidence[]` payload, so the client
    resolves them against that list (no new content, no duplicated bodies). As
    shipped in S48 the item also inlines the claim `text` (already in the payload
    via `claims[]`) so a contract section renders without a second lookup; this is
    the intended final MVP shape.
  - The `coverage_contract` block is IDENTICAL and recipient-safe on both the
    creator and recipient DTOs: it carries NO exclusion counts and NO out-of-scope
    content (section 8). As shipped in S48 the creator's exclusion posture is NOT
    part of the contract block; it stays in the separate, pre-existing creator-only
    `exclusion_counts` field on the package DTO.
- These are additive response fields; existing recipient/admin fields are
  unchanged. Because the recipient shape is a strict subset assembled server-side,
  this is not an `ekc_schemas` contract change (the shared schema package is not
  where these API DTOs live). Frontend adds the mirrored TypeScript interfaces.
- Deferred (Option C only): `POST` creator endpoints to set the out-of-scope
  declaration / confirm the contract, plus reading frozen contract rows. Not part
  of the MVP; specced later with its migration.
- No recipient route signature changes; the recipient still fetches its package
  via the existing session-scoped endpoint and now receives the assembled
  contract inside it.

## 8. Safety / privacy invariants

- Recipient stays snapshot-only: the contract is assembled from the same frozen
  claims/evidence already in the recipient payload; it adds no field that reads
  live Project/Event/Message/retrieval tables.
- No per-topic existence oracle (critical). The recipient contract must NOT expose
  per-project exclusion counts or categories, because "2 items withheld about
  project X" is exactly the per-topic signal S17/S40 deliberately withheld via the
  constant global `privacy_posture`. On the recipient side, "what it does not
  cover" is expressed only as (a) the neutral scope-limited statement and/or (b)
  the creator's explicit, deliberate out-of-scope declaration by safe project
  label - never a count derived from what was filtered. Per-project exclusion
  counts remain CREATOR-ONLY (they already are). This asymmetry is the key safety
  design point of S47.
- No matched sensitive text: the contract shows claim text and cited snapshot
  evidence that are already in the package; it never surfaces excluded content,
  and S44 findings render (creator-side) as safe metadata only, unchanged.
- Admin stays metadata-only: if any contract signal reaches admin it is aggregate
  counts (project count, per-kind totals), never claim/evidence bodies or the
  out-of-scope declaration text.
- Determinism: the computed contract is a pure function of frozen rows, so it is
  byte-stable for a given package - consistent with the repo determinism rule and
  testable by the S43 harness.
- Volume is not accomplishment: counts in the contract describe work items
  (decisions/open loops/blockers), never a person's productivity; people entries
  are read-only context, never scored or ranked.

## 9. Interaction with S44 findings and the S43 eval harness

- S44: the contract is a render over the same claims/evidence S44 already scans;
  it introduces no new pre-publish path. The creator-side contract sits alongside
  the required S44 safety step in the wizard Review; high findings still block
  publish. Because the recipient contract shows only in-package claims/evidence,
  it cannot surface anything S44 would flag as withheld. No change to S44
  detectors or gate.
- S43: extend the offline harness with contract-specific gates that reuse its
  existing fixture packages: every contract line is citation-backed (each
  decision/open_loop/blocker/person entry cites >=1 in-package evidence row that
  is present in the payload); the contract's evidence set equals the package's
  evidence set (nothing invented, nothing dropped); no excluded fixture material
  appears in any contract entry; and per-project counts equal the grouped claim
  counts. These are additive gates; existing S43 gates are unchanged. The harness
  stays offline, deterministic, and external-API-free.

## 10. Manual test plan

Compact demo (S17 handoff-demo seed):

1. Seed, open the creator wizard, scope to a single project, generate.
2. Confirm the Review step shows a per-project coverage-contract header with a
   templated covers line and by-kind counts that match the claims listed.
3. Publish; open the recipient view and confirm each project card shows Settled /
   Open / Blockers with inline evidence, empty kinds read "none in this handoff",
   and the boundary + global posture render once.

Rich demo (`scripts/seed_rich_handoff_demo.py`):

1. Scope across multiple projects, generate.
2. Confirm one contract entry per project (S37 grouping), named projects first,
   fallbacks last, counts reconciling with the grouped claims, and the "Other
   evidence" bucket still accounted for.
3. Prune an evidence item and regenerate; confirm the affected project's contract
   counts and covers line update deterministically.
4. Confirm the recipient card exposes NO per-project exclusion counts (only the
   neutral boundary + global posture), while the creator view may show the
   creator-only exclusion posture.

Return handoff (S34):

1. From a published forward package, create a return draft, generate.
2. Confirm each carried project renders as "What changed while you were away" and
   that the original package's recipient view/contract is unchanged after the
   return is published.

Regression: recipient routes byte-identical except the additive contract block;
admin console unchanged; frontend build clean; no migration in the MVP.

## 11. Acceptance criteria

- Every published package presents, per project group, a coverage contract with:
  a covers summary, decisions, open loops, blockers, optional people, cited
  evidence, and a boundary statement - assembled from frozen claims/evidence and
  the S39 `project_label`.
- Every contract line is citation-backed; the contract's evidence set equals the
  package evidence set; no excluded content appears (verified by new S43 gates).
- The recipient contract exposes no per-project exclusion counts and no live
  mailbox access; the global privacy posture is unchanged; the anti-oracle
  property holds.
- The creator reviews the contract inside the existing wizard Review step and a
  per-package confirmation is auditable as safe metadata.
- Return packages render per carried project as "what changed", without mutating
  the original package's contract.
- MVP introduces no migration, no `ekc_schemas` change, no dependency, and no
  admin/recipient route signature change (additive DTO fields only); Alembic head
  stays `0014_handoff_claim_project_label`. The single allowed backend change is
  the optional `coverage_contract_confirmed` audit write on the existing publish
  path (safe metadata only, reusing the existing `HandoffAuditEvent` table, still
  no migration); if S47 is to stay strictly frontend + API-DTO, that write is
  DEFERRED. Any Option C creator-intent store is a separate, later, service-DB-only
  migration with its own approval.
- Frontend build passes; new source/docs are ASCII-only.

## 12. Open questions and recommended defaults

Encode these defaults unless the product lead objects:

- Package-local + frozen: yes. The contract is package-local and frozen at
  generation/publish (computed from frozen claims in the MVP). (Default: yes.)
- No live tables for the recipient: yes. The recipient never queries live
  Project/Event/Message; the contract is snapshot-only. (Default: yes.)
- Group by S39 frozen `project_label`: yes, with the S37 fallbacks
  (unassigned/cross-project, other-evidence). (Default: yes.)
- Creator wording edits: DEFERRED. The creator shapes the contract by
  scope/claim pruning; free-text wording editing is not in the MVP because it
  risks uncited assertions. (Default: defer; revisit with a "choose among
  generated phrasings" option.)
- Out-of-scope expression: safe labels/categories only, never hidden content, and
  on the RECIPIENT side never as exclusion counts (anti-oracle). MVP boundary is
  the neutral scope-limited line; explicit "does not cover X" declaration is the
  deferred Option C increment. (Default: neutral line now, declaration later.)
- Start with decisions / open loops / blockers per project; no broad free-text
  narrative unless generated from cited claims. "Known exclusions per project" is
  a CREATOR-ONLY view (drawn from the existing creator-only `exclusion_counts`) and
  is NOT rendered to the recipient: the recipient side never shows per-project
  withheld counts or categories. The only per-project "does not cover" the
  recipient may see is an explicit creator-declared safe label (the deferred Option
  C declaration), never an inferred hidden-content count. (Default: yes.)
- Return handoff shows "what changed during coverage" per carried project and does
  not mutate the original package's contract. (Default: yes.)
- Migration posture: computed-only MVP (no migration). A migration is proposed
  only for the deferred Option C creator-intent store, kept service-DB only, and
  justified because computed-only cannot represent absence (an out-of-scope
  declaration) or a frozen creator confirmation. (Default: no migration in MVP.)

Open (for product lead):

- Should manager-facing confirmation be an audit event only (MVP), or also a
  visible badge on the recipient/admin surface later? (Recommended: audit event
  now; a metadata-only admin badge later, never on the recipient content surface.)
- Is a per-project "coverage confidence" signal wanted, or does it risk implying a
  productivity/quality score? (Recommended: omit; it is too close to scoring.
  Show only counts and cited evidence.)
- For return handoffs, is "what changed" scoped to the coverage window only?
  (Recommended: yes - reuse the S34 carried scope; do not widen.)

Stop after opening/reporting this spec PR. Do not implement S47 until the product
lead approves.

## S48 implementation status (as shipped)

S48 implements this spec as the computed-only MVP
(`services/handoff/coverage_contract.py` + additive `coverage_contract` DTO blocks
on the creator and recipient package responses + frontend rendering + three
additive S43 hard gates). No migration, no `ekc_schemas` change, no dependency, no
recipient live-table access, no admin surface change; Alembic head stays
`0014_handoff_claim_project_label`.

One spec item is DEFERRED from S48 and explicitly recorded here:

- `coverage_contract_confirmed` (the "This coverage looks right" creator
  acknowledgement) is NOT implemented in S48. S48 ships no wizard-state
  confirmation and no publish-path audit write, to keep the sprint strictly
  computed-only (frontend + API-DTO, plus the eval harness). It remains a future,
  optional, safe-metadata-only, NO-migration backend write on the existing publish
  path, reusing the existing `HandoffAuditEvent` table. When built, that event must
  carry only `project_count` and per-kind totals (decisions / open loops /
  blockers / people) and NEVER claim/evidence text, source `message_id_header`
  values, recipient tokens/emails, exclusion data, tokens, live/Gmail links, or any
  raw reason. Until then, the creator reviews the contract in the wizard Review
  step (read-only) and no confirmation is persisted.
