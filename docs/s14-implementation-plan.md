# S14 — Evidence & Source Navigation Polish

**Status:** ✅ Implemented / live-validated (2026-07-10). UX + evidence-trust
polish on top of the existing S0–S13 engine. **No new retrieval model, no
raw-MIME archive, no provider-wide sync, no M365, no permissions/admin.**

## Live validation (product-side, 2026-07-10)

Manually validated against the real `puluo` mailbox demo path:

- Cover-for-me answers render with cited evidence; citation chips open the
  drawer, which shows the richer S14 source context (subject / date / sender /
  snippet / Message-ID).
- **Copy Message-ID** affordance is present; the Gmail `rfc822msgid` **"Search
  in Gmail"** affordance is accepted as *best-effort* wording, not an
  exact-email guarantee (see D-S14-1 verdict below).
- Owner-tree Relationship Map `direct_exchange` edges show clickable source
  message IDs and open the S14 source detail view.
- Project-tree structural edges correctly do **not** fabricate source message
  IDs; the new `evidence_kind`-based provenance note (project / thread / domain)
  clearly explains why no source-message citation exists.
- Sensitive / HR-style no-leak behavior looked correct in the demo path.
- Network Map appears unchanged.

**D-S14-1 verdict:** "Search in Gmail" via `rfc822msgid` is accepted as
best-effort (it opens a Gmail search in whatever account the operator is signed
into); **Copy Message-ID remains the guaranteed fallback**. The API keeps the
`open_url` field; no `provider_id` links.

**Relationship Map evidence nuance (confirmed expected):** only
`direct_exchange` / `evidence_kind="message_headers"` edges carry clickable
source-message details. Structural `project_copresence` / `thread_copresence` /
`org_affiliation` / `bridge` edges are backed by project / thread / domain
evidence and intentionally have empty `source_message_ids`; the drawer shows a
provenance note instead of fabricating a Message-ID. See
`services/relationships/derive.py` (`_direct` is the only path that populates
`rel.message_ids`, gated on ≥1 safe header).

## Product goal

Make the evidence trail feel first-class. A user reading a Cover-for-me answer
or inspecting a Relationship Map edge should be able to go from a claim to the
**cited source message's context** — subject, date, who sent it, a snippet, and
where the evidence came from — without confusion, without leaking sensitive
content, and without a raw RFC `Message-ID` being the primary thing they see.

Today (S11) the evidence drawer already shows subject/date/snippet/message-id
from `supporting_evidence` and never fetches bodies or sensitive content. S14
sharpens that: a safe per-message detail lookup, richer labels, grouped
citations, a copy-id affordance, and a *tested* (not faked) Gmail deep-link.

## Invariants carried from AGENTS.md (do not break)

- **No citation, no claim.** Everything the drawer shows resolves to a
  `Message.message_id_header` that a claim actually cited.
- **Two id schemes.** UUIDs are internal; the citation/provenance key is
  `message_id_header`. The new endpoint is keyed on `message_id_header`, never
  on the internal `Message.id`.
- **Sensitivity gate defaults to exclude.** Sensitive messages must never reach
  a detail response. Consistent with S9/S13, exclusion is **whole-thread**: if a
  message *or any message in its thread* carries a non-`{none}` sensitivity tag,
  the message is treated as absent (→ 404), so the response cannot even confirm
  it exists.
- **No raw MIME, no full body, no tokens.** Snippet only (first 200 chars of
  `clean_text`, the same normalized text the FTS/embeddings use).
- **Determinism.** Same inputs ⇒ same output; no wall-clock in logic paths.

## Scope & task breakdown (dependency-ordered)

These are the S14 "tickets." Small enough to land as **one PR** (or two:
`S14.1–S14.3` backend, `S14.4–S14.6` frontend). Each has a done-condition.

### S14.1 — Safe source-message detail API

**New:** `GET /api/source-message/{mailbox_id}?message_id_header=<url-encoded>`
in `services/api/routers/source_message.py`, registered in `services/api/main.py`.

Query-param (not path) for the header because RFC Message-IDs contain `<`, `>`,
`@`, and other path-hostile characters; URL-encoding a query value is the clean,
cacheable form.

