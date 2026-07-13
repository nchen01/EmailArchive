# S16.0 - Date-Range Ingest Planning Spec

Status: ✅ Implemented (2026-07-13) on branch `s16-date-range-ingest-plan`.
Provider-neutral `ListOptions` + shared date validation
(`services/ingest/list_options.py`); Gmail `q=` translation with inclusive
`date_to` (`providers/gmail.py::build_gmail_query`); CLI `--date-from/--date-to/
--plan-only` with date-windowed scoped-snapshot sync-token bypass
(`scripts/gmail_smoke_ingest.py`); shared plan/ingest service
(`services/ingest/gmail_windowed.py`); demo-side backend endpoints
(`POST /api/gmail-ingest/{mailbox_id}/preview|ingest`) and a Status-screen
date-range control (`frontend/src/components/GmailDateRangeControl.tsx`). Live
Gmail validation is manual/operator only (see Manual Validation Flow); all CI
tests mock Gmail. The resolved product decisions below are unchanged.

This is a pre-S16.1 enabling feature. It makes large mailbox demos and smoke
tests practical by letting an operator choose a date window before fetching and
persisting mail.

## Problem Statement

Real mailboxes can contain years of stale mail, newsletters, and low-value
automated updates. Today the Gmail smoke runner can cap by message count, but it
cannot say "only ingest the handoff-relevant period." On large mailboxes this
creates three problems:

- Smoke tests are slow and noisy because the runner pages through newest-first
  mail until the cap is hit.
- Coverage demos overemphasize old or stale projects that are no longer useful.
- Operators must use arbitrary `--max-messages` caps instead of an intentional
  business window such as "the last 90 days" or "Q2 2026."

The product need is a manual pre-ingest filter: before actual ingestion, the
operator chooses the mail date range they want the pipeline to pull.

## Solution

Add a provider-backed date window to the Gmail smoke ingest path.

The first implementation should be intentionally narrow but demo-accessible:

- Gmail only.
- CLI/operator workflow plus a demo-side frontend control.
- Full-fetch snapshots only.
- No DB schema migration.
- No production customer onboarding wizard yet.

The operator or demo user should be able to:

1. Preview a date window without fetching raw message bodies or persisting data.
2. Dry-run the date window to fetch/normalize but not persist.
3. Confirm the date-windowed ingest into the existing L0 + L1 smoke runner.
4. Choose the same date window from the demo-side UI before starting a mailbox
   pull.

Recommended CLI shape:

```powershell
# Preview only: provider listing/estimate, no raw fetch, no persist.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --plan-only `
  --confirm

# Dry-run: fetch + normalize within the date window, no persist.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --dry-run `
  --confirm

# Live ingest: persist only messages returned by the date-windowed provider query.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --max-messages 3000 `
  --confirm
