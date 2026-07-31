# Email Knowledge Continuity — Implementation Plan

> Structuring a covered employee's mailbox into a scoped, audited handoff package
> of people, projects, open loops, decisions, and cited evidence, so a successor
> can pick up the role fast without receiving raw mailbox access.

---

## 1. The problem

Onboarding is well tooled; coverage handoff is not. When someone goes on leave,
hands off a role, or delegates a project, the institutional memory in their inbox
— who they worked with, what they owned, the live state of each project —
evaporates. This product turns that unstructured mailbox into a scoped handoff
package: the covered employee reviews what will be revealed, removes what does
not belong, and publishes a cited continuity artifact to the person taking over.

## 2. Two products, one engine

The same pipeline powers two go-to-market motions with very different risk profiles.
Build the engine once; ship the safe version first.

| | Employee-initiated coverage handoff (0-to-1) | Offboarding / admin handoff (v2) |
|---|---|---|
| Trigger | Vacation, leave, role handoff, planned delegation | Departure / termination |
| Employee present? | Yes — initiates, scopes, reviews, publishes | No or unavailable |
| Consent model | Employee-initiated package with scoped recipient access | Admin-side, after the fact |
| Artifact | Audited handoff package | Admin-created transition archive |
| Data freshness | Current | Historical |
| Stakes / scrutiny | Low | High (reads as monitoring) |
| Daily user | Covered employee + coverage recipient | Manager / HR / IT |
| Buyer / approver | Manager / department lead | HR / IT / Legal |

The coverage motion is the wedge: cleaner consent, fresher data, an acute and
recurring pain. D14 tightens this wedge: the MVP should center on the employee
creating an audited handoff package, not a manager or HR user searching an
employee inbox. Offboarding is the higher-value but harder follow-on.

## 3. Core user job

> *"I'm covering for X. What do I need to know, who should I ask, and what evidence supports it?"*

If a stand-in can open a package and understand the right projects, people,
open loops, decisions, and risks — each claim traceable to approved evidence —
the product has done its job.

The package creator's job is equally important:

> *"I'm stepping away. Help me create a scoped handoff that reveals what my
> teammate needs, excludes what they do not need, and leaves an audit trail."*

---

## 4. Architecture — three layers

Retrieval (RAG) is the middle layer. The two hard problems live above and around it.

```
                ┌─────────────────────────────────────────┐
   mailbox ───► │  L0  Ingestion & normalization            │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L1  Enrichment / structuring  (NOT RAG)  │
                │      people · relationships · projects ·  │
                │      roles · events                       │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L2  Retrieval (RAG)                      │
                │      vector + structured filters          │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L3  Constrained synthesis                │
                │      grounded, citation-bound answers     │
                └─────────────────────────────────────────┘
                                  │
                          Surfaces (UI)
```

### Layer 0 — Ingestion & normalization

The foundation. Get the data in cleanly and safely before anything touches a model.

- **Access.** Admin OAuth into a single mailbox (Google Workspace / Microsoft 365).
  Scope it to the one mailbox, time-box the grant, and write an immutable audit log
  of every access. Least-privilege is both a security posture and a sales asset.
- **Thread reconstruction.** Rebuild conversation lineage from `Message-ID`,
  `In-Reply-To`, and `References` headers plus the provider thread ID. Store the
  lineage; do not trust subject lines as project boundaries.
- **Deduplication.** Collapse quoted-reply duplication so the same paragraph is not
  embedded fifty times.
- **Noise filtering.** Drop newsletters, automated notifications, calendar spam, and
  mass `cc-all` blasts *before* the model sees them. Garbage in is the number-one
  quality killer downstream.
- **Sensitivity tagging.** A first-pass classifier flags privileged / legal / HR /
  personal content so later layers can redact or exclude it. (See §7.)

### Layer 1 — Enrichment / structuring

This is the part that is **not** RAG, and where the differentiated value lives. It
runs offline and materializes first-class objects that Layer 2 later queries.

- **Identity resolution.** Map the many addresses, aliases, and display-name variants
  a person uses to one canonical `Person`. Same for `Org` (domain-based + fuzzy).
- **Relationship graph.** Build weighted edges between the mailbox owner and every
  contact. Weight by frequency, recency, directionality (who initiates), and
  thread depth. This graph is what the network map renders.
- **Role inference.** Classify each contact — account executive, lead/prospect,
  internal teammate, manager, vendor, customer — from signals: email domain
  (internal vs external), directory data if available, salutation and signature
  patterns, thread role, and language. Treat as a confidence-scored label, not truth.
