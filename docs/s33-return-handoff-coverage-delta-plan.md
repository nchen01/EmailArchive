# S33 - Return Handoff / Coverage Delta Plan

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes are made here. This plan designs how a coverage recipient hands work
> back to the original employee after the coverage period ends.

**Status:** Drafted as a docs/spec-only plan; **not implemented in code**.

**Decision source:** D14 (employee-initiated audited handoff package) and D15
(return handoff is a reciprocal package linked to the original coverage package).

---

## 1. Purpose

The current Handoff Package flow solves the departure side:

> "I am stepping away. Here is the scoped package for what you need to cover."

S33 designs the return side:

> "I covered your work while you were away. Here is what changed, scoped to the
> same projects/topics I was asked to cover."

The goal is ease of use. The coverer should not manually rebuild the project
scope from scratch, and the returning employee should not get broad access to
the coverer's mailbox. The original coverage package becomes the **scope seed**
for a new **return handoff** generated from the coverer's own mailbox.

## 2. Canonical Scenario

1. Dana creates an outbound coverage package for Alex before vacation.
2. The package includes Nexus Auth, Security Audit, and an incident follow-up.
3. Alex covers those areas for three weeks.
4. Dana returns.
5. Alex clicks **Create return handoff** from the original package context.
6. The system creates a return draft from Alex's mailbox.
7. The default return scope is pre-filled from the original package:
   - same projects / coverage areas;
   - coverage period defaults to original publish date through today, adjustable;
   - recipient defaults to Dana;
   - sensitive/noise/excluded content remains excluded.
8. Alex reviews and prunes the candidate.
9. Alex publishes the return package to Dana.
10. Dana receives a compact "what changed while you were away" package, with
    cited evidence and package-local Ask.

## 3. Product Terms

**Coverage package:** the current S17 package created by the covered employee
before stepping away. Source mailbox: covered employee.

**Return handoff:** a new package created by the coverage employee when handing
work back. Source mailbox: coverage employee. Recipient: original covered
employee.

**Coverage delta:** the content focus of a return handoff: decisions made, open
loops closed or created, new blockers, changed project state, and new people
involved during the coverage period.

**Original package:** the coverage package that seeded the return handoff.

**Return scope seed:** a safe, package-local summary of what should carry from
the original package into the return draft. It is not mailbox access.

## 4. Core Decision

The return handoff is **not** a revised version of the original package.

Use `new-version` only when the same creator is revising the same outbound
coverage package. Use a **return handoff** when the recipient of that outbound
package becomes the creator of a new package back to the original employee.

Reason:

- source mailbox changes from Dana's mailbox to Alex's mailbox;
- creator changes from Dana to Alex;
- recipient changes from Alex to Dana;
- the artifact answers a different question: "what changed while you were away";
- mailbox and audit boundaries are clearer if this is a linked reciprocal
  package, not a version in the same lineage.

## 5. Non-Goals

Do not build:

- a way for Dana to pull directly from Alex's mailbox;
- a manager/admin-generated return package;
- a live cross-mailbox comparison surface;
- recipient access to Alex's live mailbox, L0/L1/L2 rows, jobs, OAuth, Gmail, or
  source-message routes;
- productivity/performance scoring of Alex's coverage;
- automatic publishing without Alex's review;
- broad "everything Alex touched while Dana was out" handoff.

The return flow stays employee-initiated and package-scoped.

## 6. User Roles

**Original covered employee:** the person returning from leave or coverage.
They receive the return handoff.

**Coverage employee / coverer:** the person who received the original package
and did the work. They create, review, prune, and publish the return handoff.

**Manager/admin/security reviewer:** governance only. They may later see safe
metadata in Admin/Audit surfaces, but they do not read return package content
unless separately authorized by a future product/legal decision.

## 7. Authorization and Consent

Return handoff creation requires both:

1. **Owner(source mailbox):** the coverer must be authenticated as the owner of
   the mailbox used to generate the return handoff.
2. **Original recipient proof:** the coverer must be the original package's
   recipient, or an authenticated tenant user whose verified email matches the
   original `handoff_recipient.recipient_email`.

In dev mode, this may be relaxed to preserve localhost testing, but production
must fail closed. A user who merely knows the original package id cannot create
a return handoff.

The returning employee does not access the coverer's mailbox. They only receive
the published return package snapshot.