```

`--date-from` and `--date-to` are calendar dates in `YYYY-MM-DD` format. Product
semantics should be:

- `date_from`: inclusive start date.
- `date_to`: inclusive end date.
- missing `date_from`: open start.
- missing `date_to`: open end.

Provider implementation may translate this to provider-specific syntax. For
Gmail, the expected translation is a Gmail search query passed to
`users.messages.list(q=...)`, e.g. an `after:` lower bound plus a `before:`
upper bound adjusted so the product-level `date_to` remains inclusive.

## Why This Belongs Before S16.1

S16.1 creates a purpose-built demo fixture. That fixture is the canonical demo
story, but we still need a practical path for large mailbox smoke tests and real
coverage validation. A date window gives the product lead and engineer a safe
operator control for "recent handoff period only" without pretending that old
mail is equally relevant.

This also reduces the temptation to use `--max-messages` as a proxy for time.
Caps are useful safety rails, but they are not product semantics.

## User Stories

1. As a product lead, I want to ingest only the recent handoff period, so that
   the demo emphasizes active work rather than stale projects.
2. As an engineer, I want to preview how many Gmail messages match a date
   window, so that I can adjust the range before fetching thousands of raw
   messages.
3. As an operator, I want date-windowed dry-runs, so that I can validate noise,
   sensitivity, people, and edge counts before persisting.
4. As an operator, I want date-windowed live ingest, so that large mailboxes can
   be tested without sweeping years of old newsletters.
5. As a reviewer, I want the ingest summary and audit logs to show the selected
   date window, so that later results can be interpreted correctly.
6. As a future customer admin, I want the date semantics to be understandable,
   so that "April 1 through June 30" means what it says in the command.
7. As a privacy reviewer, I want date filtering to happen at provider listing
   time where possible, so that the app does not fetch bodies for messages the
   operator intentionally excluded.
8. As a maintainer, I want this feature not to alter clustering/retrieval
   policy, so that the ingest window remains a source selection control rather
   than a ranking algorithm.

## Implementation Decisions

### D-S16.0-1 - Date window is a provider listing filter

The date range should be applied before raw message fetch. For Gmail, that means
using the Gmail `messages.list` search query rather than fetching all IDs and
discarding messages after normalization.

Reason: the feature exists to reduce large-mailbox fetch volume and avoid
pulling old mail at all.

### D-S16.0-2 - Add a provider-level list options object

Current provider seam:

```text
list_ids(since_token: str | None) -> Iterator[str]
```

Recommended seam:

```text
list_ids(since_token: str | None, options: ListOptions | None = None) -> Iterator[str]
```

`ListOptions` should hold provider-neutral listing constraints:

- `date_from: date | None`
- `date_to: date | None`
- future extension fields only if needed later

Do not overload `IngestParams` with date-window state. `IngestParams` is mostly
normalization/runtime behavior. The date window is source selection, closer to
`IngestConfig.since_token` and `max_messages`.

### D-S16.0-3 - CLI flags are explicit dates, not relative phrases

Use:

- `--date-from YYYY-MM-DD`
- `--date-to YYYY-MM-DD`

Do not start with `--last-days`, `--quarter`, or natural language ranges. Those
can come later. Explicit dates are testable and avoid timezone surprises.

### D-S16.0-4 - Add plan-only preview

Add `--plan-only` to `gmail_smoke_ingest.py`.

`--plan-only` should:

- authenticate;
- create/reuse the mailbox only if needed for token lookup and operator context;
- list or estimate matching message IDs for the date window;
- print the selected date window and count/estimate;
- not fetch raw message bodies;
- not normalize;
- not persist L0/L1;
- not save a sync token.

If Gmail only provides an estimate cheaply, the output should label it as an
estimate. If implementation enumerates IDs to get an exact count, it must still
avoid fetching raw bodies.

### D-S16.0-5 - Date-windowed runs are scoped snapshots, not incremental sync

First implementation should reject or bypass incremental sync when a date window
is present.

Recommended behavior:

- If `--date-from` or `--date-to` is present, treat the run as a full
  date-scoped snapshot.
- Do not use a stored Gmail history token for that run.
- Do not save a new sync token from a date-windowed run.
- Print `sync_token: NOT saved (date-windowed snapshot)`.

Reason: Gmail history is not naturally scoped to the operator's chosen date
window. Saving a history token after a windowed snapshot could make later runs
look complete while silently excluding older messages outside the previous
window. Scoped incremental sync can be designed later if production needs it.

### D-S16.0-6 - Keep `--max-messages` as a safety cap

Date range chooses the business window. `--max-messages` remains a safety cap
inside that window.

If the cap is hit, existing capped-run behavior should remain:

- warn that the snapshot may be incomplete;
- do not save sync token;
- print a clear hint to narrow the date range or raise the cap.

### D-S16.0-7 - Record the window in observable output

The runner summary should include:

- `date_from`
- `date_to`
- whether the window was open-ended
- whether the provider filter was applied
- whether the run hit `--max-messages`
- whether a sync token was saved or intentionally not saved

If audit log has an existing free-form field suitable for details, record the
window there. If not, do not add a migration for S16.0; structured stdout plus
existing audit start/finish rows are enough for this bounded feature.

### D-S16.0-8 - Expose the date window in the demo-side frontend

This feature must be accessible from the demo side, not only from PowerShell.
S16.0 should add a small, bounded frontend control for date selection before
Gmail ingest.

This is not a full production onboarding wizard. The frontend may assume the
local/demo runtime already has the required Gmail token configured in the
backend environment. Do not ask the browser user to paste OAuth token JSON into
the UI.

Recommended UX:

- date-from input;
- date-to input;
- preview/plan action;
- dry-run action if practical;
- clear warning that date-windowed ingest is a scoped snapshot;
- disabled/blocked confirm action until preview or dry-run succeeds.

### D-S16.0-9 - Do not default demos to a fixed range

The demo-side UI should support customizable dates. Do not hardcode a 90-day or
180-day default as product behavior.

It is acceptable to show an example placeholder such as `YYYY-MM-DD`, but the
user/operator chooses the actual range.

## Proposed Tickets

### S16.0.1 - Date-window spec and tests skeleton

Docs-only spec plus test plan. Confirm the resolved product decisions below
before coding.

Done when:

- this document is reviewed;
- date semantics are approved;
- implementation ticket order is accepted.

### S16.0.2 - Provider listing options seam

Add provider-neutral list options and wire them through the ingest pipeline.

Done when:

- fixture provider ignores unsupported date options safely or filters fixture
  dates deterministically if easy;
- Gmail provider receives date options;
- existing callers still work without options;
- protocol tests cover backward-compatible default behavior.

### S16.0.3 - Gmail query construction

Translate date options into Gmail `messages.list(q=...)`.

Done when:

- generated Gmail query is deterministic;
- date bounds are included only when set;
- tests prove start/end/open-ended combinations;
- no user text is interpolated except validated dates.

### S16.0.4 - CLI flags and validation

Add `--date-from`, `--date-to`, and date validation to
`scripts/gmail_smoke_ingest.py`.

Done when:

- invalid dates fail before any Gmail/API/DB call;
- `date_from > date_to` fails with a clear message;
- summary output prints the chosen window.

### S16.0.5 - Plan-only preview

Add `--plan-only` for pre-ingest date-window preview.

Done when:

- no raw messages are fetched;
- no L0/L1 rows are persisted;
- no sync token is saved;
- output labels counts as exact or estimated.

### S16.0.6 - Date-windowed snapshot sync-token rule

Make date-windowed runs bypass stored sync tokens and avoid saving new tokens.

Done when:

- logs make the behavior explicit;
- tests prove stored token is ignored when a date window is supplied;
- tests prove no token is saved after a date-windowed run.

### S16.0.7 - Docs and runbook update

Update the S16 demo runbook and S15 verification matrix only if this becomes a
required demo path.

Done when:

- commands show preview, dry-run, and live run;
- docs warn that windowed ingest is a scoped snapshot, not incremental sync.

### S16.0.8 - Demo-side frontend date-range control

Expose date-range selection in the demo UI before Gmail ingest.

Done when:

- the UI offers customizable `date_from` and `date_to` inputs;
- invalid dates and `date_from > date_to` are blocked before any backend call;
- preview/plan output is visible before live ingest;
- the UI explains that the window is a scoped snapshot and no sync token will be
  saved;
- the UI does not ask for or display Gmail OAuth tokens;
- existing loaded-mailbox demo flow is not broken.

### S16.0.9 - Backend API seam for demo-side preview/ingest

Add the smallest backend seam needed for the frontend control to invoke the
same validated date-window behavior as the CLI.

Done when:

- request validation matches CLI validation;
- endpoint never logs OAuth tokens or raw message content;
- plan/preview mode does not fetch raw bodies or persist rows;
- live mode requires an explicit confirm field/action;
- response includes date window, count/estimate, cap-hit status, and sync-token
  disposition;
- tests cover invalid dates, preview no-persist, and confirm behavior.

## Testing Decisions

Test at the highest seams:

- CLI argument validation for date parsing and invalid ranges.
- Gmail provider query construction with a mocked Gmail service.
- Smoke runner behavior for plan-only, dry-run, live date-windowed runs.
- Sync-token behavior for date-windowed snapshots.

Avoid live Gmail tests for routine CI. Live validation should be manual/operator
only, using a known mailbox and the S15 verification matrix pattern.

Important regression cases:

- no dates -> existing behavior unchanged;
- only `--date-from`;
- only `--date-to`;
- both dates;
- invalid date format;
- start after end;
- date window plus stored sync token;
- date window plus `--max-messages` cap hit;
- `--plan-only` never fetches raw bodies and never persists.
- frontend date validation blocks invalid ranges before backend calls;
- backend preview endpoint never fetches raw bodies and never persists;
- backend confirm endpoint shares the same date-window snapshot semantics as
  the CLI.

## Manual Validation Flow

After implementation, validate with a large mailbox:

```powershell
cd C:\Users\PC\EmailArchive