- **Project clustering.** The hard one. A project is a fuzzy set of threads, people,
  and time spans — there is no `project_id` in email. Cluster *across* thread
  boundaries using participant overlap, entity/keyword co-occurrence, temporal
  bursts, shared attachments and links, calendar tie-ins, and embedding similarity.
  Output materialized `Project` objects with members, time span, and source threads.
- **Event extraction.** Pull verbs and outcomes at the right epistemic grain:
  *proposed* vs *did* vs *confirmed-outcome*. This is the raw material for honest
  accomplishment summaries and prevents the "volume = impact" trap.

### Layer 2 — Retrieval (RAG)

Standard, well-understood plumbing — but it queries the structure L1 produced.

- **Embeddings** over message/thread chunks for semantic search.
- **Hybrid retrieval.** Vector similarity *plus* structured filters (by project,
  by contact, by role, by time window) from the L1 objects.
- **Provenance.** Every retrieved chunk carries its `message_id` so any downstream
  claim can be traced back to a specific, openable message.

### Layer 3 — Constrained synthesis

Where summaries get generated — and where the grounding discipline lives.

- **Citation-bound generation.** Every assertion must link to one or more
  `message_id`s. No citation, no claim.
- **Epistemic honesty.** Distinguish what is evidenced from what is inferred. Prefer
  *"Coordinated the migration across 12 threads; final outcome not visible in email"*
  over a confident fabrication.
- **Anti-pattern guardrails.** Volume of email is not accomplishment. The synthesis
  layer must resist rendering "sent 200 emails" as "drove to completion."
- **Partial-record framing.** Email is one channel; real work also lives in Slack,
  docs, and meetings. Surface what is evidenced and flag what cannot be seen.

---

## 5. Data model (sketch)

| Object | Key fields |
|---|---|
| `Person` | canonical id, names, org, role label + confidence |
| `Identity` | email address, display name → resolves to `Person` |
| `Org` | name, domain, internal/external flag |
| `Thread` | provider id, root `Message-ID`, participants, time span |
| `Message` | id, thread id, sender, recipients, ts, body ref, sensitivity tags |
| `Project` | id, label, members, time span, source thread ids, confidence |
| `Edge` | person_a, person_b, weight, frequency, recency, direction |
| `Event` | actor, type (proposed/did/outcome), project, source message ids |

## 6. Surfaces (MVP)

These surfaces exist today as standalone workspace views. D14 makes them
components of the handoff-package experience.

1. **Handoff package** — the primary product artifact. The covered employee
   selects date/project/person scope, reviews generated package content, removes
   unnecessary evidence, publishes to a recipient, and leaves an audit trail.
   Shipped and end-to-end validated in S17.2–S17.20, including a deterministic
   package-local ask, new-version re-share/supersede, static HTML export, a
   package-local recipient navigation tree, a creator-only empty-generation
   diagnostic, a refresh-safe workspace mailbox, a deterministic handoff-demo
   seed, and a manual-demo runbook
   (`docs/s17-live-validation.md`, `docs/s17-handoff-manual-demo-runbook.md`).
2. **Network map** — the mailbox owner at center; contacts colored by role; edge
   weight = contact frequency; filterable by project. Click a node for the
   relationship detail and the threads behind it.
3. **Project view** — members, timeline, current state, and the threads that define it.
4. **Cover-for-me query** — the natural-language entry point that answers the §3 job,
   every answer cited.

## 7. Privacy & compliance (design constraints, not afterthoughts)

- The mailbox contains **third-party personal data** (every external contact). Those
  individuals retain rights under GDPR/CCPA regardless of which internal employee
  reads the data.
- Privileged, HR-sensitive, and personal content must be detectable and excludable
  (the L0 sensitivity pass).
- Recipients should receive package-scoped access, not raw mailbox access.
- Employee-initiated review is the normal path. Manager-initiated or emergency
  access is a separate higher-friction path, not the MVP default.
- The product must not produce productivity scores, performance summaries,
  responsiveness metrics, effort inference, ranking, compensation, promotion, or
  termination support.
- The offboarding config (admin-side, no employee consent) is the pattern that draws
  the most scrutiny and reads as employee monitoring — hard limits in the EU.
- Keep an audit log of access; support retention limits and data-subject deletion.
- *Not legal advice — run a real privacy review before selling into a regulated buyer.*

## 8. MVP cut & sequencing

**Implemented (S0–S4, complete):**
- Single-mailbox ingest (`FixtureProvider` + `GmailProvider`): thread reconstruction, address
  normalization, deduplication, noise filtering, sensitivity tagging.
