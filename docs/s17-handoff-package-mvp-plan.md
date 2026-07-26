# S17 - Audited Handoff Package MVP Plan

**Status:** SHIPPED (S17.2–S17.15) and end-to-end validated. This file remains
the plan of record; the sub-sprint status below and `docs/s17-live-validation.md`
describe what is actually built. Manual demo runbook:
`docs/s17-handoff-manual-demo-runbook.md`.

Decision source: **D14** in `docs/decisions.md`.

## 0. Sub-sprint status (as of S17.15)

- **S17.2 ✓** — Domain spec (`docs/s17.2-handoff-package-domain-spec.md`):
  state machine, audit vocab + safe metadata, API surface, capability-token
  handling, invariants, acceptance criteria.
- **S17.3 ✓** — Draft/scope/generate backend: migration `0007`, ORM
  (HandoffPackage/Scope/Claim/Evidence/Exclusion/AuditEvent), deterministic
  generator (whole-thread sensitivity gate + creator exclusions before snapshot),
  audit `_safe_metadata` key denylist.
- **S17.4 ✓** — Creator scope-review UI (`frontend/src/components/HandoffReview.tsx`),
  URL-driven and refresh-safe.
- **S17.5 ✓** — Publish/revoke + one-time capability code (migrations `0008`,
  `0009`), recipient session + package endpoints; only token **hashes** stored;
  the code is consumed atomically on first exchange.
- **S17.6 ✓** — Read-only recipient view at `/handoff/recipient`: reads the
  `#c=<code>` fragment, strips it, exchanges it, and resumes across refresh from
  a `sessionStorage` session token (never the raw code).
- **S17.7 ✓** — Creator publish + one-time share-link UI. Copy is the only
  share action; there is deliberately **no** creator "open" affordance (it would
  consume the one-time code). Published/revoked packages are immutable in the UI.
- **S17.8 ✓** — End-to-end validation + docs alignment:
  `tests/test_s17_handoff.py::test_full_creator_to_recipient_journey` and
  `docs/s17-live-validation.md`.
- **S17.9 ✓** — Recipient package-local ask (`POST /api/handoff/recipient/ask`,
  `services/handoff/ask.py`, ask box on `/handoff/recipient`). **Deterministic,
  LLM-free** term-overlap over the package's own claims + evidence: no package
  evidence, no answer; every citation is an in-package HandoffEvidence header;
  the no-evidence response is a single neutral constant (no existence oracle);
  `package_asked` audit carries only `query_len` + `matched_evidence`.
- **S17.10 ✓** — Package versioning + new-version re-share
  (`POST /api/handoff/{id}/new-version`, "Create revised version" in
  `HandoffReview`). Forks a frozen package (published/revoked/superseded) into a
  fresh **draft** in the same `lineage_id` with `version = max(lineage)+1` and
  `supersedes_package_id` set; copies the previous scope but NOT claims/evidence/
  recipient/sessions/capability code/published-expiry-revoked timestamps
  (evidence is re-snapshotted on Generate). Publishing the new version supersedes
  any still-published package in the lineage — flips it to `superseded`, revokes
  its recipient grant, kills its sessions, and mints a fresh one-time code.
  Audit: `package_version_created` + `package_superseded`, both with safe id/
  version metadata only. No migration (columns already existed).
- **S17.11 ✓** — Static HTML export (`GET /api/handoff/{id}/export.html`,
  `services/handoff/export_html.py`, "Export HTML" in `HandoffReview`). A
  self-contained, read-only snapshot of a frozen package (published/revoked/
  superseded; draft/generated → 409) for demo portability / offline handoff /
  compliance archive. Recipient-view privacy parity: only package metadata, the
  constant posture, claims, and snapshotted evidence — NO mailbox id, exclusion
  counts, Gmail/source/open_url link, capability code, or session token. All text
  is HTML-escaped (XSS-safe), served `text/html; charset=utf-8` as a
  `Content-Disposition: attachment` (`handoff-package-v{version}.html`). Reads
  only `handoff_*` rows; audits `package_exported` with safe counts only.