$env:EKC_MAILBOX_ID = "<mailbox uuid if reusing>"

# 1. Preview the window.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --mailbox-id $env:EKC_MAILBOX_ID `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --plan-only `
  --confirm

# 2. Dry-run the exact same window.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --mailbox-id $env:EKC_MAILBOX_ID `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --max-messages 3000 `
  --dry-run `
  --confirm

# 3. Persist only after the dry-run looks right.
.\.venv\Scripts\python.exe scripts\gmail_smoke_ingest.py `
  --mailbox-id $env:EKC_MAILBOX_ID `
  --owner-email user@example.com `
  --date-from 2026-04-01 `
  --date-to 2026-06-30 `
  --max-messages 3000 `
  --confirm
```

## Resolved Product Decisions

1. Should `date_to` be inclusive for product users?
   - Decision: yes. A human asking for `2026-06-30` expects June 30 to be
     included. Provider code translates this internally.

2. Should date filtering use provider received date or the email's `Date`
   header?
   - Decision: provider received/internal date for Gmail listing. It is what
     Gmail can filter before fetching bodies. Header-date filtering can be a
     later enrichment/reporting concern.

3. Should date-windowed runs save sync tokens?
   - Decision: no for first implementation. Treat them as scoped snapshots and
     make this explicit in output.