- Identity resolution, relationship graph, role inference (rules-based v1).
- PostgreSQL 16 + pgvector persistence (Alembic migrations 0001–0005, SQLAlchemy mappers).
- Project clustering: Leiden community detection, soft membership, c-TF-IDF labeling, incremental
  ID carry-over. Eval: extended-BCubed F1 ≥ 0.75.
- Event extraction: per-thread, LLM-backed via injectable `extract_fn`, proposed/did/outcome
  typed, citation-enforced. (D10 — L1 exception to the L3-only LLM rule.)
- L3 synthesis: project "What's been done" + contact "Ask about this contact" — grounded,
  cited, Pydantic-validated against allowed message_id_headers.
- Surfaces: network map (S2), project view with activity panel (S3–S4), synthesis buttons (S4).
- 138 tests passing. Frontend build clean. `python scripts/dev_seed.py` seeds all layers.

**Implemented (S5, complete):**
- **Cover-for-me query** (implementation-plan §6.3, D11). `POST /api/cover-for-me/{mailbox_id}`.
  Word-boundary entity detection against Person names and Project labels already in Postgres.
  Routes to `synthesize_project` / `synthesize_contact` (reusing S4); citation allow-list
  enforced (invalid headers filtered post-model). "Insufficient structured evidence" fallback
  when no entity matches — never bluffs. Third "Cover for Me" tab in the frontend. 148 tests.

**Implemented (S7, complete and live-validated):**
- **L2 retrieval** per D12 (see `docs/decisions.md`, `docs/s7-implementation-plan.md`).
- Implemented through S7.11: migration 0006, `message_embedding` table and HNSW index,
  `subject_clean_tsv` FTS column, embed client seam (`FakeEmbedClient` + `VoyageEmbedClient`),
  idempotent backfill script, vector search, FTS search, hybrid merge, retrieval contracts,
  reranker boundary hardening, retrieval eval (7 hard gates pass on fixture), and
  cover-for-me L2 upgrade (L1+L2 hybrid routing, L2-only path, citation allow-list enforced).
- Optional S7.12 hosted Voyage reranker remains feature-flagged off (not required for MVP).

**Implemented (S8–S10, complete):**
- **S8** Real-mailbox demo readiness: real-mailbox backfill validation, API/UI evidence
  transparency (`supporting_evidence`), operational preflight (`scripts/preflight.py`,
  `GET /api/preflight`), graceful failure UX (`retrieval_status` enum), smoke eval.
- **S9** Project-clustering materialization on live mailboxes (`scripts/materialize_projects.py`):
  persists Project / ThreadProjectAssignment / ProjectMember from stored `voyage-4` embeddings;
  `--confirm` is embedding-gated; whole-thread sensitivity exclusion; idempotent persist.
- **S10** Local runtime reliability: `VoyageEmbedClient` switched to the Voyage REST API over
  `httpx` (no `voyageai` SDK / `langchain` / `uuid_utils` native chain — D12b S10 note);
  preflight embed-client construction probe; blessed Windows launch scripts; frontend request
  timeout + typed errors (no infinite "Loading…"); Vite `strictPort`; `GET /api/health`.

**Implemented (S11–S14, complete):**
- **S11** Frontend demo polish: clickable citation chips open an evidence drawer
  (subject/date/`message_id_header`/snippet/retrieval source, from
  `supporting_evidence` only); repeated citations deduped; distinct titled error
  states; display-only project-label cleanup (`utils/projectLabels.ts`);
  demo-readiness strip.
- **S12** Product shell + landing: dependency-free client router (`src/router.tsx`)
  over the History API; workspace shell holding mailbox state across navigation;
  Workspace Overview as the default `/app` entry (counts, readiness, suggested
  questions, top projects); status screen; marketing landing page; Cover-for-me
  onboarding (suggested-question chips); project search filter. Frontend-only.
- **S13** Relationship Map: new `services/relationships/` derives a graph-backed,
  tree-renderable relationship map *live* from L1 tables (owner/project/org/graph
  modes); `GET /api/relationship-map/{mailbox_id}`; whole-thread sensitivity +
  noise exclusion; edge width = evidence volume, not importance; Network Map
  preserved.
- **S14** Evidence & source navigation: safe `GET /api/source-message/{mailbox_id}`
  keyed on `message_id_header` (mailbox-boundary + malformed-UUID 404s); a shared
  whole-thread sensitivity gate applied to both that endpoint and Cover-for-me
  `supporting_evidence`; richer citation drawer (subject/sender/date/snippet),
  copy Message-ID, best-effort Gmail `rfc822msgid` "Search in Gmail"; Relationship
  Map structural (project/thread/domain) edges show provenance notes instead of
  fabricated message IDs.