Response DTO `SourceMessageDetail` (new `services/api/schemas/evidence.py`):
- `message_id_header: str`
- `subject: str` (MIME-decoded via `decode_mime_words`, like cover-for-me)
- `date: str` (ISO 8601)
- `sender_display: str` — the parsed display name if present, else the address
  local-part. Bounded by one rule: never expose more than the workspace already
  shows elsewhere. The Network Map already surfaces each contact's full
  `canonical_email`, so a display name or bare local-part is strictly less
  exposure; that is the guarantee we hold to (no first-char masking — it would
  be inconsistent with, and stricter than, the rest of the workspace).
- `sender_domain: str` — the sender's email domain (the part after `@`, already
  lowercased by ingest). Not a `tldextract` registered domain — just the domain.
- `provider_type: Literal["gmail", "msgraph"]` — from `mailbox.provider`.
- `snippet: str` — first 200 chars of `clean_text`.
- `source_type: Literal["l1_structured", "l2_retrieval"] | None` — retrieval
  provenance when known (optional; the drawer already gets answer-level status).
- `open_url: str | None` — Gmail deep link **only if S14.2 proves it reliable**;
  otherwise `None` (never faked).

Behaviour:
- **404** for a header that does not exist in this mailbox, that belongs to a
  different mailbox, or that is sensitivity/whole-thread-excluded. All three are
  indistinguishable by design (no existence oracle for sensitive mail).
- Mailbox boundary enforced with `mailbox_id` in the `WHERE` clause.
- Noise messages are still returnable (a newsletter can be a legitimate
  citation); only sensitivity excludes. Document this choice inline.

**Done when:** endpoint returns the DTO for a normal citation, returns 404 for
missing / wrong-mailbox / sensitive, and never includes body/MIME/token fields.

### S14.2 — Gmail deep-link investigation (flag, don't fake)

Investigate whether a reliable Gmail link can be built from what we store.
Candidates:
- `https://mail.google.com/mail/#search/rfc822msgid:<url-encoded Message-ID>` —
  uses the RFC `Message-ID` we already store; **account-index agnostic** (no
  hardcoded `/u/0/`), but requires the operator to be signed into the mailbox
  account. This is the most durable option.
- `https://mail.google.com/mail/u/0/#all/<provider_id>` — uses the Gmail
  internal id; brittle (assumes `u/0` is the mailbox) — **rejected** unless
  proven.

**Decision rule:** expose `open_url` **only for `provider_type == "gmail"`**,
built from the `rfc822msgid:` search form, and treat it as *best-effort, opens
in your signed-in Gmail*. It must be manually verified against the real mailbox
in the S14 demo before we call it reliable. `copy Message-ID` (S14.5) is the
guaranteed fallback and ships regardless. For `msgraph`/unknown → `open_url =
None`.

**D-S14-1 RESOLVED (2026-07-09):** Use the best-effort `rfc822msgid` link.
- Gmail mailboxes only; `open_url = https://mail.google.com/mail/#search/rfc822msgid:<url-encoded Message-ID>`.
- URL-encode the `Message-ID` value; strip nothing else.
- Never expose `open_url` for non-Gmail providers.
- `copy Message-ID` stays available even when `open_url` exists.
- UI label is **"Search in Gmail" / "Find in Gmail"**, never "Open exact
  email" — reliability depends on the signed-in account and Gmail search.
- If live manual testing fails, keep the API field but hide/disable the UI
  action until a more reliable provider-specific deep link exists.
- Do **not** use `provider_id` links.

### S14.3 — Upgrade `EvidenceMessage` / `supporting_evidence`

Add **optional, default-valued** fields to `EvidenceMessage`
(`services/api/schemas/cover_for_me.py`, or promote the model into the shared
`evidence.py` and re-export to avoid churn): `sender_display`, `sender_domain`,
`source_type`, `open_url`. Populate them in `_build_supporting_evidence` from
the L2 hit / L1 row already loaded (sender fields come from `Message.sender_email`
+ `addresses`). All fields default so old clients and existing tests keep
passing. **No `SCHEMA_VERSION` bump** — `EvidenceMessage` is an API DTO, not an
`ekc_schemas` contract model (confirm before editing).

