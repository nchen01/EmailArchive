# S18 — Hosted Product Readiness / Web App Deployment Plan

> **Docs-only planning sprint.** No backend/frontend/schema/migration changes.
> This plan answers one question: *what needs to be true before the audited
> Handoff Package MVP becomes a real hosted web application instead of a
> localhost demo?* It maps the work; S19+ implement it.

**Status:** S18 planned (this document). Nothing here is implemented.

---

## 0. Baseline — what is already shipped (S17.2–S17.20)

So the "gaps" below are read against a concrete starting point, this is what
exists today and works end to end in local/demo mode:

- **Creator flow:** create draft → set scope (date range / projects / people /
  threads) → generate candidate → review + prune claims/evidence → **publish** to
  one recipient. Publishing freezes the package.
- **One-time recipient link:** publish mints a single capability code carried in
  a URL fragment; it is consumed on first use and exchanged for a **short-lived
  session token**. The raw code is never persisted or logged and is not
  recoverable from the server after publish (verified S17.20 smoke).
- **Recipient view** (`/handoff/recipient`): read-only, package-local coverage
  workspace (area rail · brief · people · package-local **Ask**, deterministic and
  LLM-free). Reads **only** package snapshot rows.
- **Lifecycle:** revoke; new-version re-share / supersede (immutable published
  packages; edits fork a new version); static self-contained **HTML export**;
  audit events across the lifecycle.
- **Pipeline underneath:** L0 Gmail ingest (incl. S16.0 date-range windows) →
  L1 enrichment (people/projects/events, Anthropic event extraction) → L2
  retrieval (Voyage `voyage-4`, pgvector HNSW, Postgres FTS) → package snapshot.
- **Runtime today:** single FastAPI process + Vite/React dev server + local
  Postgres 16 + pgvector (Docker). Migrations at head `0009_recipient_code_consumed`.
  Secrets in a gitignored `.env`. Background tasks are **manual scripts**
  (`scripts/embed_backfill.py`, `scripts/materialize_projects.py`, ingest, seed).
  Raw MIME is not stored (`raw_uri = None`, D6).

**What is NOT true today (the S18 subject):** there is no real creator
authentication (the creator types a `mailbox_id`), no tenant/workspace model, no
OAuth token vault, no job queue, no deployment pipeline, no monitoring, no admin
audit viewer, no retention enforcement. Those are the gaps this plan sequences.

---

## 1. Product shape — a hosted web app, not a desktop app

**Decision to confirm:** the product becomes a **hosted, browser-based web
application**. Rationale, each of these fits a hosted web app and fights a
desktop app:

- **OAuth to email providers** (Gmail, later M365) needs a registered redirect
  URI, a confidential client secret, and server-side token custody, none of
  which belong on an end-user desktop machine.
- **Browser recipient links** are already the delivery mechanism: a recipient
  opens a URL on whatever device they have. That only works against a hosted
  origin with TLS and a stable domain.
- **Package sharing, audit, and admin/security controls** are inherently
  multi-user, server-authoritative concerns.
- **Cross-device access** (creator on laptop, recipient on phone) requires a
  shared backend, not per-machine state.

**Local/dev stays first-class.** The current localhost mode (dev `mailbox_id`
entry, local Postgres, manual scripts) remains supported for engineering and
demos. Production hosting is *additive*; the dev path is never removed. The
`mailbox_id`-entry creator mode becomes **dev-only** (see §3).

---

## 2. User roles and access model

Future production roles (today only an implicit "creator via mailbox_id" and
"recipient via capability link" exist):

| Role | Who | Purpose |
|---|---|---|
| **Covered employee / package creator** | The mailbox owner stepping away | Ingest own mailbox, scope, review, publish, revoke |
| **Recipient / coverage employee** | The person covering | Open a published package, read it, ask package-local questions, export (if allowed) |
| **Manager / approver** *(deferred unless a segment requires it)* | Creator's manager | Optionally approve a package before publish |
| **Workspace / admin / security reviewer** | IT / security / workspace admin | Govern policy, inspect audit logs, manage connections — **without** silent mailbox browsing |
| **System operator** | Us / the deploying team | Run migrations, operate workers, manage secrets, respond to incidents |