**Implemented (S15, complete):**
- **S15.1** Verification hardening: fixed S9 materialization test contamination
  by isolating DB fixture state, preserving dry-run invariants, and preventing
  aborted transactions from cascading into unrelated tests.
- **S15.2** Verification matrix: `docs/s15-verification-matrix.md` defines the
  four green tiers (minimum local, DB-gated, demo-mailbox, live-integration),
  the manual UI checklist, and the cleanup policy for `ekc_test` vs `ekc_dev`.

**Implemented / planned (S16+, current direction):**
- **S16.0** Date-range ingest: customizable Gmail date-window preview/ingest for
  large mailboxes and scoped snapshots. Date-windowed runs bypass stored sync
  tokens and do not save new ones; replace-snapshot requires an explicit date
  bound and confirmation.
- **S16.1+** Canonical demo readiness (broader purpose-built demo-story fixture):
  **superseded in practice** for the current demo path by the shipped S17
  handoff-demo work — the seeded `handoff-demo` mailbox
  (`scripts/seed_handoff_demo.py`) drives Handoff package generation and `puluo`
  drives Cover-for-me / Relationship Map / Network Map
  (`docs/s17-handoff-manual-demo-runbook.md`). Any remaining broader
  demo-story/landing work is optional, not a blocker (D13).
- **S17.2–S17.20 ✓** Audited handoff package MVP: employee-initiated package
  creation, scope review, package publish, one-time recipient link, read-only
  recipient view, deterministic LLM-free package-local ask (S17.9), revoke,
  new-version re-share / supersede (S17.10), static HTML export (S17.11),
  three-part recipient coverage workspace (S17.12 nav tree → S17.17 coverage-area
  grouping → S17.18 rail/brief/people layout → S17.19 top-level Ask tab +
  claim-attached evidence), creator-only empty-generation
  diagnostic + Start-over fix (S17.13), refresh-safe workspace mailbox +
  deterministic handoff-demo seed (S17.14), manual-demo runbook + `--verify`
  seed mode (S17.15), Handoff-demo network-map graph tolerance (S17.16),
  creator Handoff workspace layout — full-width package header + sticky action
  rail so Publish is reachable without scrolling, + Overview-readiness note
  (S17.20), + audit lifecycle — shipped and e2e-validated
  (D14; `docs/s17-handoff-package-mvp-plan.md`, `docs/s17-live-validation.md`,
  `docs/s17-handoff-manual-demo-runbook.md`).
  Deferred beyond S17: optional LLM synthesis for the package ask, PDF/docx/zip
  export, manager approval, multi-recipient, rich snapshotted relationship/
  project/owner trees (S17.17 ships a package-local topic brief, not a graph), stronger
  production auth.

**Implemented (S22–S27, S29–S31; S34 backend):**
- **S22 ✓** Auth + tenant boundary (implements S19): `Tenant`/`AppUser`/
  `TenantMembership` + `Mailbox.tenant_id`/`owner_user_id` (migration 0010),
  fail-closed `AUTH_MODE`, and `require_owner_mailbox`/`require_owner_package`
  on every creator/mailbox route (dev preserves the localhost flow; production
  fails closed). Recipient routes stay package-local snapshot only.
- **S23 ✓** Gmail OAuth + token-vault minimal slice (implements S20): the
  state+PKCE start/callback/disconnect/status flow (`services/oauth/`,
  `services/api/routers/oauth_gmail.py`, migration 0011), a `mailbox_provider_account`
  storing only `vault_ref` + safe metadata (never raw tokens), mismatch/fail-closed
  rules, and a vault-backed production credential resolver. The shipped
  `DevTokenVault` is **dev/test-only**; a production KMS/secrets-manager vault, real
  Google app verification, and moving ingest onto the resolver are deferred.
- **S24 ✓** Background job infrastructure (implements S21 §2/§3/§8/§11):
  `services/jobs/` + `services/api/routers/jobs.py` + migration 0012 — a
  tenant-scoped `job` table with the six states (queued/running/succeeded/failed/
  canceled/partially_succeeded), enqueue/status/list/cancel APIs (S22
  owner/tenant-guarded), a Postgres `FOR UPDATE SKIP LOCKED` worker claim with
  lease/heartbeat + expired-lease reclaim, idempotency dedupe, safe-metadata
  sanitization, and a harmless `noop` job for validation. Recipients never touch jobs.