- **S17.12 ✓** — Package-local recipient navigation tree. **Frontend-only**
  (`frontend/src/utils/packageTree.ts`, `frontend/src/components/
  PackageNavigationTree.tsx`, wired into `RecipientPackage`). A compact
  document-style outline — claim kinds ("What you need to know"), sender groups
  ("People and domains"), and "Supporting messages" — derived PURELY from the
  recipient payload's `claims` + `evidence`; clicking a leaf scrolls to that
  claim group / evidence card and briefly flashes it. NOT the live Relationship
  Map: no Message/Thread/Project/Person/Edge/Event, no relationship-map /
  source-message / Gmail API, no Message-ID / mailbox id / counts / links in the
  labels. Unknown claim kinds still appear; empty packages render nothing.
- **S17.13 ✓** — Manual-demo readiness. Fixes the creator "Start over"
  stuck-on-"Loading…" state (the route-load effect now resets `loading`/`error`
  on the no-route branch), and adds a **creator-only** empty-generation
  diagnostic: `HandoffPackageOut.generation` ({`code`, `event_count`}), computed
  only for an empty generated candidate, distinguishing `no_events_for_mailbox` /
  `no_events_in_scope` / `all_events_excluded_by_policy`. The creator UI shows
  explicit copy (notably that widening the date range will not help when the
  mailbox has no extracted events). No invariant weakened — still no uncited
  claims, no retrieval hits in evidence, sensitivity/noise gates unchanged; the
  recipient view never carries the field (no existence oracle). Real generation
  needs a mailbox with L1 `Event` rows — the seeded test fixture or LLM event
  extraction (`services/enrich/events_llm.py`); puluo has zero and cannot demo
  generation until extraction runs (see `docs/s17-live-validation.md`).
- **S17.14 ✓** — Manual-demo enablement. (1) The creator workspace mailbox is
  now refresh-safe: `Workspace` persists ONLY the mailbox UUID in `sessionStorage`
  (`ekc_workspace_mailbox_id`), so a `/app/handoff/<id>` deep-link reload keeps
  the mailbox loaded instead of showing "Load a mailbox to begin"; nothing else
  is stored (no key/token/content) and `RecipientPackage`'s own storage is
  untouched. (2) `scripts/seed_handoff_demo.py` seeds a deterministic, LLM-free,
  puluo-isolated demo mailbox (`handoff-demo@example.com`) with a coherent
  engineering smoke dataset — Nexus Auth, a Connection Pool incident, ML Engineer
  headcount, Security Audit remediation (6 threads / 9 messages / 9 events, ~7
  claims / 7 evidence) — so the full flow (generate → publish → recipient →
  export → new-version) can be exercised end to end. It also seeds one
  whole-thread-sensitive thread and one noise newsletter, both cited by an event
  and both excluded from evidence, so the sensitivity + noise gates are exercised.
  Guarded by `test_handoff_demo_seed_data_is_coherent` and end-to-end validated by
  `test_handoff_demo_seed_generates_publishes_and_excludes`.
- **S17.15 ✓** — Manual demo hardening + runbook. Canonical
  `docs/s17-handoff-manual-demo-runbook.md` (3 PowerShell windows: seed, backend,
  frontend; full creator → recipient → export → new-version click-through;
  states plainly that puluo is not the Handoff generation mailbox). The seed
  script prints a clean copyable next-step block and gains a `--verify` mode that
  dry-runs a generate to confirm non-empty, exclusion-clean claims/evidence with
  NO publish/token side effects (`verify_seed`, guarded by
  `test_handoff_demo_verify_reports_ok_without_side_effects`). The full flow was
  driven end to end via the API; no manual-demo blockers were found. No generator
  / recipient-security / one-time-code / access-model change.