## 8. Scope Carry-Forward

The product requirement is that return handoff setup should automatically pick
up the projects/topics from the initial coverage.

Use a tiered carry-forward model.

### 8.1 Structured carry-forward

If the original package has structured scope, carry it first:

- `handoff_scope.included_project_ids`;
- `handoff_scope.included_person_ids`;
- `handoff_scope.allowed_domains`;
- `handoff_claim.project_id` values from the original package.

These become the return draft's default scope filters against the coverer's
mailbox.

### 8.2 Snapshot-derived carry-forward

If the original package lacks true project ids, use package-local snapshot hints:

- coverage area labels from the recipient package UI;
- salient terms from original safe claim text;
- safe domains / sender domains from original evidence;
- original claim kinds and source topics.

These hints must be labeled as **scope hints**, not true Project rows. They are
used to propose a focused return scope, and Alex still reviews before publishing.

Implementation note: current `HandoffScope.keyword_filters` is stored but not
fully used by generation. If S34 relies on keyword hints, S34 must implement
keyword matching explicitly for return packages or avoid depending on it.

### 8.3 Manual narrowing

The return draft should default to all original coverage areas, but the coverer
may remove areas before generation or during review. Adding a brand-new unrelated
area should require an explicit "add related area" action and should be audited,
so the return package does not quietly expand beyond the original coverage scope.

## 9. Date Window

Default return window:

- `date_from`: original package `published_at` date;
- `date_to`: today, or the user-selected coverage end date if provided.

The coverer can adjust this before generating. The UI should make the default
obvious:

> "Coverage period: from when Dana shared the package through today."

Do not overload `expires_at` as the coverage period. Expiration controls access;
coverage period controls what evidence is searched.

If product wants a formal planned coverage period later, add explicit
`coverage_starts_on` / `coverage_ends_on` fields rather than reusing expiry.

## 10. Data Model Proposal

Keep return handoff objects in the service DB, not `ekc_schemas`; this is a
product/API surface, not an L0/L1 contract. `SCHEMA_VERSION` should not change.

Recommended additive migration for S34:

### 10.1 `handoff_package.package_type`

Add a constrained text column:

- `coverage` (default for existing packages);
- `return_delta`.

Existing rows backfill to `coverage`.

### 10.2 `handoff_return_context`

New table, one row per return package:

| Field | Notes |
|---|---|
| `package_id` | PK/FK to the return `handoff_package` |
| `original_package_id` | FK to the original coverage package |
| `original_lineage_id` | copied for audit/search |
| `original_creator_email` | returning employee |
| `original_recipient_email` | coverer who is now creating the return |
| `return_date_from` / `return_date_to` | defaulted coverage window, adjustable |
| `carried_project_ids` | structured project ids from original scope/claims |
| `carried_person_ids` | structured people ids when safe/useful |
| `carried_domains` | safe domains copied from scope/evidence |
| `carried_area_labels` | snapshot-derived topic labels, if no project ids |
| `seed_method` | `structured`, `snapshot_hints`, or `mixed` |
| `created_at` | audit timestamp |

This table records how the return draft was seeded. It should not contain
message bodies, evidence bodies, raw source headers, tokens, session hashes, or
OAuth/provider data.

### 10.3 Existing package fields

For a return handoff:

- `handoff_package.mailbox_id` = coverer's mailbox;
- `creator_email` = coverer;
- `reason` = likely `delegation` or a new enum value if added;
- `lineage_id` = new lineage for the return package;
- `supersedes_package_id` remains for versions of the return package only;
- `HandoffRecipient.recipient_email` defaults to the original package creator.

Open question: whether to add a `reason` enum value like `return` or
`coverage_return`. See section 20.

## 11. Generation Semantics

Return generation should reuse the same safety discipline as coverage package
generation:

- derive claims from already-extracted, already-cited Events;
- no citation, no claim;
- whole-thread sensitivity gate;
- noise exclusion;
- creator/manual exclusions before evidence snapshot;
- package-local evidence only;
- deterministic/idempotent regeneration.

The difference is content framing.

Return package claims should answer:

- What decisions were made while the original employee was away?
- Which prior open loops were closed?
- Which new open loops remain?
- What blockers or risks appeared?
- What project state changed?
- Who is now involved and why, based only on cited evidence?

Use the existing claim kinds where possible:

- `decision`: decisions/outcomes during coverage;
- `open_loop`: still-open next actions;
- `blocker`: blockers/risks if the extraction pipeline supports them;
- `project_state`: changed state or important context;
- `person_note`: new relevant person/domain context.

Do not add new claim kinds unless a later implementation review proves the
existing enum cannot express the return brief.

## 12. Matching Original Projects to the Coverer's Mailbox

The hard part is that project ids are mailbox-local. Dana's `project_id` is not
Alex's `project_id`.

S34 should not assume a project UUID can be copied across mailboxes and used as
the same project. Instead:

1. Carry original project ids as provenance.
2. Convert each original project/coverage area into a **return scope descriptor**:
   title/label, domains, salient terms, people hints, time window.
3. Match Alex's mailbox content using those descriptors.
4. If Alex's mailbox has materialized projects, map descriptors to Alex-side
   projects by thread/project assignment and label/token similarity.
5. If Alex's mailbox has no materialized projects, generate from Events whose
   cited messages match the descriptor terms/domains/people within the return
   date window.

The UI should say "Coverage areas" unless a true Alex-side Project row is
resolved. Do not fabricate shared cross-mailbox project identity.

## 13. UX Flow

### 13.1 Entry points

Preferred production entry points:

- From the recipient package view: **Create return handoff**.
- From the creator workspace Handoff tab: **Create return handoff from package**.

Because today's recipient view is capability-session based and not a full tenant
login, the implementation may require the coverer to sign in/connect their own
mailbox before creating the return package.

### 13.2 Return setup screen

Show:

- original package title and creator;
- original coverage areas/projects selected by default;
- coverage period default;
- coverer's source mailbox;
- returning employee recipient;
- privacy posture.

Actions:

- adjust return date range;
- deselect original areas;
- connect/load source mailbox if missing;
- generate return draft.

### 13.3 Creator review

Reuse the existing creator review surface, but change copy:

- "Review what changed while Dana was away";
- "Remove anything that should not travel back";
- "Claims are generated only from your mailbox and only inside the carried
  coverage scope."

Manual evidence removals remain sticky until restored, matching the existing
creator review behavior.

### 13.4 Recipient view

Reuse the S17 recipient package UI, with return-specific framing:

- header: "Return handoff";
- subheading: "What changed while you were away";
- coverage-area rail;
- related people/domains;
- package-local Ask;
- evidence attached inline to claims.

No live mailbox links.

## 14. API Proposal

