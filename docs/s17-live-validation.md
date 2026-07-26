# S17 Handoff Package — Live Validation (current through S17.14)

**Originally:** 2026-07-23, S17.8 — end-to-end handoff package validation +
docs/status alignment. **Kept current** through later sub-sprints.

This document records the validated state of the employee-initiated audited
handoff package flow (D14). The end-to-end journey (§3) was first executed at
S17.8 (covering the S17.2–S17.7 flow); S17.9 added the deterministic package-local
ask, S17.10 new-version re-share / supersede, and S17.11 static HTML export, each
with its own tests in `tests/test_s17_handoff.py`. S17.12 adds a frontend-only
package-local recipient navigation tree (no backend, no new package facts);
S17.13 the "Start over" fix + creator-only empty-generation diagnostic; and
S17.14 a refresh-safe workspace mailbox + the deterministic handoff-demo seed
(see §4 "Handoff demo readiness").

---

## 1. Status: what works end-to-end

The full creator-to-recipient flow is implemented and validated:

Creator (`/app/handoff`) — create draft → set optional date scope → generate →
review claims/evidence + creator-only exclusion summary → remove an evidence
item and regenerate (unsupported claims drop) → publish to one recipient with a
default 30-day expiry → copy the **one-time** recipient link. After publish the
creator surface is immutable (no scope edit / regenerate / remove) and the raw
link is not recoverable on refresh.

Recipient (`/handoff/recipient#c=<code>`) — the SPA reads the capability code
from the URL fragment, strips the fragment, exchanges it for a short-lived
session, and renders a read-only, package-local view (title, creator, reason,
published/expires dates, constant privacy posture, grouped claims, snapshotted
evidence). A refresh resumes from the `sessionStorage` session token. A consumed
link opened in a fresh session, an expired/revoked session, and any other
failure all collapse to one neutral "no longer available" state.

Lifecycle — the creator can revoke a published package; revocation immediately
blocks the recipient (even a previously-live session) with the same neutral
state. For a lost link, wrong recipient, or scope revision, the creator forks a
new version in the same lineage (S17.10); publishing it supersedes the prior
published version, blocks its recipient, and mints a fresh one-time link.

---

## 2. Validation method

Two complementary methods were used; neither transmits real mailbox content.

**Backend / state machine — executed.** A single contiguous end-to-end test,
`tests/test_s17_handoff.py::test_full_creator_to_recipient_journey`, walks
steps 2–16 of the flow below in order against a real Postgres (`ekc_test`) via
the FastAPI `TestClient`. It seeds a deterministic mailbox with L1 `Event` rows
(no Gmail, no LLM, no Voyage) and asserts every transition. Raw capability
codes and session tokens are asserted on but never printed or logged.

**Frontend behaviors — build + code inspection.** The browser-only behaviors
(step 1, fragment stripping, `sessionStorage` resume, locked controls, honest
copy) were verified by `npm run build` (tsc typecheck + vite) and direct code
review, per the repo convention that there is no frontend test runner
(`docs/s15-verification-matrix.md`). The S17.7 pre-merge fix already removed the
one creator footgun found in review (an "Open recipient view" link that would
consume the one-time code) and corrected the "cannot be recovered" copy.

**Mailbox used:** an ephemeral, per-test seeded fixture mailbox in `ekc_test`
(random UUID, torn down after the test). A real live mailbox was not used
because `ekc_dev` currently holds messages but zero `Event` rows, so live
package generation there would require re-running enrichment; the deterministic
seeded harness gives a repeatable, content-safe end-to-end proof instead. No raw
capability code or recipient link is recorded here.

---

## 3. Flow steps and result

| Step | Behavior | Result |
|---|---|---|
| 1 | Creator opens `/app/handoff` | ✓ build/route (UI) |
| 2 | Create draft | ✓ executed |
| 3 | Set optional date scope | ✓ executed |
| 4 | Generate → claims + evidence | ✓ executed |
| 5 | Review claims/evidence + exclusion summary (creator-only) | ✓ executed |
| 6 | Remove one evidence item → regenerate → unsupported claim disappears | ✓ executed |
| 7 | Publish with one recipient + default 30-day expiry | ✓ executed |
| 8 | Copy one-time recipient link | ✓ UI (copy-only; no consume) |
| 9 | Creator refresh: published, controls locked, raw link not recoverable, honest copy | ✓ executed (immutability 409s) + UI |
| 10 | Recipient opens `/handoff/recipient#c=<code>` | ✓ executed (exchange) |
| 11 | URL fragment stripped | ✓ code inspection (UI) |
| 12 | Recipient package renders title/creator/reason/dates/posture/claims/evidence | ✓ executed (payload) |
| 13 | Recipient refresh resumes from sessionStorage | ✓ executed (session reuse) + UI |
| 14 | Consumed link in fresh session → neutral unavailable | ✓ executed (403) |
| 15 | Creator revokes | ✓ executed |
| 16 | Previously-live recipient session blocked → neutral unavailable | ✓ executed (403) |

`test_full_creator_to_recipient_journey` also asserts the recipient payload
contains no `mailbox_id`, `exclusion_counts`, `open_url`, or `gmail` string.

---

## 4. Issues found

No new defects were found during S17.8 validation. The only P1 discovered in
this arc (the creator "Open recipient view" link consuming the one-time code)
was found and fixed in the S17.7 pre-merge review; it is retained here as the
canonical example of the footgun the copy-only design avoids.