- **S25 ✓** Gmail date-range ingest moved onto the S24 job runner
  (`gmail_ingest_window` handler, `scripts/run_worker.py`): the confirm endpoint
  validates + verifies the account request-time (S16.0 confirm / `replace_snapshot`
  / account-guard safeguards preserved), then enqueues an idempotent ingest job; a
  worker runs the fetch/normalize/persist. Preview stays synchronous. Only
  date-range ingest is moved — enrichment / embedding backfill / project
  materialization stay manual scripts (S26).
- **S26 ✓** Enrichment / event extraction / embedding backfill / project
  materialization moved onto the S24 runner (`services/jobs/handlers/pipeline.py` +
  `services/api/routers/pipeline_jobs.py`): the job types `l1_enrichment`,
  `event_extraction`, `embedding_backfill` (cost-gated — a dry-run estimate unless
  the operator confirms; no live Voyage call otherwise), and
  `project_materialization` (S9 embeddings precheck), each wrapping the existing
  core logic so the CLI scripts (`embed_backfill.py`, `materialize_projects.py`)
  still work. Owner/tenant-guarded enqueue endpoints + a minimal Status pipeline
  panel + a worker (`scripts/run_worker.py`). Recipients never touch jobs.

- **S27 ✓** Hosted deployment readiness (`services/hosted_readiness.py`,
  `docs/s27-hosted-deploy-readiness-plan.md`, `docs/s27-hosted-deploy-runbook.md`):
  a **safety-gated** slice — an environment-validation module + **fail-closed startup
  guardrails** — not a broad hosted migration. The guards (API lifespan in
  `services/api/main.py`, worker in `scripts/run_worker.py`) are a **no-op unless
  `EKC_DEPLOY_ENV=production`** and otherwise refuse to boot a hosted process with
  `AUTH_MODE=dev` / the dev token vault (`EKC_ALLOW_DEV_VAULT`), a missing/dev-default
  `DATABASE_URL`, an un-migrated DB, missing OAuth client config or a localhost/non-https
  redirect URI, missing OAuth-callback log redaction, wildcard CORS, an unobservable
  job queue, a raw mailbox-id production bypass, or a recipient-router snapshot-only
  regression. Ships a `scripts/preflight.py --hosted` command, safe `/readyz` +
  `/api/readyz` endpoints (bare status only — no leak), and a **migration-free**
  worker-readiness signal off the existing `job` table lease/heartbeat. Reuses the
  shipped `services/preflight.py` check framework and the `AUTH_MODE` / vault /
  redaction seams. **No migration** (head stays `0012_job_infra`). Tests in
  `tests/test_s27_hosted_readiness.py`. Non-goals (unchanged): infra provisioning,
  admin/audit UI, telemetry vendor, M365, a production IdP beyond the fail-closed
  guard, recipient accounts, manager approval, multi-recipient, rich recipient
  trees, retention enforcement, external security review.
- **S29 ✓** Read-only Admin / Audit Viewer (`services/admin/` read-service +
  `services/api/routers/admin.py`, `tests/test_s29_admin_viewer.py`) — the read set of
  the S28 spec. `GET /api/admin/{overview,packages,packages/{id},packages/{id}/audit,
  provider-accounts,jobs,jobs/{id},audit,exclusions/summary,readiness}`, guarded by new
  `require_admin`/`require_admin_or_reviewer` deps on `Principal.roles`; tenant-scoped
  (cross-tenant → 404, unauthenticated-in-production → 401, wrong role → 403). Allow-list
  DTOs (`services/admin/contracts.py`) expose **safe metadata only** — package lifecycle
  (with `reason_category`, the safe enum), provider-connection metadata, safe job fields,
  audit events (whitelisted metadata projection), and **aggregate exclusion counts only**.
  Security reviewers get a masked recipient email and no provider email/scopes. **No
  leak** of evidence bodies, claim text, scope detail, source headers, raw `Job.params`/
  `error_message`/`worker_id`, `sync_token`, `vault_ref`, tokens, or DB URLs (sentinel
  test asserts absence). Sensitive package-detail reads write an `admin.package.viewed`
  audit event. **No mutations** (revoke/disconnect are S30), **no migration** (head stays
  `0012_job_infra`), recipient routes untouched and package-local snapshot-only.