**Action → role capability matrix** (production target; "—" = not allowed,
"cfg" = policy-configurable):

| Action | Creator | Recipient | Manager | Admin/Security | Operator |
|---|---|---|---|---|---|
| Ingest mailbox (own) | ✅ | — | — | — | — |
| Scope mailbox / date range | ✅ | — | — | — | — |
| Review generated handoff | ✅ | — | cfg | — | — |
| Publish package | ✅ (cfg: after approval) | — | cfg approve | — | — |
| Revoke package | ✅ | — | cfg | ✅ (governance) | — |
| View package | creator preview | ✅ (own link) | cfg | — | — |
| Ask package-local questions | ✅ preview | ✅ | cfg | — | — |
| Export package | cfg | cfg | cfg | — | — |
| Inspect audit logs | own packages | — | cfg (their reports) | ✅ (workspace) | ✅ (operational) |

Two invariants encoded above: **Admin/Security can read audit metadata and
govern, but cannot open/browse a mailbox or read package content as if they were
the recipient.** **Manager involvement is off by default** and only turns on for
customer segments that require approval (§ open question 9).

---

## 3. Auth boundary (production)

The current model and what must harden:

- **Creator — today:** types a `mailbox_id` string; anyone who knows the id can
  drive that mailbox's handoff surfaces. **Production requirement:** real **owner
  authentication** (SSO/OIDC session), and the mailbox must be **bound to the
  authenticated owner** — a creator may only act on mailboxes they own/connected.
  The `mailbox_id`-entry path is retained **dev-only**, gated behind an
  environment flag, never enabled in a hosted deployment.
- **Recipient — today:** one-time capability code (URL fragment) → consumed →
  short-lived session token; package-local reads only. This is already the right
  shape. **Harden before production:** enforce TLS-only; keep the code out of
  logs/referrers (fragment, not query — already true, keep it); short session TTL
  with server-side revocation on package revoke; rate-limit code redemption and
  session creation; bind a session to a single package; decide whether recipients
  optionally upgrade to a company account (§ open question 2).
- **Admin/workspace — target:** authenticated workspace role that grants
  **governance** (policy, audit viewer, connection management) and explicitly
  **not** silent mailbox browsing or recipient-equivalent content access.
- **Local/demo mailbox-id mode:** **dev-only**, behind a flag; must be impossible
  to reach in a hosted build.

**Non-negotiable:** authentication changes never weaken the recipient
package-local read boundary (§7) — a stronger creator/admin identity must not
become a new path to live mailbox rows.

---

## 4. OAuth / email provider model

**Order:** Gmail first (already the implemented provider), Microsoft 365 later
(currently stubbed, D2 says it drops in without pipeline changes).

Plan to specify in S20:

- **OAuth consent:** confidential web-app client; registered redirect URI on the
  hosted origin; incremental consent; clear per-connection consent copy tied to
  the "employee-initiated handoff" stance (§8).
- **Least-privilege scopes:** read-only mail scope sufficient for ingest; no
  send, no modify beyond what a feature needs (the existing sensitive-label
  writes, if kept, are a separate, explicit scope decision). Enumerate the exact
  minimal Gmail scopes in S20.
- **Token storage outside the app DB if possible:** access/refresh tokens go in a
  **secrets manager / token vault** (§ open question 6), not plaintext in
  Postgres. If an interim DB store is unavoidable, tokens must be envelope-
  encrypted with a KMS-held key, never logged.
- **Refresh / revocation:** background refresh before expiry; handle provider-
  side revocation (user disconnects, admin revokes) by marking the connection
  dead and stopping jobs; a first-class "disconnect" that deletes stored tokens.
- **Provider account mismatch:** the connected Google account must match the
  mailbox owner identity; detect and refuse a mismatch (e.g., SSO identity ≠
  connected mailbox) rather than silently ingesting the wrong inbox.
- **Date-range ingest UX (from S16.0):** the hosted connect flow reuses the
  date-window preview/ingest — the owner picks a window, previews volume, then
  ingests; windowed runs bypass stored sync tokens (already implemented).