**Safety (P1, post-review):** because S14 makes `supporting_evidence` richer
(snippet/sender/`open_url`), `_build_supporting_evidence` MUST apply the *same*
whole-thread sensitivity gate as `/api/source-message`, not a weaker "all cited
headers" query. Both paths now share one predicate — `fetch_safe_source_rows` in
`services/api/evidence.py`. A cited header that is missing, wrong-mailbox,
directly sensitive, or in a thread with a sensitive sibling is omitted from
`supporting_evidence` entirely (the claim chip degrades to "detail not
available"); no snippet/sender/link is emitted for it. L2 hits are re-checked
against the safe row here rather than trusting that retrieval already excluded
them.

**Done when:** cover-for-me responses carry the richer labels, blocked headers
are omitted, and existing cover-for-me tests still pass.

### S14.4 — Evidence Drawer UX upgrade (`EvidenceDrawer.tsx`)

- Show subject, date, sender display + domain, snippet, `source_type` label, and
  the answer-level retrieval-source line (already present).
- **Group repeated citations:** the inline citation chips stay attached to
  each claim (that claim→evidence mapping is the useful context and is worth
  keeping), but the drawer consolidates: opening any citation shows the single
  source once and, when several claims cite it, a "cited in N places" count.
  So the *detail* view is deduped-with-a-count even though each claim keeps its
  own contextual chip. (Backend `supporting_evidence` is already unique per
  header; the count is a UI concern computed over `result.claims`.)
- Keep it compact and professional; keep the graceful "no preview available"
  degraded state for headers with no evidence row.

### S14.5 — Copy-id + search-in-Gmail affordances

- **Copy Message-ID** button in the drawer (always present; uses
  `navigator.clipboard`, with a select-on-click fallback). This is the
  guaranteed fallback when no deep link exists.
- **Search in Gmail** link rendered **only when `open_url` is non-null**
  (i.e. gmail + S14.2 verified). Labeled as best-effort — "Search in Gmail" /
  "opens a Gmail `rfc822msgid` search in your signed-in account" — not an
  exact-email guarantee.

### S14.6 — Consistent evidence affordance across surfaces

- **Cover-for-me** → uses the upgraded `EvidenceDrawer` (primary surface).
- **Relationship Map** → `RelationshipDetailDrawer.tsx` currently lists raw
  `source_message_ids`. Make each id inspectable: clicking one calls
  `/api/source-message` and shows the same subject/date/snippet detail (reuse
  the detail fetch/hook). Where an edge has no message headers, keep the current
  compact list. Network Map is **untouched**.
- **Project View** citation chips may stay lightweight, but must not visually
  conflict with the drawer styling.

### S14.7 — Tests & verification

Backend (`tdd`, `FakeEmbedClient` only — no live Voyage):
- `test_api_source_message.py`: found → DTO with expected fields; wrong mailbox
  → 404; missing header → 404; sensitive message → 404; whole-thread-sensitive
  sibling → 404; snippet truncated to 200; sender_domain derived correctly;
  no body/MIME/token keys in the JSON.
- Cover-for-me tests continue to pass with the new optional fields.

Frontend:
- `npm.cmd --prefix frontend run build` must pass. Check whether a FE test
  runner exists; if not, **document manual verification here** (no runner is
  added in S14).

## Manual demo (against the real `puluo` mailbox — operator-run)

Requires the live stack and, for embeddings, the operator's explicit Voyage
authorization per `CLAUDE.md`. Steps:
1. Cover-for-me query that yields multiple citations across claims.
2. Open a citation → confirm subject / date / sender / snippet are readable.
3. Confirm a header cited by several claims is grouped (one row, "cited in N").
4. Confirm **copy Message-ID** works; if `provider == gmail`, click **Search in
   Gmail** and confirm it opens a Gmail `rfc822msgid` search in the signed-in
   account; if Gmail returns the expected message, record that as the live
   verdict. Copy Message-ID remains the guaranteed fallback.
5. Run a sensitive HR-style query → confirm no source content is exposed and the
   detail endpoint 404s for any excluded header.
6. Open the Relationship Map, pick an edge with source messages → confirm the
   same detail drawer opens; confirm **Network Map is unchanged**.

## Out of scope (explicit)

Raw-MIME archive/object store, provider-wide email sync, M365 support,
permissions/admin surfaces, and any new retrieval/embedding model. S14 is trust
and navigation polish only.

## Skills/flow used

`to-spec`-equivalent: this doc (Matt Pocock `to-spec`/`to-tickets` are
user-invocation-only and no tracker is provisioned, so the repo-native
`docs/sNN-*-plan.md` + ordered tickets is the equivalent). Implementation uses
`tdd` for the API and citation logic, `diagnosing-bugs` for any localhost/API
failure, `prototype` only if the Gmail-link UX is unclear (S14.2), and
`code-review` before commit.