- **S30 ✓** Audited admin actions (`services/api/routers/admin.py`,
  `services/handoff/lifecycle.py`, `services/oauth/flow.py::disconnect_account`,
  `tests/test_s30_admin_actions.py`) — exactly two tenant-admin-only, reason-gated
  mutations. `POST /api/admin/packages/{id}/revoke` reuses the shared S17 revoke
  lifecycle (`revoke_package`, now called by both the creator route and admin): marks
  the package revoked, revokes the recipient grant, kills live sessions, and writes
  `package.revoked_by_admin` with `{reason, admin_user_id, prior_status, revoked_at}`.
  `POST /api/admin/provider-accounts/{id}/disconnect` reuses the S23 vault path
  (`disconnect_account`): provider-side revoke + vault purge, mark `disconnected`, drop
  `vault_ref`, and writes an `AuditLog` `provider_account_disconnected_by_admin` row
  with the reason. Both: tenant-admin only (reviewer/creator → 403), cross-tenant/
  malformed id → 404, blank reason → 422, idempotent. **No token/`vault_ref`/OAuth
  code/provider response returned or logged; no migration** (head `0012_job_infra`);
  recipient snapshot-only invariant untouched. Non-goals hold (no edit/generate/publish/
  prune, no recipient impersonation, no silent reconnect, no connect-on-behalf, no
  content access).

- **S31 ✓** Admin / Audit Viewer **frontend** (`frontend/src/components/admin/AdminConsole.tsx`,
  `AdminPackages.tsx`, `AdminProviders.tsx`, `ui.tsx`; admin client + DTOs appended to
  `frontend/src/api/client.ts` + `types.ts`; a **dev-only** `/app/admin` tab in
  `workspace/Workspace.tsx`, tenant-scoped so it renders without a loaded mailbox). A
  compact governance console over the S29/S30 `/api/admin/*` endpoints — overview +
  readiness, package lifecycle list/detail/audit, provider-account status, jobs
  list/detail, audit log, exclusion summary — plus the two audited actions (revoke
  package, disconnect provider) behind a mandatory typed-reason `ConfirmReasonModal`
  that refreshes the affected state and shows success/error. **Frontend only — no
  backend / schema / migration / dependency change** (verified: only `frontend/**` +
  docs changed; head stays `0012_job_infra`). Renders only the safe metadata the API
  returns (`SafeMeta` collapses any non-scalar; no evidence bodies, claim text, tokens,
  `vault_ref`, `sync_token`, or raw job params/errors), respects security-reviewer
  masking, and never opens the recipient view or impersonates. The Admin nav **and
  route** are **dev-gated** (`import.meta.env.DEV` gates both the nav link and the
  `/app/admin` render, so a production build shows the "sign-in required" empty state
  instead of the console on a direct URL) until production role-gated sign-in exists
  (S22).
  Manual smoke (dev, `AUTH_MODE=dev`, backend + worker running): (1) open `/app/admin`;
  (2) view Overview counts + readiness; (3) open Packages, select one, read detail +
  audit; (4) open Providers; (5) open Jobs / Audit log / Exclusions; (6) revoke a
  published package with a reason → status flips to `revoked` and the recipient link
  returns the neutral "unavailable"; (7) disconnect a connected provider account with a
  reason → status flips to `disconnected`; (8) confirm no body/subject/snippet, token,
  `vault_ref`, or DB URL appears anywhere.
- **S34 ⌛ (backend implemented; creator/recipient UI pending)** Return handoff /
  coverage delta (implements `docs/s33-return-handoff-coverage-delta-plan.md` / D15).
  A reciprocal package (`package_type=return_delta`, new lineage) created from the
  **coverer's** mailbox back to the original employee — never a `new-version`.
  Migration **0013** (`handoff_package.package_type` coverage|return_delta, safe
  `coverage_return` reason, `handoff_return_context` provenance table; head
  `0013_return_handoff`, no ekc_schemas change). Auto scope-seed
  (`services/handoff/return_scope.py`): original coverage-area **labels → coverer-side
  project ids**, else domain/person snapshot hints → coverer person ids; original
  project UUIDs are provenance only, never cross-mailbox filters (§12). Endpoints
  `POST /api/handoff/{original}/return-draft` + `GET .../return-context`
  (`services/handoff/return_handoff.py`): coverer owns the source mailbox + (prod) is
  the original recipient; window = original `published_at` → today; publish defaults
  the recipient to the original creator; generation reuses `generate_candidate`
  unchanged (sensitivity/noise/exclusion gates, no-citation-no-claim). S29 admin DTOs
  gain `package_type` + return→original linkage (metadata only). Tests
  `tests/test_s34_return_handoff.py` (5); full DB-gated suite 829 passed / 2 skipped.
  **Pending S34 follow-up:** creator UI setup/review, recipient return framing, and
  the two-mailbox demo-seed script. Recipient routes stay snapshot-only; the original
  outbound package is never revoked/superseded/mutated by a return publish.

