# S49 - Calendar-First Handoff Context

Status: spec-only (proposed). NOT implemented. Do not write code until the product
lead approves this spec and its sub-sprints (S50+).

This is a docs/spec-only planning sprint. It designs how calendar data can
strengthen handoff packages - meetings, deadlines, coverage windows, time-bound
commitments - as the FIRST external connector after package quality/safety/pilot
readiness, per `docs/product-roadmap-quality-first.md` (item 7, "calendar first";
section 4 "Why calendar first"; section 5 integration principles). It is
deliberately narrow and privacy-first, and it must not turn the product into a
surveillance or productivity system.

## 1. Purpose

Calendar is high-signal for a handoff and comparatively low-risk on privacy:
what is due and when, when coverage starts/ends, which commitments are time-bound,
and which meetings a successor may need to attend or be briefed on. S49 plans how a
covered employee can pull their OWN calendar's meeting/deadline context into a
handoff package - reviewed, scoped, and frozen - so the recipient reads it as
package-local snapshot evidence alongside the existing email-derived claims.

Concretely, calendar should let a handoff answer:

- "What is due while I am out, and when?" (deadlines / time-bound open loops)
- "What recurring meetings does this project have, and who runs them?"
- "When does coverage start and end, and what falls inside that window?"
- "Which decisions or commitments are anchored to a specific date?"