Deferred to S17.16+: **optional LLM synthesis** for the package ask (S17.9 is
deterministic-only), PDF/docx/zip export (S17.11 ships HTML only), manager
approval, multi-recipient, **rich snapshotted relationship/project/owner trees**
inside the package (S17.12 ships the lightweight package-local nav tree only),
and a stronger production auth boundary.

## 1. Product Thesis

The product should converge on an **employee-initiated audited handoff package**.
The covered employee is not passively analyzed. They actively create a scoped
continuity artifact for the teammate taking over their work.

The existing engine remains the foundation:

`L0 ingest -> L1 enrichment -> project materialization -> L2 retrieval -> L3 synthesis`

But the primary surface becomes a deliberate package:

1. Employee starts a handoff.
2. Employee chooses scope.
3. System proposes a package.
4. Employee reviews evidence and exclusions.
5. Manager approves or requests changes.
6. Recipient receives a scoped package.
7. Access is audited, expirable, and revocable.

This should feel like creating a compliant work handoff, not opening a search
portal over someone else's inbox.

## 2. Target Users

**Primary creator:** covered employee.

Examples: vacation coverage, parental leave, medical leave, internal transfer,
planned role handoff, temporary delegation.

**Primary recipient:** employee taking over or covering the work.

They need fast access to project state, open loops, key people, risks, blockers,
recent decisions, and source evidence.

**Approver / buyer:** team manager or department lead.

They need confidence that the handoff is complete enough for coverage and narrow
enough to be appropriate.

**Governance stakeholders:** HR, Legal, IT/Security.

They define policy and review exceptions. They are not the default day-to-day
user for the MVP.

## 3. Non-Goals

The product must not become employee surveillance or performance evaluation.

Do not build:

- productivity scores;
- responsiveness or effort metrics;
- sentiment analysis about the employee;
- "what did they do all day" views;
- performance summaries;
- promotion, termination, compensation, or ranking support;
- manager-side silent scanning as the normal path.

Use language like **coverage**, **continuity**, **handoff package**, **project
state**, **open loops**, **decisions**, **risks**, **blockers**, **scope review**,
and **cited evidence**.

Avoid language like **monitoring**, **productivity**, **performance**,
**ranking**, **responsiveness**, **effort**, or **surveillance**.

## 4. MVP Flow

### 4.1 Create Handoff

The covered employee clicks "Create handoff package" and chooses a reason:

- vacation / PTO;
- leave;
- role transition;
- project delegation;
- other planned coverage.

The reason is audit metadata and may influence default scope later, but should
not be used for any employment decision.

### 4.2 Choose Scope

The employee sets:

- date range;
- projects;
- people / teams / domains;
- optional keyword or thread filters;
- recipient;
- expiration date.

S16.0 date-window ingest is the first primitive for this scope model. The package
scope should eventually sit above ingest scope: ingest can fetch a window, while
package scope controls what is revealed.

### 4.3 Generate Candidate Package

The system proposes:

- project summaries;
- key people;
- open loops;
- recent decisions;
- blockers / risks;
- relationship context;
- cited evidence;
- excluded/sensitive aggregate counts.

Only safe evidence enters the candidate package. Sensitive/HR/legal/privileged/
personal content is excluded by default.

### 4.4 Review and Prune

The covered employee reviews:

- included projects;
- included people;
- included evidence;
- package answer previews;
- source-message details;
- exclusions.

They can remove threads, evidence, projects, people, or claims. Removed items
should be audit-recorded as exclusions without exposing excluded content to the
recipient.

### 4.5 Approve and Publish

MVP approval model:

- employee-created package can be self-submitted;
- manager approval is recommended for team use;
- HR/legal approval is not required unless a later exception workflow introduces
  sensitive or privileged content.

Publishing creates a read-only package for the recipient. The recipient sees the
handoff package, not raw mailbox search.

### 4.6 Recipient View

The recipient sees:

- briefing;
- projects;
- people to know;
- open loops;
- decisions / blockers;
- cited evidence;
- safe source navigation;
- package-specific Cover-for-me questions.

All answers and evidence are constrained to the package scope.

### 4.7 Audit, Expiration, Revocation

Audit events:

- handoff created;
- scope changed;
- candidate package generated;
- evidence excluded;
- package submitted;
- manager approved / rejected;
- package published;
- recipient viewed;
- source evidence opened;
- package expired;
- package revoked.

Access should have an expiration date. Revocation should remove recipient access
without deleting retained audit rows.

## 5. Compliance Guardrails

Default behavior:

- sensitivity excluded;
- legal/privileged hard-blocked;
- HR/medical/personal/protected-leave content excluded;
- no per-query sensitive existence oracle;
- no raw mailbox access for recipient;
- no productivity/performance framing;
- all cited claims trace to included package evidence;
- all package changes audited.

Future higher-permission paths:

- manager-initiated package;
- unavailable-employee or emergency access;
- legal-approved privileged inclusion;
- HR-approved leave/offboarding coverage.

Those are not MVP and should require separate decisions.

## 6. Proposed Domain Objects

These are planning names, not yet schema commitments.

`HandoffPackage`

- id;
- mailbox_id;
- creator_person_id / creator_email;
- status: draft, generated, submitted, approved, published, expired, revoked;
- reason;
- title;
- created_at, updated_at, published_at, expires_at;
- source date window;
- policy mode.

`HandoffScope`

- package_id;
- date_from / date_to;
- included_project_ids;
- included_person_ids;
- included_thread_ids;
- excluded_thread_ids;
- excluded_message_id_headers;
- allowed_domains;
- recipient_email(s).

`HandoffEvidence`

- package_id;
- message_id_header;
- subject;
- sender;
- timestamp;
- snippet;
- source_type;
- included claim IDs or answer IDs.

`HandoffExclusion`

- package_id;
- exclusion_type: sensitivity, user_removed, policy_removed, duplicate, low_confidence;
- target_type: message, thread, project, person, claim;
- target reference;
- aggregate-only display label where needed;
- audit reason.

`HandoffAuditEvent`

- package_id;
- actor;
- action;
- timestamp;
- metadata with no message body or secret content.

## 7. Dependency-Ordered Tickets

### S17.1 - Product vocabulary and UI copy alignment

Replace remaining generic "query mailbox" language in high-traffic surfaces with
handoff-package language. Keep Cover-for-me as a tool inside the package, not the
entire product.

Done when: landing, overview, status copy, and docs use package/continuity
language consistently.

### S17.2 - Read-only package domain model spec

Write the schema/API spec before implementation. Decide whether package objects
live in `ekc_schemas`, SQLAlchemy-only API schemas, or both.

Done when: package statuses, scope fields, evidence fields, and audit events are
specified with invariants.

### S17.3 - Draft package generator

Build a backend function that creates a candidate package from an existing
mailbox + scope. It should reuse existing L1/L2/L3 outputs and return a
deterministic package preview.

Done when: fixture/demo mailbox can generate a candidate package without
persisting recipient access.

### S17.4 - Scope review UI

Create the employee review surface: included projects, people, evidence,
excluded aggregate counts, and remove controls.

Done when: user can inspect and remove evidence before publish.

### S17.5 - Package publish and recipient view

Persist a package and render it as a read-only workspace for the recipient.

Done when: recipient sees only package-scoped content; Cover-for-me answers are
bounded by the package.

### S17.6 - Package audit trail

Persist audit events for create, scope changes, exclusions, publish, access,
expiration, and revocation.

Done when: every package mutation creates an audit event with no body text,
secrets, or raw content.

### S17.7 - Expiration and revocation

Add package lifecycle controls. Expired/revoked packages should not expose
recipient content but audit rows remain.

Done when: expired/revoked package access is blocked and tested.

### S17.8 - Manager approval path