- **Large-mailbox constraints:** ingestion must be a background job (§6) with
  progress, resumability, backpressure, and per-provider rate-limit handling;
  never a synchronous request. Define page-size, quota, and retry policy in S20.

---

## 5. Deployment architecture (hosted target)

Technology-neutral shape, with a recommended default named where it is obvious.
Nothing here is built in S18.

```
                    ┌────────────────────────┐
   Browser  ──────► │  Static React frontend  │  (CDN / static host)
   (creator,        └───────────┬────────────┘
    recipient,                  │ HTTPS (JSON)
    admin)          ┌───────────▼────────────┐
                    │  FastAPI backend (API)  │  (stateless, horizontally scalable)
                    └───┬───────────┬────────┘
                        │           │ enqueue
             ┌──────────▼───┐   ┌───▼──────────────┐
             │ Postgres +    │   │  Job queue       │  (Redis / managed broker)
             │ pgvector      │   └───┬──────────────┘
             │ (managed DB)  │       │ consume
             └──────▲────────┘   ┌───▼──────────────┐
                    │            │ Background worker │  (ingest/enrich/embed/
                    │            │  pool             │   materialize/generate)
        ┌───────────┴──┐        └───┬───────────────┘
        │ Object/blob   │◄──────────┘ raw MIME later (D6)
        │ store (later) │
        └───────────────┘
   Cross-cutting: Secrets manager (OAuth creds, API keys) · Logs/metrics/traces ·
   Error reporting · Backups.
```

Components:

- **Frontend:** the existing React build served as a static app behind a CDN.
- **Backend:** FastAPI, kept **stateless** so it scales horizontally; no
  long-running work in request handlers (§6).
- **Database:** managed **Postgres + pgvector** (already the local shape — the
  smallest jump). Managed migrations + a runbook (§9).
- **Background worker:** a separate process pool consuming a queue; runs every
  heavy task in §6.
- **Object/blob store:** deferred until raw MIME is stored (D6); reserved here so
  the boundary exists in the architecture from day one.
- **Secrets manager:** OAuth client secret, provider tokens, `VOYAGE_API_KEY`,
  `ANTHROPIC_API_KEY`, DB creds — out of `.env`/DB, injected at runtime.
- **Observability:** structured logs, metrics, distributed traces, and error
  reporting (recommended default: OpenTelemetry-compatible export; specific
  vendor deferred).

**Recommended defaults where obvious:** managed Postgres+pgvector; Redis (or a
managed equivalent) as the broker; a cloud secrets manager; OTel for
telemetry. These are defaults to confirm in S22, not commitments made here.

---

## 6. Background jobs

**Rule:** none of these run inside a web request. Each is enqueued and executed
by the worker pool, with observable state.

Tasks that must be background jobs:

- **Gmail ingest** (per mailbox, per date window)
- **L1 enrichment** (people/relationships/projects/roles)
- **Event extraction** (Anthropic-backed)
- **Embedding backfill** (Voyage `voyage-4`; today `scripts/embed_backfill.py`)
- **Project materialization** (today `scripts/materialize_projects.py`)
- **Handoff package generation** — synchronous today; move to a job **if/when it
  becomes heavy** (large scope, LLM synthesis) so the creator UI stays responsive.
- **Export generation** — HTML is cheap and can stay inline; **PDF/DOCX/ZIP** (if
  added later) must be a background job producing an artifact in the blob store.

**Canonical job states:**

`queued → running → succeeded`
`running → failed` (retryable/terminal per policy)
`running → canceled` (user/admin/operator requested)
`running → partially_succeeded` (e.g., ingest completed N of M pages, or some
messages failed enrichment) — a first-class state so the UI can be honest about
"most of your mailbox is ready" rather than all-or-nothing.

Each job record should carry: id, type, mailbox/tenant, requested_by, state,
progress, timestamps, retry count, and a **safe** error summary (no mailbox
content). Detailed state machine + schema is an S21 deliverable.

---

## 7. Data boundaries

The layers, and which callers may read each:

| Tier | Contents | Who reads it |
|---|---|---|
| **Raw mailbox data** | Provider API responses / raw MIME (raw MIME not stored yet, D6) | Ingest worker only |
| **L0 normalized messages** | Cleaned, deduped messages | Enrichment + retrieval, creator-scoped |
| **L1 structured** | People, relationships, projects, roles, events | Enrichment, creator surfaces, package generation |
| **L2 embeddings / retrieval** | `voyage-4` vectors, FTS, hybrid index | Retrieval, creator surfaces, package generation |
| **HandoffPackage snapshot** | Frozen package + claims + evidence + audit events | Creator (own) + **package generation writes it** |
| **Recipient package views** | Read-only projection of the snapshot | **Recipient session only** |
| **Audit logs** | Safe lifecycle metadata | Creator (own), admin/security, operator |

**Invariant (must survive every S18+ change):** the **recipient package view
reads only package-local snapshot rows** — never live L0/L1/L2 or mailbox rows.
The one-time code + session authorizes access to a *frozen snapshot*, not to the
mailbox. This is already true today (S17.6+), and hosting, auth, and admin work
must not open a back door around it. Any new query path a recipient session can
reach must be provably snapshot-only.

---

## 8. Privacy / compliance posture

Product stance (already reflected in the shipped MVP; restated as the production
contract):

- **Employee-initiated by default** — the covered employee creates and scopes the
  package. Not manager/HR search of an inbox (D14).
- **Creator reviews before publish** — nothing reaches a recipient without the
  owner seeing and pruning it.
- **Sensitive/noise exclusion before the recipient package** — exclusion happens
  at generation; excluded content never enters the snapshot.