**Planned — docs/spec-only, not implemented (S28 spec fully implemented by S29+S30):**
- **S28 — Admin / Audit Viewer + Operations Console** (`docs/s28-admin-audit-ops-plan.md`):
  a governance + operational visibility surface over **safe metadata only** — package
  lifecycle, provider-account connection metadata, job status, audit trails, aggregate
  exclusion counts, and readiness/ops health — plus exactly two audited admin actions
  (**revoke package**, **disconnect provider account**, each requiring a mandatory
  reason and writing an audit event). Roles reuse the shipped `TenantMembership` enum
  (`admin`, `security_reviewer`) plus an `operator` concept (operator kept infra-level
  per S19 §6; open question §15.1). Overriding invariant: **no admin route is a content
  backdoor** — no mailbox bodies/subjects/snippets, `HandoffEvidence` bodies,
  `HandoffClaim.text`, scope detail, excluded content, Gmail/source links, OAuth
  tokens/codes/`state`/`code_verifier`/`vault_ref`, provider/LLM responses, raw
  `Job.params`/`error_message`, or tracebacks; admin package access is metadata-only;
  recipient routes stay package-local snapshot-only and unchanged. Proposed
  `/api/admin/*` GET endpoints + allow-list DTOs + `require_admin`/`require_security_reviewer`
  dependencies. **No migration** for the read-only viewer (reads existing
  `HandoffPackage`/`MailboxProviderAccount`/`Job`/`AuditLog`/`HandoffAuditEvent`/
  `HandoffExclusion` rows); a product `operator` role would be one later
  `ck_tenant_membership_role` widening. Implementation sequence: **S29 read-only viewer
  → S30 admin actions**. Non-goals: no content backdoor, no recipient impersonation, no
  admin edit/generate/publish/prune, no token viewing/silent reconnect, no legal-hold
  content access, no M365/new provider/new auth provider, no retention-enforcement engine.