It does this by EXTENDING what the creator can pull into a package (roadmap
section 5: "a connector expands what the creator can pull into a package, never
what the recipient can browse live"), reusing the shipped handoff flow (S17), S39
frozen project labels, the S45 guided wizard, the S48 coverage contract, the S40
recipient Ask, the S23 OAuth/token-vault boundary, and the S24 job runner.

## 2. Non-goals (hard boundaries)

- NO productivity scoring, attendance scoring, punctuality/response metrics, or any
  ranking of people by meeting load. This is the AGENTS.md invariant 9 ("do not
  build surveillance") applied to calendar; it is the single most important
  boundary of S49.
- NO manager surveillance surface. Calendar context appears in a covered
  employee's own handoff package and in the metadata-only admin views at most as
  aggregate counts - never a per-person meeting/attendance report.
- NO free browsing of calendars. The creator does not get a calendar explorer; the
  connector fetches only within the creator's chosen coverage window / project
  scope (roadmap section 5: "no broad corpus harvesting").
- NO recipient live calendar access. The recipient never gets a calendar token, a
  live query, or a source link that browses the calendar. Recipient access stays
  package-local snapshot-only (S17 / S39 / S48 invariant).
- NO Slack / Teams / Jira / Linear. Calendar is the ONLY connector S49 plans;
  chat/work-tracker integration stays gated on pilot evidence (roadmap items 8-9).
- NO write access to the calendar. Read-only, least-privilege scopes only (roadmap
  section 5).
- NO attachments, NO conferencing transcripts, NO recording ingestion. Out of
  scope entirely (section 3).

## 3. Exact calendar data allowed

The connector may read, per event, ONLY the following safe fields (this is an
allow-list; anything not listed is dropped at ingest):

- meeting title (event summary)
- start time and end time (with time zone) and all-day flag
- organizer (display name + domain; email retained creator-side only, see
  sensitivity rules)
- attendees (display name + domain; count). Attendee email is retained
  creator-side only and is NOT snapshotted to the recipient by default (section 4).
- recurring status (a boolean + a human-readable recurrence summary such as
  "weekly"; NOT the raw RRULE if it can encode anything sensitive)
- meeting-link PRESENCE as a boolean only (has_conferencing = true/false). The
  actual join URL is NOT stored by default and NEVER snapshotted to the recipient
  (a live link is a browsing affordance; roadmap section 5).
- calendar source / provider metadata (provider = "google", calendar id/name as
  safe label, the creator's own account label)

Explicitly conditional:

- description / body: DEFAULT EXCLUDED from the MVP. Event descriptions frequently
  contain dial-in PINs, personal notes, doc links, and pasted sensitive content.
  The MVP does not read descriptions into any package. A later sprint MAY opt-in a
  description field ONLY behind: the S44 safety scan, explicit creator review, and
  a per-event include toggle - never by default. Recorded as an open question
  (section 13), defaulting to exclude.

Explicitly out of scope (never read):

- attachments and attachment contents
- conferencing transcripts, recordings, captions, or notes
- free/busy of OTHER people, working-location, or any cross-user calendar
- calendar ACLs, delegation graphs, or sharing metadata

## 4. Sensitivity rules

Calendar sensitivity reuses the S44 pre-publish safety model and the existing
sensitivity/exclusion posture, adapted to calendar:

- Private / "private visibility" events are EXCLUDED by default before anything is
  offered to the creator for review. A private event never becomes a package
  candidate unless the creator explicitly overrides for that single event (and even
  then only title/time, never description).
- Personal / out-of-work events (detected by the calendar's own private/personal
  visibility flag, or a personal calendar source) are excluded by default.
- Events whose title matches HR / legal / medical / compensation / security
  keywords (reuse the S44 categories: hr_legal, personal/SSN/medical, payment,
  credential/secret) are EXCLUDED or FLAGGED for creator review, exactly like the
  S44 findings on message-derived claims. HIGH-severity calendar findings block
  publish the same way S44 HIGH findings do today.
- No hidden-content counts to the recipient. Consistent with S48's anti-oracle
  rule and the constant `PrivacyPosture`, the recipient is NEVER told "N calendar
  events were withheld about project X." Excluded calendar events are creator-only
  aggregate metadata at most (like the existing creator-only `exclusion_counts`),
  never a per-topic recipient signal.
- Recipient package stays snapshot-only. Calendar context reaches the recipient
  ONLY as frozen, reviewed, package-local snapshot rows - never a live query.
- Determinism: sensitivity classification is deterministic and rule-based (no LLM),
  matching the repo determinism invariant and the S43/S44 offline posture.

## 5. Creator UX

Calendar is a creator-side enrichment folded into the existing S45 guided wizard
and the S17 review surface; it does not add a separate product area.

- Connect / status: a "Connect Google Calendar" action and a connection-status
  chip, mirroring the S23 Gmail connect UX. It shows provider, connected account
  label, granted read-only scope, and status (connected / needs-reconnect /
  disconnected). Disconnect reuses the S30 audited disconnect pattern.
- Coverage window: the creator selects a coverage window (start/end) - the same
  date-window concept as the S16.0 / S25 ingest window and the handoff scope dates.
  The connector fetches calendar events ONLY inside that window and the creator's
  selected project/person scope.
- Candidate context in the wizard: during the S45 Scope and Review steps, the
  wizard shows CANDIDATE meetings/deadlines derived from the fetched window,
  grouped by the S39 frozen project label where resolvable (by attendee-domain /
  title match to a project; unresolved -> an "unassigned" bucket, same fallback as
  S37/S48). Candidates are clearly marked "not yet included."
- Explicit inclusion: a calendar candidate becomes package content ONLY if the
  creator includes it. Nothing calendar-derived is auto-published. This mirrors the
  S45 prune/include model and roadmap section 5 ("creator reviews before publish").
- Safety review: included calendar items flow through the S44 pre-publish gate
  alongside message claims; HIGH findings block publish until removed or
  acknowledged with a reason (audited, safe-metadata-only, as today).
- Cited evidence only: an included calendar item becomes package evidence and can
  back a claim / coverage-contract item ONLY when it is frozen into the package.
  "No citation, no claim" holds: a calendar-anchored open loop cites its frozen
  calendar item exactly as a message-anchored claim cites its message snapshot.

## 6. Recipient UX

- Project cards may show "Relevant meetings / deadlines": the S48 recipient
  coverage-contract card for a project can carry a small, frozen list of
  meeting/deadline items (title, time, organizer display, recurring boolean),
  rendered as snapshot metadata with evidence collapsed inline - same pattern as
  the existing contract item + evidence disclosure.
- Time-based Ask, package-locally: the S40 recipient Ask gains time-aware intent
  (deadlines, "what is due first", "when does X meet") answered ONLY from the
  frozen package's calendar snapshot rows, deterministic and LLM-free, with the
  same oracle-safe neutral no-answer. No live calendar call.
- No live calendar links. A meeting-join URL is not snapshotted by default; a
  source badge (e.g. "from calendar") is allowed only as approved snapshot metadata
  (roadmap section 5), never as a live-browse affordance. If a future sprint ever
  snapshots a join link, it must be an explicit per-event creator approval and is
  out of the MVP.

## 7. Backend / data-model options

The connector needs (a) a place to hold the creator's fetched calendar events for
review (a live/enrichment layer, creator-owned), and (b) a frozen package-local
snapshot the recipient reads (like `HandoffEvidence`).

- Option A - computed-only / no new storage. Fetch calendar at generate time and
  snapshot directly into the package; keep no persistent calendar tables.
  Pro: no migration for the staging layer. Con: re-fetches on every regenerate
  (API cost + rate limits), no reviewable staging between fetch and generate, and
  it couples generation to a live external call (breaks the offline/deterministic
  test posture and the S24 job model). NOT recommended for the staging layer.
- Option B - service-DB tables for the live/enrichment layer. Add
  `calendar_event` and `calendar_event_attendee` tables (service DB only, NOT
  `ekc_schemas`), tenant + mailbox scoped, holding the section-3 allow-listed
  fields for the fetched window. These are the creator-owned live layer, analogous
  to `Message` / `Event`. Pro: reviewable, idempotent sync via the S24 runner,
  decoupled from generation, testable offline. Con: a service-DB migration.
- Package snapshot layer (needed under either option). The recipient-facing frozen
  rows: EITHER (b1) reuse `HandoffEvidence` with `source_type = "calendar"` (the
  column already exists) plus a synthetic stable calendar citation id, OR (b2) a
  dedicated `handoff_calendar_item` package-local table keyed by `package_id` + a
  stable calendar item id. Recommend (b2): calendar events are not RFC-5322
  messages and have no `message_id_header`, so overloading the message-citation
  space is muddy; a dedicated package-local table keeps the two-id-scheme invariant
  clean (internal UUIDs vs a calendar-native provenance id used for citation),
  frozen at publish like every other handoff row.

Recommendation: Option B for the live layer (`calendar_event` +
`calendar_event_attendee`) plus a dedicated package-local `handoff_calendar_item`
snapshot table (b2). Justify the migration because computed-only cannot provide a
reviewable, idempotently synced, offline-testable staging layer, and because the
recipient snapshot must be frozen and citation-anchored independently of any live
call. All migrations are SERVICE-DB ONLY - no `ekc_schemas` / `SCHEMA_VERSION`
change (these are product-layer tables, mirroring the S23/S24 precedent where
`mailbox_provider_account` and `job` were service-DB migrations 0011/0012).

## 8. OAuth / vault implications

- Provider: Google Calendar first (matches the shipped Google/Gmail OAuth in S23;
  reuses `services/oauth/` - PKCE + state, fail-closed callback, vault-backed
  tokens).
- Read-only least-privilege scope: `https://www.googleapis.com/auth/calendar.
  events.readonly` (or `calendar.readonly`) ONLY. Never a write scope. The scope is
  added to the S23 scope set for a calendar connect, and `scopes_granted` records
  exactly what was granted.
- Refresh tokens stay in the vault. The app DB stores only a `vault_ref` + safe
  provider metadata, exactly as S20/S23 mandate; the token vault boundary is
  unchanged. The shipped `DevTokenVault` remains dev/test-only.
- Mismatch / fail-closed: reuse the S23 `complete_callback` guards - verified
  account email required, `oauth_account_mismatch` on email mismatch, cross-owner
  rejection, replayed-state rejection - all fail closed with audit events and no
  token persisted on failure.
- Provider account model: the shipped `mailbox_provider_account` table has
  `CheckConstraint("provider IN ('gmail')")` and a unique `(tenant_id, mailbox_id,
  provider)`. Two options: (8a) extend the provider check to include a calendar
  provider value (e.g. `'google_calendar'`) so calendar is a distinct provider row
  with its own scopes/vault_ref; or (8b) treat Gmail + Calendar as one Google
  account and add an additional granted scope to the existing row. Recommend (8a):
  a distinct `provider = 'google_calendar'` row (own scopes_granted, own vault_ref,
  own connect/disconnect lifecycle) so calendar can be connected/disconnected
  independently of mail and audited separately - a small service-DB migration to
  widen the provider CHECK. This keeps least-privilege and independent revocation
  clean and avoids implying that connecting mail also connects calendar.

## 9. Job model

- Job type `calendar_sync_window` on the shipped S24 runner (`services/jobs/`,
  `@register(...)`, Postgres `FOR UPDATE SKIP LOCKED`, lease/heartbeat), exactly
  like `gmail_ingest_window` (S25). Calendar sync is background, retryable work -
  never inline in a web request.
- Params: `{ mailbox_id, provider_account_id, window_start, window_end,
  project_scope? }` - safe metadata only. NO tokens in params (the worker resolves
  the token from the vault via `vault_ref`, per S23/S26).
- Idempotency: an idempotency key over `(mailbox_id, provider_account_id,
  window_start, window_end)` so re-enqueue is a no-op / upsert, matching the S24
  idempotency model. Re-sync upserts `calendar_event` rows keyed by
  `(mailbox_id, calendar_item_id)`.
- Progress / errors: safe metadata only (counts, window, status) - NEVER event
  titles, attendee emails, tokens, or provider error bodies (mirrors the S24/S27
  "safe metadata only" rule and the redaction posture).
- Rate limits + kill switch: per-tenant rate/cost controls (S21 section on rate/
  cost) and a fail-closed kill switch (an env/config flag that makes the handler
  no-op) so calendar sync can be globally disabled without a deploy, consistent
  with the S27 fail-closed guardrails.

## 10. Package snapshot model

- Snapshot at generate/publish. When the creator generates (and freezes at
  publish), each INCLUDED calendar candidate is written as a package-local
  `handoff_calendar_item` row (section 7, b2): title, start/end, organizer display,
  attendee-domain summary + count, recurring boolean + summary, has_conferencing
  boolean, provider label. NO join URL, NO attendee emails, NO description by
  default.
- Citation rules. A `handoff_calendar_item` carries a stable `calendar_item_id`
  used as its citation key, analogous to `message_id_header`. A claim or
  coverage-contract item that references a meeting/deadline cites that id; "no
  citation, no claim" holds - a calendar-anchored item with no frozen calendar row
  is dropped, exactly like a message-anchored claim with no evidence (S48
  `contract_items_cited` gate generalizes).
- No live recipient access. The recipient reads `handoff_calendar_item` rows from
  the frozen snapshot only; the recipient route continues to select ONLY
  `handoff_*` rows (the S39/S48 invariant), never `calendar_event` (the live layer)
  and never the provider.
- Coverage-contract integration (S48). The S48 `CoverageContractEntry` gains an
  additive, optional `meetings`/`deadlines` view assembled from the package's frozen
  `handoff_calendar_item` rows grouped by the same S39 frozen `project_label`. It is
  computed-only over frozen rows (consistent with S48's Option A recommendation),
  carries no exclusion counts, and stays identical/safe on both creator and
  recipient DTOs. Deadlines can surface as time-anchored open loops; recurring
  meetings as project context.

## 11. Eval / safety requirements

- S43 eval additions. Extend the offline, deterministic harness
  (`services/handoff/eval/`) with calendar fixtures and additive hard gates:
  meeting/deadline correctness (an included calendar item appears with the right
  time and project grouping), calendar citation-backed (every calendar-anchored
  contract item cites a frozen `handoff_calendar_item`), excluded-calendar-absent
  (a private/sensitive fixture event never appears in the snapshot or contract),
  and reconciliation (the contract's calendar set equals the package's calendar
  set). These reuse the existing S48 gate style; no external API in the harness -
  calendar fixtures are seeded rows, like the message fixtures.
- S44 safety additions. Extend the pre-publish safety scan to calendar items:
  private-visibility exclusion, HR/legal/medical/compensation/security title
  keywords (reuse the S44 categories), and description-never-ingested-by-default.
  HIGH-severity calendar findings block publish identically to message findings;
  overrides remain reason-gated and audited with safe metadata only (never the raw
  reason or the event title).

## 12. Implementation sequence (proposed sub-sprints)

Ordered so the privacy/OAuth boundary lands before any data flows, and the
recipient-facing surface lands last:

- S50 - Google Calendar OAuth / connect (spec then implementation): extend the S23
  OAuth flow with a read-only calendar scope and a `provider = 'google_calendar'`
  account row (service-DB migration to widen the provider CHECK), connect/status/
  disconnect UX, fail-closed mismatch handling, audit events. No calendar data is
  fetched yet.
- S51 - calendar sync job + live layer: the `calendar_sync_window` job on the S24
  runner, the `calendar_event` / `calendar_event_attendee` service-DB tables
  (migration), idempotent windowed fetch, the section-3 allow-list + section-4
  default exclusions applied at ingest, rate limits + kill switch. Creator-owned
  live layer only; nothing recipient-facing.
- S52 - handoff wizard / package integration: candidate meetings/deadlines in the
  S45 Scope/Review steps, explicit inclusion, the `handoff_calendar_item`
  package-local snapshot table (migration), the S44 calendar safety gate, and the
  S48 coverage-contract meeting/deadline view. Creator review before publish.
- S53 - recipient Ask / time-based polish + eval: recipient project-card
  "Relevant meetings / deadlines" rendering, the S40 time-aware package-local Ask,
  and the S43 calendar eval gates. Recipient stays snapshot-only.

This order is safer than integrating first because it proves the OAuth/vault and
job boundary (S50-S51) before any calendar content can reach a package (S52) or a
recipient (S53). Each sub-sprint is separately approvable; each migration is
service-DB only with its own review.

## 13. Open questions and recommended defaults

Encode these defaults unless the product lead objects:

- Descriptions: DEFAULT EXCLUDE event descriptions from the MVP (section 3). Revisit
  only behind an explicit per-event creator toggle + S44 scan. (Default: exclude.)
- Attendee emails to recipient: DEFAULT NO - snapshot attendee display name +
  domain + count to the recipient, keep full emails creator-side only. (Default:
  domain/display only.)
- Join links: DEFAULT NOT snapshotted; `has_conferencing` boolean only. A live join
  link is never a recipient affordance in the MVP. (Default: boolean only.)
- Provider account model: DEFAULT a distinct `provider = 'google_calendar'` row
  (8a), independently connectable/revocable. (Default: distinct provider.)
- Data-model path: DEFAULT Option B live tables + a dedicated
  `handoff_calendar_item` snapshot table (b2). (Default: B + b2.)
- Project resolution for calendar items: DEFAULT deterministic, rule-based mapping
  (attendee-domain / title match to a project) with an honest "unassigned" fallback;
  no LLM. (Default: rule-based.)
- Coverage-window default: DEFAULT the handoff's existing scope window; the creator
  can narrow it. No open-ended full-calendar pull. (Default: scoped window.)
- M365 / Outlook calendar: DEFERRED (Gmail/Google first, matching S20/S23 where
  M365 stays stubbed). (Default: Google only.)
- Kill switch: DEFAULT calendar sync is globally disable-able via config without a
  deploy (section 9). (Default: yes.)

Open (for product lead):

- Should recurring-meeting context be summarized (one row per series) or expanded
  per occurrence in the window? (Recommended: one row per series with a recurrence
  summary; expand occurrences only for deadlines/time-bound items.)
- Should the admin console show aggregate calendar-connection status (metadata
  only)? (Recommended: yes, connection status + counts only, never event content -
  consistent with S28-S31 metadata-only admin.)
- Do we need per-event creator notes on why a meeting matters for the handoff?
  (Recommended: reuse claim text rather than a new free-text field, to keep
  everything citation-backed.)

## Boundary restatement (must hold for every S49 sub-sprint)

- Recipient access stays package-local snapshot-only: no calendar token, no live
  query, no live link; the recipient route reads only `handoff_*` rows.
- No surveillance / productivity scoring: no attendance, punctuality, meeting-load,
  or per-person calendar metrics anywhere (AGENTS.md invariant 9).
- Read-only least-privilege OAuth; refresh tokens stay in the vault; fail-closed on
  mismatch (S20/S23).
- Creator reviews before publish; nothing calendar-derived auto-publishes.
- Deterministic, offline-testable classification and eval; no LLM in the
  sensitivity/eval paths.
- No `ekc_schemas` / `SCHEMA_VERSION` change; every migration is service-DB only.
- One connector: calendar only; no Slack / Teams / Jira / Linear.

Stop after opening/reporting this spec PR. Do not implement S49 (or S50+) until the
product lead approves.