- **Recipient sees the global privacy posture only** — a statement that sensitive
  content is excluded, **not** exclusion counts or categories (no leakage of "how
  much" or "about what").
- **No existence oracle** — the package-local ask cannot confirm/deny sensitive
  topics; it answers only from included evidence.
- **No productivity / performance scoring** — the product is continuity, not
  monitoring; this is a hard product boundary.
- **Audit records safe metadata only** — actor, action, package id, timestamp;
  never mailbox content or excluded material.
- **Published packages are immutable** — corrections create a new version that
  supersedes the prior one.

**Future compliance questions to resolve with HR/Legal (not in S18):**

- HR/legal review of the handoff flow and consent wording.
- Default **retention period** for packages and snapshots (§ open question 4).
- **Right to revoke/delete** — creator revoke exists; define data deletion
  semantics, incl. third-party content in shared threads (existing open question).
- **Employee consent wording** at mailbox-connect time.
- **Company policy boundaries** — what a workspace admin may/may not configure.
- **Cross-border data/storage** — where mailbox-derived data and tokens may live.

---

## 9. Production-readiness gap checklist

Concrete gaps between the current demo and a hosted product. Each maps to an S19+
sprint (§11). None are started.

- [ ] **Real creator auth** — SSO/OIDC owner login; mailbox bound to owner. *(S19)*
- [ ] **Workspace/tenant model** — tenant boundary on every row + query. *(S19)*
- [ ] **OAuth token vault** — provider tokens outside the app DB. *(S20)*
- [ ] **Hosted DB migrations + runbook** — safe forward/rollback procedure. *(S22)*
- [ ] **Background worker + queue** — job orchestration + state machine. *(S21)*
- [ ] **Deployment pipeline** — build/test/deploy for frontend, backend, worker. *(S22)*
- [ ] **Monitoring / alerts** — health, job failures, quota, error rates. *(S22)*
- [ ] **Rate-limit handling** — provider quotas + our own endpoint limits. *(S20/S21)*
- [ ] **Backup / restore** — DB backups; tested restore. *(S22)*
- [ ] **Admin / audit viewer** — read-only governance UI. *(S23)*
- [ ] **Production secrets management** — no secrets in `.env`/DB. *(S22)*
- [ ] **Data retention policy** — enforced expiry/deletion jobs. *(S24)*
- [ ] **Security review** — external/internal review before customer data. *(S24)*
- [ ] **Threat model for package links/sessions** — capability code + session. *(S24)*
- [ ] **Email provider review** — Google (and later Microsoft) app verification /
      restricted-scope review. *(S20)*

---

## 10. MVP launch path (phased)

| Phase | What must be true | Intentionally deferred |
|---|---|---|
| **1 — Local / internal demo** *(today)* | Everything in §0 works locally; manual scripts; `.env` secrets; single mailbox via id | All hosting concerns |
| **2 — Hosted single-tenant pilot** | Hosted infra (§5); real creator auth (S19); Gmail OAuth + token vault (S20); background ingest/enrich (S21); managed DB + migrations runbook (S22); basic monitoring + backups; retention **policy stated** (enforcement may lag) | Multi-tenant isolation depth; admin audit UI; M365; PDF/export-in-prod decision; manager approval |
| **3 — Controlled workspace beta** | Tenant/workspace isolation hardened; admin/audit viewer (S23); security review + threat model (S24) passed; retention enforced; rate-limit + quota handling proven at real-mailbox scale | Broad self-serve signup; billing; M365 GA |
| **4 — Broader multi-tenant product** | Multi-tenant at scale; M365 (S20 follow-on); self-serve onboarding; full compliance posture (DPA, cross-border, consent); operational maturity | — |

Phase 1→2 is the biggest jump and is gated on S19+S20+S21+S22. Each later phase
adds governance and scale, not new end-user surface.

---

## 11. S19+ candidate sprint map (dependency order)

Proposed only — **do not start.** Ordered by dependency.

1. **S19 — Auth + tenant boundary spec.** Owner authentication (SSO/OIDC),
   mailbox-owner binding, workspace/tenant model on every row and query, and the
   dev-only `mailbox_id` flag. *Foundation for everything else.*
2. **S20 — OAuth / token-vault + provider connection spec.** Gmail confidential
   client, least-privilege scopes, token vault, refresh/revocation, account-
   mismatch handling, provider app-verification path; M365 as the second provider.
   *Depends on S19 (tokens belong to an authenticated owner/tenant).*
3. **S21 — Background job orchestration.** Queue + worker pool + job state machine
   (§6) for ingest/enrich/embed/materialize/(generate). *Depends on S19/S20.*
4. **S22 — Hosted deployment + runbook.** Infra (§5), managed DB migrations,
   secrets manager, CI/CD, monitoring, backups. *Depends on S19–S21.*
5. **S23 — Admin / audit viewer.** Read-only governance UI over safe audit
   metadata; no mailbox browsing. *Depends on S19 (roles) + S22 (hosting).*
6. **S24 — Production security / privacy hardening.** Threat model (package
   links/sessions), security review, retention enforcement, cross-border/DPA.
   *Depends on S19–S23.*
7. **S25 — Real-mailbox quality + event-extraction pipeline.** Enrichment/event
   quality at real scale (parallels S6's quality pass, now for the hosted path).
   *Can proceed in parallel once S20/S21 exist.*

---

## 12. Open questions (for product/eng/legal before S19)

1. **Tenancy:** is the first hosted version **single-tenant** (one company) or
   **multi-tenant** from day one? (Changes the S19 data model depth.)
2. **Recipient identity:** must recipients have a **company account**, or does
   **capability-link** access remain acceptable for MVP?
3. **Who may initiate a handoff:** only the mailbox owner, or also
   **manager/admin with consent**?
4. **Default retention period** for packages/snapshots?
5. **Exports in production MVP:** allowed, or **disabled until policy approved**?
6. **Where do OAuth tokens live** (secrets manager vs. envelope-encrypted DB)?
7. **First hosted provider:** Gmail only, or Gmail + M365?
8. **Minimum audit UI** needed before production?
9. **Manager approval before publish** — required for any customer segment?
10. **Employee-exit data handling:** what must be deleted/revoked when an
    employee leaves (tokens, packages, snapshots, derived L1/L2)?

---

## Acceptance (this sprint)

- Docs-only; no backend/frontend/schema/migration/dependency changes.
- The plan is specific enough that an engineer can implement **S19 (auth + tenant
  boundary)** directly from §2, §3, §7, and §11.
- Shipped S17 behavior (§0) is clearly separated from planned S18/S19+ work.
- `git diff --check` passes.
- README/AGENTS/CLAUDE updated with **pointer/status only** (see those files).