**Planned — docs/spec-only, not implemented (S33):**
- **S33 — Return Handoff / Coverage Delta**
  (`docs/s33-return-handoff-coverage-delta-plan.md`, D15): designs the reciprocal
  handoff after a coverage period ends. The coverer creates a new package from
  their own mailbox back to the original employee; the return draft is automatically
  seeded from the original package's projects / coverage areas where possible; and
  the returning employee receives only the published return package snapshot, never
  live access to the coverer's mailbox. The spec proposes a future `package_type =
  return_delta` plus `handoff_return_context`, return-scope seeding, a return draft
  endpoint, return-specific generation/copy, and a two-mailbox demo seed.

**Specs shipped as docs/spec-only plans — not implemented in code (S18–S21):**
_Authoritative implementation sequence: S21 §14 — S22 auth → S23 OAuth/vault → S24
job infra → S25 ingest→jobs → S26 enrichment→jobs → S27 hosted deploy (admin/audit
+ security hardening after). This supersedes the earlier S18 §11 / S19 §11 / S20
§12 ordering._
- **S18 — Hosted product readiness / web-app deployment plan** (planning sprint,
  no code): what must be true to move from the localhost/demo MVP to a deployable
  hosted web app — product shape (web, not desktop), production roles + access
  model, the creator/recipient/admin auth boundary, Gmail-first OAuth + token
  vault, hosted architecture (static React + FastAPI + managed Postgres/pgvector +
  background worker + secrets manager + observability), background-job states,
  data-tier boundaries (recipient reads package-local snapshots only), privacy/
  compliance posture, a production-readiness gap checklist, a phased launch path,
  and an S19+ sprint map (its original ordering is superseded by S21 §14 — see
  the section banner above). See
  `docs/s18-hosted-product-readiness-plan.md`. Implementation deferred to S19+.
- **S19 — Auth + tenant boundary** (docs/spec-only, no code): the production
  identity/tenant/authorization model S20+ implements — tenant/workspace model,
  user roles, mailbox ownership + tenant binding, creator/recipient/admin
  permissions, the dev-only `mailbox_id` mode + how it is disabled in production
  (fail-closed `AUTH_MODE`), the authorization check required on every creator
  endpoint, and audit events for auth-sensitive actions. Shipped S17 behavior is
  unchanged (dev mode preserves the localhost flow). OAuth/token-vault stays
  deferred to S20. See `docs/s19-auth-tenant-boundary-plan.md`. Not implemented.
- **S20 — OAuth + token vault** (docs/spec-only, no code): how a production tenant
  user safely connects a Gmail mailbox — the authorization-code OAuth flow
  (state/nonce, account verification, owner/tenant binding per S19), a token-vault
  boundary keeping refresh tokens out of the app DB and logs (app DB holds only a
  `vault_ref` + safe provider metadata; D6), the proposed `mailbox_provider_account`
  object (service DB only, not `ekc_schemas`), least-privilege Gmail scopes
  (read-only ingest + identity; no write/send), provider-account-mismatch
  fail-closed handling, token lifecycle (connect/refresh/revoke/offboarding),
  auth-sensitive OAuth audit events, and a focused threat model. Gmail only; M365
  stays the D2 stub (not implemented). See `docs/s20-oauth-token-vault-plan.md`.
  Not implemented.
- **S21 — Background job orchestration** (docs/spec-only, no code): the production
  job system for long/retryable work outside web requests (Gmail ingest, L1
  enrichment, event extraction, embedding backfill, project materialization,
  cleanup/retention; heavy handoff generation + PDF/DOCX/ZIP export later) — a
  tenant-scoped `job` model + states (queued/running/succeeded/failed/canceled/
  partially_succeeded), per-type authz/idempotency/retry/progress, S16.0 ingest
  integration (confirm starts a job; `replace_snapshot` staged transactional
  swap), vault-backed token resolution in the worker (no tokens in payloads/logs),
  S19 authz re-checked in the worker, safe audit events, a Postgres-backed
  queue + worker (leases/heartbeat/stuck-job recovery) default, rate/cost controls,
  and a concrete S22+ implementation map. See
  `docs/s21-background-job-orchestration-plan.md`. Not implemented.

**Deferred beyond S17:**
- **Thread-context neighbor expansion** (per spec).
- **Chunk-level splitting, attachment embeddings** — message-level only currently.
- **Permissioned sensitive-message embedding override** — requires a permission model first (Q3).
- **Full production secrets manager, DPA/customer deployment gates** — before any non-demo mailbox.
- **Multi-mailbox, admin-side offboarding motion, cross-channel ingestion** — v2 product scope.
- **M365 provider** — stub now; drops in without pipeline changes (D2).
- **Object store for raw MIME** — `raw_uri = None` until production deployment (D6).
- **Background job queue (Postgres-backed first per S21; external broker like Redis later), full OAuth/secrets manager, OTel** — needed before real customer mailboxes;
  not needed for a controlled demo against a test inbox.

**Plug-in point (resolved by D12 / S7):** Layer 2 retrieval is now implemented
locally under `services/retrieval/` (Voyage `voyage-4` embeddings, pgvector HNSW,
Postgres FTS, hybrid merge) — see `docs/decisions.md` (D12) and
`docs/s7-implementation-plan.md`. The earlier TODO to confirm which signals the
current RAG pipeline extracts and where it slots in is resolved; this note is
kept only as historical context.

## 9. Open questions

- **Directory access (org chart)** alongside the mailbox? It would sharpen role inference
  dramatically beyond the current keyword-based v1. The rules-based classifier correctly handles
  the fixture but will struggle with ambiguous cases at real-mailbox scale. Resolved if yes:
  add directory lookup as signal priority 2 in spec 01 §3/§5.
- **Projects displayed when email gives no canonical label?** Addressed in spec 03 §12 (c-TF-IDF
  keyphrases + top contact + month fallback). Still to validate against real-mailbox output.
- **Confidence thresholds** for hiding inferred facts: `role_confidence_threshold = 0.4` (default
  in `EnrichParams`, spec 04 `mailbox.display_threshold`). UI respects this for role labels.
  Fine-tuning per-tenant is deferred.
- **Retention** after a coverage period ends: `retention_days` param in `IngestParams` (wired in
  params, not yet enforced in a scheduled job).
- **Handoff package scope:** resolve whether manager approval is mandatory in
  the first implementation, whether packages support one recipient or many, the
  default expiration period, whether post-publish edits version the package, and
  whether excluded-counts are shown as aggregate package metadata.
- **Embedding model + dimension:** resolved by D12b. `voyage-4`, 1024 dimensions,
  HNSW cosine. Migration 0006 adds `message_embedding` table and combined FTS index.
- **Data-subject deletion semantics** for third-party content inside shared threads — needs a
  product + legal decision (spec 04 §11, §13).