Creator-side routes, all owner/tenant guarded:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/handoff/{original_package_id}/return-draft` | Create a return draft from a published original package and a coverer mailbox |
| `GET` | `/api/handoff/{package_id}/return-context` | Creator view of how the return draft was seeded |
| `PATCH` | `/api/handoff/{package_id}/return-scope` | Update carried areas/date window before generation, draft/generated only |
| `POST` | `/api/handoff/{package_id}/generate` | Reuse existing generate route, dispatching by `package_type` |
| `POST` | `/api/handoff/{package_id}/publish` | Reuse existing publish route; recipient defaults to original creator |

Keep recipient routes unchanged. A recipient session reads only the published
return package snapshot.

## 15. Audit Events

Add safe-metadata events:

- `return_handoff_created`;
- `return_scope_seeded`;
- `return_scope_changed`;
- `return_candidate_generated`;
- `return_package_published`;
- `return_package_revoked`;
- `return_package_viewed`;
- `return_package_asked`.

Safe metadata may include:

- original package id;
- return package id;
- original lineage id;
- package type;
- seed method;
- counts of carried projects/areas/people/domains;
- date window presence;
- claim/evidence counts.

Never audit:

- evidence bodies;
- message subjects/snippets;
- raw source headers in admin-facing metadata;
- OAuth/provider tokens;
- recipient capability/session tokens;
- prompt/response text;
- raw exception text.

## 16. Privacy and Compliance Invariants

1. The returning employee never reads the coverer's mailbox directly.
2. The coverer must initiate and publish the return package.
3. The original package can seed scope, but cannot authorize mailbox access.
4. Return evidence comes only from the coverer's mailbox and only after the
   coverer reviews/prunes it.
5. Recipient view remains package-local snapshot-only.
6. Admin/security surfaces remain metadata-only.
7. Sensitive/noise gates remain whole-thread and fail closed.
8. No citation, no claim.
9. No productivity/performance framing.
10. No cross-mailbox project-id equivalence is assumed.

## 17. Demo Plan

Extend the deterministic handoff demo with a second mailbox:

- outbound mailbox: Dana / original covered employee;
- return mailbox: Alex / coverer;
- same coverage areas: Nexus Auth, Security Audit, Connection Pool, ML Engineer;
- Alex-side messages during the coverage period that cite:
  - a decision made;
  - an open loop closed;
  - a new open loop;
  - a new stakeholder/domain;
  - one sensitive thread excluded;
  - one noise thread excluded.

Expected demo:

1. Dana publishes coverage package to Alex.
2. Alex opens it.
3. Alex creates return handoff.
4. Projects/coverage areas are preselected automatically.
5. Alex generates, reviews, and publishes.
6. Dana opens the return package and sees a compact coverage-delta brief.

## 18. Implementation Sprint Proposal

Suggested sequence after this spec is approved:

- **S34.1 - Domain migration + ORM.**
  Add `package_type` and `handoff_return_context`; no `ekc_schemas` change.
- **S34.2 - Return scope seed service.**
  Build descriptors from original structured scope, claims, evidence domains,
  and coverage-area labels.
- **S34.3 - Return draft endpoint + auth.**
  Create return package from original package + coverer mailbox; enforce owner
  mailbox and original-recipient proof.
- **S34.4 - Return generation mode.**
  Generate coverage-delta claims from the coverer's mailbox within the carried
  scope and date window.
- **S34.5 - Creator UI setup/review.**
  Add "Create return handoff" setup and return-specific review copy.
- **S34.6 - Recipient return view framing.**
  Reuse recipient package UI with "what changed while you were away" framing.
- **S34.7 - Demo seed + tests.**
  Extend seed with a coverer mailbox and assert automatic carry-forward,
  sensitivity/noise exclusion, recipient snapshot-only, and no mailbox backdoor.
- **S34.8 - Runbook/docs.**
  Add manual demo steps for outbound + return handoff.

## 19. Acceptance Criteria for S34

- Creating a return draft from an original published package preselects the
  original projects/coverage areas without manual relinking.
- The default return date window is original publish date through today, and is
  adjustable.
- The return package source mailbox is the coverer's mailbox, not the original
  employee's mailbox.
- The return recipient defaults to the original package creator.
- A user who is not the coverer/original recipient cannot create the return
  package from the original package id.
- Generated return claims/evidence are non-empty on the demo seed and are scoped
  to the carried coverage areas.
- Sensitive/noise/excluded content never enters return evidence.
- Every return claim cites in-package return evidence.
- Recipient view reads only return package snapshot rows.
- Admin views expose only metadata and never return evidence bodies/subjects.
- Existing coverage package versioning still works and is not confused with
  return handoff linkage.
- Full DB-gated tests pass; frontend build passes if UI changes are included;
  `git diff --check` is clean.

## 20. Open Questions for Product Review

These are not blockers for the spec, but should be answered before S34
implementation is locked.

> **Update — several of these are now resolved.** Questions 1, 2, 3, 4, and 6
> have been answered by product and are **locked as accepted defaults in
> §21** (they are annotated **RESOLVED → §21.N** below). Questions 5, 7, and 8
> remain genuinely open. The §21 defaults govern S34 implementation unless a
> later product decision overrides them.

1. Should the return handoff require the coverer to be a tenant user, or can the
   original recipient capability session be upgraded into a return-creation flow?
   Recommendation: require tenant sign-in / mailbox ownership for the coverer.
   **RESOLVED → §21.1** (coverer auth required; a capability session alone is not enough).
2. Should `HandoffPackage.reason` get a new enum value (`return` or
   `coverage_return`), or should return packages use `delegation` plus
   `package_type=return_delta`?
   Recommendation: add a new safe enum value if the migration already touches
   package constraints.
   **RESOLVED → §21.2** (add `coverage_return` if S34 touches package constraints;
   otherwise `package_type=return_delta` is the primary discriminator).
3. Should the return package default date end be "today" or the original package
   `expires_at` when it is still in the future?
   Recommendation: default to today; never overload expiry as coverage period.
   **RESOLVED → §21.3** (`date_from` = original `published_at`, `date_to` = today;
   `expires_at` is never the coverage-window endpoint).
4. Should Alex be allowed to add a brand-new coverage area not present in the
   original package?
   Recommendation: yes, but only via explicit action and audit.
   **RESOLVED → §21.4** (scope defaults to the original; a new area may be added
   only via an explicit UI action, and any expansion is audited).
5. Should the original employee see "closed loops" as a distinct UI group even if
   the backend stores them as `decision` claims?
   Recommendation: yes in UI if deterministic classification can be done from
   claim text/event type without adding a claim kind.
6. Should return handoff publication revoke or supersede the original outbound
   package?
   Recommendation: no. They are linked artifacts with separate lifecycles.
   **RESOLVED → §21.5** (publishing a return never revokes/supersedes/mutates the
   original; `new-version` stays scoped to same-creator/same-mailbox revisions).
7. Should Admin/Audit Viewer show that a return package is linked to an original
   package?
   Recommendation: yes, metadata only: package ids, type, seed method, dates,
   counts; no content.
8. How long should return packages live by default?
   Recommendation: same default as coverage packages (30 days) unless customer
   policy says otherwise.

---

## 21. Resolved defaults for S34 implementation

These are the **accepted defaults** for building S34. They are locked
implementation guidance, not informal answers: an S34 implementer should build to
them directly, and any deviation requires an explicit, later product decision that
supersedes this section (record such a change here and in `docs/decisions.md`).
They resolve §20 questions 1, 2, 3, 4, and 6; §20 questions 5, 7, and 8 remain
open.

### 21.1 Coverer authentication is required

Creating a return handoff requires the coverer to be **signed in** and to
**own/connect the source mailbox** used to generate the return package. A
recipient **capability session alone is not sufficient** to create a return
handoff. The original package may **seed scope** (§8) but **cannot authorize
access** to the coverer's mailbox — mailbox access derives solely from the
coverer's own authenticated ownership/connection.

*Rationale:* the return package is generated from the coverer's mailbox, so its
authority must come from the coverer owning that mailbox, never from possession of
the original package's capability link. This keeps mailbox custody unambiguous and
fails closed in production. (Implements §7; dev mode may relax sign-in for
localhost testing only.)

### 21.2 Reason enum

If the S34 migration **touches `handoff_package` constraints** (it does — §10.1
adds `package_type`), add a safe `reason` enum value **`coverage_return`** in the
same migration. If for some reason package constraints are **not** touched,
`package_type=return_delta` remains the **primary discriminator** and the return
package reuses an existing safe `reason` value (`delegation`).

*Rationale:* `package_type` is the authoritative return/coverage discriminator
regardless; a dedicated `coverage_return` reason is a low-cost readability win
that is nearly free to add while the constraint is already being altered, and is
not worth a standalone migration otherwise. (Refines §10.3.)

### 21.3 Default coverage date window

- `date_from` = the original package's **`published_at`** date.
- `date_to` = **today** (adjustable by the coverer before generation).
- **`expires_at` is never** used as the coverage-window endpoint.

*Rationale:* expiry governs recipient **access**; the coverage window governs
which **evidence is searched** (§9). `expires_at` can be far in the future, so
reusing it would search the wrong window. A formal planned coverage period, if
ever needed, uses explicit `coverage_starts_on` / `coverage_ends_on` fields, not
expiry.

### 21.4 Adding new coverage areas

The return draft's scope **defaults to the areas carried from the original
package** (§8). The coverer **may add a new area only through an explicit UI
action** ("add related area"), and **any expansion beyond the original coverage
scope is audited** (`return_scope_changed`, §15).

*Rationale:* automatic carry-forward gives ease of use without silently widening
scope; an explicit, audited add keeps the return package from quietly exceeding
what the coverer was originally asked to cover. (Implements §8.3.)

### 21.5 Original package lifecycle is untouched

Publishing a return handoff **does not revoke, supersede, or mutate the original
outbound coverage package**. The two are **linked reciprocal artifacts with
separate lifecycles** (D15). **`new-version` remains only** for revising a package
within the **same creator / same source-mailbox** lineage — it is never used for
the return direction.

*Rationale:* revoking or superseding the original would destroy the coverer's own
record of what they were asked to cover and reintroduce the exact lineage
confusion D15 exists to avoid. The linkage lives in `handoff_return_context`
(§10.2), not in shared lineage or lifecycle state. (Implements §4 / D15.)