Documentation drift was corrected: `README.md`, `AGENTS.md`,
`docs/implementation-plan.md`, `docs/s17-handoff-package-mvp-plan.md`, and the
`CLAUDE.md` status line no longer describe S17.5–S17.7 as future work.

**S17.13 (manual-demo fixes).** Two P1s from a live demo were fixed: (1) the
creator "Start over" left the review surface stuck on "Loading handoff package…"
because the route-load effect cleared `pkg` but not `loading`/`error` on the
no-route branch — now all route-load state resets. (2) Generating a handoff on
the **puluo** mailbox produced an empty package with no explanation. Root cause
is by design: `services/handoff/generator.py` derives claims only from extracted
L1 `Event` rows, and puluo (`e21c187a-956a-47ee-92aa-b21badd16f4d`) has **zero**
Event rows, so no date range can produce claims. The invariants are untouched
(no uncited claims, no retrieval hits pulled into evidence). Instead a
creator-only `generation` diagnostic (`GET`/generate response) now distinguishes
`no_events_for_mailbox` / `no_events_in_scope` / `all_events_excluded_by_policy`,
and the creator UI shows explicit copy — critically, that widening the date range
will not help when the mailbox has no events. The recipient view never carries
this field (no existence oracle).

**Handoff demo readiness (S17.14).** The Handoff flow needs a mailbox with L1
`Event` rows. **puluo (`e21c187a-…`) has zero Events, so it is not a full Handoff
generation demo mailbox** — its `Event` rows would only exist if the Anthropic-LLM
event extraction had been run for it; the UI now says so via the
`no_events_for_mailbox` diagnostic rather than looking like a failed Save/Generate.

Two supported paths:

- **Preferred — seed the deterministic demo mailbox (no LLM, no Gmail):**
  ```powershell
  $env:DATABASE_URL='postgresql+psycopg2://ekc:<pw>@localhost:5432/ekc_dev'
  .\.venv\Scripts\python.exe scripts\seed_handoff_demo.py
  ```
  Prints a `mailbox_id` for the isolated `handoff-demo@example.com` mailbox. The
  smoke dataset is a coherent engineering handoff — **Nexus Auth, Connection Pool
  incident, ML Engineer headcount, Security Audit remediation** (6 threads / 9
  messages / 9 events) — plus one whole-thread-sensitive thread (comp review) and
  one noise newsletter, both cited by an event and both **excluded** from
  evidence. Load that id in the workspace → Handoff → Create draft → Generate →
  expect **~7 claims / 7 evidence** across `acme.dev` + `datadoghq.com` → Publish
  → recipient → export → new-version all work. The script only ever touches its
  own mailbox (idempotent; never puluo). Verified end to end by
  `test_handoff_demo_seed_generates_publishes_and_excludes` (asserts the sensitive
  + noise content is absent from evidence, the recipient view, and the HTML
  export) and its data shape by `test_handoff_demo_seed_data_is_coherent`.

- **Real mailbox — run L1 event extraction:** the Anthropic-LLM `extract_fn`
  (`services/enrich/events_llm.py`, requires `ANTHROPIC_API_KEY`) inside the
  enrichment pipeline must run and persist Events for the target mailbox before
  Handoff testing. This costs Anthropic tokens per thread and requires explicit
  key authorization — not run here.

---

## 5. Deferred to S17.15+

- **Optional LLM synthesis** for the package ask. The recipient package-local
  **ask** shipped in S17.9 as a *deterministic, LLM-free* term-overlap over the
  package's own claims + evidence (`POST /api/handoff/recipient/ask`): no package
  evidence → no answer; every citation is an in-package HandoffEvidence header;
  the no-evidence response is a single neutral constant (no existence oracle).
  LLM synthesis over that same package-local evidence is the optional next step.
- **PDF / docx / zip export.** Static **HTML** export shipped in S17.11
  (`GET /api/handoff/{id}/export.html`) — a self-contained, escaped, read-only
  snapshot with recipient-view privacy parity. Other formats are the next step.
- **Manager approval** (states `submitted`/`approved`/`rejected` reserved since 0007).
- **Multi-recipient** (currently exactly one recipient per package).
- **Rich snapshotted relationship/project/owner trees** inside the package.
  S17.12 shipped a *lightweight, frontend-only package-local navigation tree*
  (`PackageNavigationTree`) derived purely from the recipient payload's claims +
  evidence — claim-kind groups, sender groups, and supporting messages, scroll
  navigation only. A richer graph-backed/snapshotted tree remains deferred.
- **Stronger production auth boundary** — the recipient session is a short-lived
  bearer suitable for the MVP, not a hardened production identity boundary.

---

## 6. Safety notes (must stay true)

- The **one-time capability code is shown once**, at publish, and is consumed on
  first exchange; the server stores only its sha256 hash.
- The **raw code is not recoverable** after a creator refresh — it lives only in
  transient React state, never storage, never logs, and only ever in the URL
  fragment of the copyable link.
- The **recipient session is stored only in `sessionStorage`** (tab-scoped), not
  `localStorage`; the raw code is never stored.
- The **recipient reads package-local snapshotted evidence only** — recipient
  endpoints touch only `handoff_*` rows, never the live mailbox tables.
- **No Gmail/source-mailbox links** and **no mailbox id / exclusion counts** are
  ever exposed to the recipient; the privacy posture is a constant, so it cannot
  act as an existence oracle for sensitive/excluded content.
- Every mutation and recipient access writes a `handoff_audit_event` with safe
  metadata only (counts/ids/status; never body/subject/snippet/token/secret).