Add an approval step after employee review.

Done when: manager can approve or request changes; recipient cannot access until
approved/published.

This can be deferred if S17 needs to stay small.

## 8. Open Product Questions

1. Is manager approval mandatory for MVP, or can the employee publish directly
   in the first demo version?
2. Should the first package support exactly one recipient, or multiple recipients?
3. What is the default expiration period: 14 days, 30 days, or chosen per package?
4. Should package scope be editable after publish, or should edits create a new
   version?
5. Should the package show aggregate excluded counts ("12 messages excluded by
   sensitivity policy") or only a global privacy posture?
6. Should recipient source navigation open Gmail when possible, or stay inside
   package evidence for stricter scope control?
7. What is the first real-life use case to optimize: vacation coverage, parental
   leave, internal transfer, or planned offboarding?

## 8a. Resolved (2026-07-16)

Confirmed with the product owner. Coding of schema/API may proceed from these.
Items marked **changeable** may be revisited as the model matures.

1. **Approval — direct publish.** The employee self-publishes in the MVP. Manager
   approval (S17.8) is deferred, but `submitted`/`approved` statuses are reserved
   in the status enum now so adding approval later needs no migration.
2. **Recipients — one per package.** Single coverage recipient for MVP;
   multi-recipient is a fast-follow once single-recipient grant/expiry/revocation
   is proven.
3. **Expiration — 30 days default, per-package override.** `expires_at` defaults
   to `published_at + 30 days` and is adjustable per package.
4. **Post-publish edits — immutable + versioning.** A published package is frozen;
   editing scope/evidence creates a new version that supersedes it (the prior
   version stays revocable). Evidence is snapshotted at publish.
5. **Exclusion display — global posture for the recipient.** The recipient sees
   only a global privacy statement ("sensitive categories are excluded by
   default"), never counts. The **creator** sees aggregate exclusion counts during
   scope review (their own mailbox). No sensitive-volume leak to the recipient.
6. **Recipient evidence — self-contained package, no live-mailbox link.** The
   package includes the actual **safe, included** message content (normalized,
   sensitivity-gated subject / sender / date / body snippet), **snapshotted at
   publish**, so the recipient reads evidence *inside* the package with no
   dependency on the live mailbox and no reach beyond approved scope.
   Sensitive/excluded content never enters `HandoffEvidence`. The existing S11–S14
   evidence drawer and S14 "Search in Gmail" affordance stay live (for the
   **creator's own-mailbox** review and future flexibility) — **do not delete**.
   **Changeable:** recipient-side live links could return later under stricter
   permissioning.
7. **First use case — vacation coverage.** Employee-present, cleanest consent,
   most frequent, lowest sensitivity; the S16 demo fixture + narrative optimize
   for this. Internal transfer is the intended secondary.

**Carried into the S17.2 spec:** `HandoffPackage.status` reserves approval states;
`HandoffRecipient` is 1:1 with a package in MVP; `expires_at` default =
`published_at + 30d`; publish freezes evidence and supports supersede/versioning;
`HandoffEvidence` stores snapshotted **safe** content (no raw MIME, no sensitive,
no live-mailbox dependency); the recipient view shows global privacy posture only.

## 9. Relationship To Existing Sprints

- S11-S14 evidence/source navigation becomes the package inspection system.
- S16.0 date-range ingest becomes an ingest-scope primitive.
- S16.1 demo fixture should be authored to demonstrate handoff-package creation,
  even if package persistence is not implemented yet.
- S17 should not change core clustering/retrieval/synthesis algorithms unless a
  package-specific invariant requires it.

## 10. Acceptance Criteria For The Direction

The product is on-track when a viewer can say:

"The employee created a scoped handoff, reviewed what would be revealed, shared
only the needed project context, and the recipient can verify every claim from
approved evidence."

The product is drifting when it feels like:

"A manager or HR person is searching and evaluating an employee's inbox."