4. Should the date window be stored on `mailbox.config`?
   - Decision: not in S16.0. Print it in the run summary and include it in audit
     details only if an existing field already supports it. Avoid a migration
     for this bounded feature.

5. Should the product default to a specific date range for demos?
   - Decision: no. Use customizable dates. The UI can show placeholders or
     examples, but it must not silently choose a fixed default range.

6. Should this feature be available in the frontend?
   - Decision: yes, on the demo side. Add a bounded frontend date-range control
     backed by the same validated CLI/provider semantics. This is not a full
     production customer onboarding wizard.

## Revision — replace-snapshot mode + hardening (2026-07-13)

### D-S16.0-10 — Explicit "replace current mailbox snapshot" mode
Choosing a date window can produce a **clean** workspace, not just a smaller
fetch. This is **explicit and opt-in** — never implied by the presence of
`date_from`/`date_to`. Default remains append/upsert (preserves out-of-window
rows). Replace mode:
- API `replace_snapshot: bool = false`; CLI `--replace-snapshot`; frontend
  "Replace current mailbox snapshot" checkbox with destructive warning copy.
- Requires `confirm=true` / `--confirm`; **cannot** run in preview/plan-only or
  dry-run; still a scoped snapshot (no sync token saved).
- When `replace_snapshot` is false, the API/UI honestly label the run as
  append/upsert (response `mode`), not clean replacement.

**Fetch-before-clear:** the new window is fetched first, and only then is the old
snapshot cleared — a Gmail failure can never leave a mailbox wiped-but-empty.

**Cleared tables** (centralized in `clear_mailbox_snapshot_for_reingest`, scoped
strictly by `mailbox_id`): `message_embedding`, `message_attachment`, `event`,
`thread_project_assignment`, `project_member`, `project`, `edge`, `identity`,
`person`, `org`, `message`, `thread`, and `sync_state` (drops the stale
incremental token). **Never** touched: `audit_log` (append-only), the `mailbox`
row itself, operator `project_label_override`, and any other mailbox.

### API hardening
- **Audit logging:** both `/preview` and `/ingest` write `ingest_start` (before
  provider access), `ingest_finish` (with `message_count`) on success, and
  `ingest_error` on failure — actor `api:gmail-ingest`, scope `gmail.readonly`.
  OAuth tokens, raw message content, and raw exception messages are never logged
  or returned (errors surface as a sanitized 502).
- **Account guard:** before any listing/fetch, the endpoints call Gmail
  `getProfile` and require the token's account to match `Mailbox.owner_email`
  (case-insensitive) — a mismatch is a 409 with no persistence. Protects both
  `GMAIL_TOKEN_<id>` and the fallback `GMAIL_TOKEN`.
- **Mailbox metadata:** `/ingest` sets `Mailbox.owner_person_id` after L1 (like
  the CLI), and resolves `internal_domains` explicitly — a request may supply
  `internal_domains` (validated, lowercased, persisted into `mailbox.config`),
  otherwise `mailbox.config` is used.

## Out Of Scope

- Production customer onboarding wizard.
- M365 date-range provider support.
- Scoped incremental sync for date-windowed mailboxes.
- New DB schema for saved ingest windows.
- Retuning noise/project clustering for old-vs-new mail.
- Changing retrieval ranking or recency scoring.
- Modifying puluo to look cleaner.

## Review Checklist

Before implementation starts, confirm:

- date semantics are approved;
- date-windowed runs are allowed to skip sync-token saving;
- `--plan-only` is desired;
- demo-side frontend access is expected, but a production onboarding wizard is
  out of scope;
- this feature should be implemented before S16.1 demo fixture work or in
  parallel as a separate branch.
