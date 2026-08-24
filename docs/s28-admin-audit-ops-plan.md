# S28 — Admin / Audit Viewer + Operations Console (spec)

**Status:** **Implemented by S29 (read-only viewer) + S30 (two audited actions).**
This document is the originating spec; it made no code changes itself.

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes are made in *this* document. It designs the **governance and operational
> visibility** surface now that S22–S27 are implemented, under one hard rule: **the
> admin/audit/ops console must never become a backdoor into employee mailbox
> content or recipient package evidence.** Implementation is S29+ (§17); nothing
> here is built.

**Source docs (authoritative, in precedence order):** `docs/decisions.md` (D6
tokens never touch app DB/logs; D14 handoff direction), then
`docs/s18-hosted-product-readiness-plan.md` (§2 roles, §7 data boundaries, §8
privacy), `docs/s19-auth-tenant-boundary-plan.md` (§6 admin/security-reviewer
boundary, §9 audit events, the `owner()`/tenant model), `docs/s20-oauth-token-vault-plan.md`
(§8 lifecycle, §10 audit events), `docs/s21-background-job-orchestration-plan.md`
(§10 audit, §13 privacy — jobs are creator/operator-side, never recipient-read),
`docs/s27-hosted-deploy-readiness-plan.md` (readiness checks + safe-metadata
posture), `docs/s17-handoff-package-mvp-plan.md`, `docs/s17-live-validation.md`,
and `README.md`/`AGENTS.md` for shipped status. Where they disagree, that order wins.

**Status of the arc:**
- **S17.2–S17.20 — shipped** (audited Handoff Package MVP; behavior unchanged here).
- **S18 / S19 / S20 / S21 — shipped as docs-only specs.**
- **S22 (auth+tenant), S23 (Gmail OAuth + dev token vault), S24 (job infra), S25
  (ingest→jobs), S26 (pipeline→jobs), S27 (hosted readiness guardrails) — implemented.**
- **S28 — this spec.** Admin/Audit Viewer + Operations Console. This document is the
  spec; it shipped no code itself.
- **S29 ✓ + S30 ✓ — implemented.** **S29** built the read-only Admin/Audit Viewer
  (the §5 GET routes, allow-list DTOs, role guards). **S30** built the two audited
  admin actions (§7): `POST /api/admin/packages/{id}/revoke` and
  `POST /api/admin/provider-accounts/{id}/disconnect`, tenant-admin-only, reason-gated,
  audited, reusing the creator-revoke and vault-disconnect paths (disconnect **fails
  closed** if the vault is unavailable or revoke fails). The roles, DTOs, endpoints,
  events, and actions described below are therefore **now built** — read this doc as
  a historical spec realized by S29/S30, not as unbuilt design.

> **Note:** the sections below are written in the original *proposed* tense; where a
> line says something is "proposed for the S29+ build" or "not implemented," read it
> as **implemented by S29 (read set) / S30 (actions)** per the status above.

**Untouched invariant:** the recipient package view reads **only package-local
snapshot rows** (S18 §7, S19 §5). S28 adds a **creator/operator-side governance
surface**; it never widens what a recipient can see, and no admin route reads
mailbox content or package evidence bodies (§8, §9).

---

## 1. Purpose and non-goals

**Purpose.** Give a tenant's governance and operations people **safe visibility and
narrow, audited control** without content access:
- **See** package lifecycle metadata, provider-connection metadata, job status,
  audit trails, aggregate privacy/exclusion posture, and system readiness.
- **Act** in two narrow, audited ways only: **revoke a package** and **disconnect a
  provider account** — each with a **mandatory reason**.
- **Never read** mailbox content, package evidence bodies/snippets/subjects, source
  text, Gmail/source links, OAuth tokens/codes, provider responses, raw job params,
  prompts, tracebacks, or recipient sessions.

**Non-goals (do NOT design/build here):**
- **No content backdoor of any kind** (the overriding invariant, §8).
- **No admin impersonation** of a recipient and **no admin open** of the recipient
  package view (§3, §9).
- **No admin editing/generating/publishing/pruning** a package on a creator's behalf
  (§5, §7). Admin control is limited to revoke + disconnect.
- **No token viewing, no silent reconnect, no admin OAuth connect** on a user's
  behalf (§6, §11).
- **No legal-hold content access.** Any future content access for legal review is a
  **separate, higher-scrutiny feature** with explicit approval — out of scope (§3, §8).
- **No new productivity/performance scoring** — governance is continuity + safety,
  never employee monitoring (D14, S21 §13).
- **No M365**, no new OAuth provider, no new auth provider beyond the shipped
  S22/S23 model, no retention-enforcement engine (that is a later hardening sprint).
- **S28 ships no code.** Implementation sequencing is §17.

---

## 2. Role / capability matrix

Roles reuse the shipped `TenantMembership.role` enum (`creator`, `admin`,
`security_reviewer`) plus the **operator** concept. **Operator status (S19 §6):
"operator" is not currently a product-API role** — it is infra-level. S28 must
resolve how the ops console authenticates (see the open question §15.1); the default
below treats operator capabilities as a distinct, separately-authenticated surface,
not a mailbox-content path.

Capabilities (`—` = not allowed; `meta` = safe metadata only; `cfg` = policy-configurable):

| Capability | Creator | Recipient | Tenant admin | Security reviewer | Operator |
|---|---|---|---|---|---|
| Ingest / scope / review / publish own mailbox | ✅ (own) | — | — | — | — |
| Read own package **content** (creator preview) | ✅ (own) | — | — | — | — |
| Open recipient package view (content) | — | ✅ (own link) | — | — | — |
| See package **lifecycle metadata** (tenant) | own | — | ✅ meta | ✅ meta | — |
| See package **evidence bodies / snippets / scope detail** | own | own (snapshot) | — | — | — |
| **Revoke** a package (mandatory reason) | own | — | ✅ (audited) | — | — |
| Edit / generate / publish / prune a package | own | — | — | — | — |
| See provider-account **connection metadata** | own | — | ✅ meta | meta | — |
| **Disconnect** a provider account (mandatory reason) | own | — | ✅ (audited) | — | — |
| View OAuth tokens / vault refs / codes | — | — | — | — | — |
| See **job** status + safe metadata (tenant) | own | — | ✅ meta | meta | ✅ meta |
| See **raw** job params / bodies / tokens / tracebacks | — | — | — | — | — |
| Inspect **audit trail** (safe metadata) | own | — | ✅ (tenant) | ✅ (tenant) | ops-scope |
| See **aggregate exclusion counts** | own | — | ✅ counts | ✅ counts | — |
| See **excluded content** (subjects/bodies/ids) | — | — | — | — | — |
| See **system readiness / worker / queue health** | — | — | meta | — | ✅ meta |
| Read mailbox L0/L1/L2 / Gmail / source-message | own | — | — | — | — |

**Two boundaries encoded above:** admin/security/operator get **metadata and
lifecycle state**, never content; and **revoke + disconnect are the only admin
mutations**, both audited with a mandatory reason.

---

## 3. Data visibility matrix by role

For each data domain, what each governance role may read. **Everything not listed
as visible is denied by default (fail closed).**

**Handoff package (per `HandoffPackage`):**
- **Visible (admin + security reviewer):** `id`, `mailbox_id`, `status`, `version`,
  `lineage_id`, `supersedes_package_id`, `policy_mode`, `title`, `creator_email`,
  `creator_user`(id), recipient `recipient_email`, `created_at`, `published_at`,
  `expires_at`, `revoked_at`, and derived `exported_at` (from audit events).
- **NOT visible to any governance role:** `HandoffClaim.text`, `HandoffEvidence`
  (`body_snapshot`, `subject`, `sender_display`, `snippet`, `message_id_header`,
  `source_type`), `HandoffScope` selection detail (`included/excluded_*`,
  `keyword_filters`, `excluded_message_id_headers`), and the package `reason`
  free-text (creator-authored, may hint at content — **excluded by default**, §15.2).
- **Recipient session artifacts** (`HandoffRecipient.capability_code_hash`,
  `HandoffRecipientSession.session_token_hash`): **never** visible.

**Provider account (per `MailboxProviderAccount`):**
- **Visible (admin; security reviewer meta):** `id`, `tenant_id`, `owner_user_id`,
  `mailbox_id`, `provider`, `provider_account_email`, `scopes_granted`, `status`,
  `connected_at`, `last_verified_at`, `disconnected_at`, `mismatch_reason`(category).
- **NOT visible:** `vault_ref` (opaque handle — pointless + risk), any token, the
  `OAuthState.code_verifier` (never surfaced anywhere).

**Job (per `Job`):**
- **Visible (admin/operator; security reviewer meta):** `id`, `job_type`, `status`,
  `attempt`, `max_attempts`, `created_at`/`started_at`/`finished_at`/`next_retry_at`,
  `progress` **safe counters only**, `summary` (already sanitized), `error_category`,
  and `tenant_id`/`mailbox_id` per role.
- **NOT visible:** raw `params`, `error_message` free-text, `idempotency_key`,
  `worker_id`(internal host:pid), and anything content-like inside params.

**Audit (per `AuditLog` + `HandoffAuditEvent`):**
- **Visible (admin/security reviewer):** `actor`, `action`, `scope`, `ts`,
  `message_count`, `package_id`/`mailbox_id`, and a **whitelisted safe projection**
  of `HandoffAuditEvent.metadata_` (§6) — never the raw JSONB blob.
- **NOT visible:** `AuditLog.sync_token` (provider sync token), any metadata key not
  on the safe whitelist.

**Exclusion posture (per `HandoffExclusion`):**
- **Visible (security reviewer + admin):** **aggregate counts** grouped by
  `exclusion_type` and safe `aggregate_label` (category), plus a policy-outcome
  summary (e.g. "N threads excluded as sensitive").
- **NOT visible:** `target_ref` (thread/message id), per-item `audit_reason` bodies,
  excluded subjects/bodies/message ids, or any per-message exclusion detail.

**Readiness / ops (per S27 `hosted_readiness` checks):**
- **Visible (operator; admin meta):** per-check `{name, status}`, overall
  ready/degraded, and the **already-curated** safe `message` (guaranteed
  secret-free); worker/queue safe counts (queued/running/stuck counts, last worker
  activity age).
- **NOT visible:** env values, `DATABASE_URL`, OAuth secret/config values, full CORS
  origins if sensitive (count only), stack traces, raw exception text, vault refs.

---

## 4. Proposed read models / DTOs

Response DTOs (Pydantic, S29 build). **Every DTO is an explicit allow-list** — it
names the safe fields it exposes; it never spreads a raw ORM row or JSONB blob.

```
PackageAdminSummary        { id, mailbox_id, title, status, version, lineage_id,
                             creator_email, recipient_email, created_at,
                             published_at, expires_at, revoked_at }
PackageAdminDetail         = PackageAdminSummary + { policy_mode, creator_user_id,
                             supersedes_package_id, exported_at, recipient_state
                             (granted/consumed/expired/revoked — derived, no hashes),
                             counts { claim_count, evidence_count } }  # counts only
PackageAuditEventView      { package_id, lineage_id, actor, action, ts,
                             safe_metadata: dict }   # whitelisted keys only (§6)
ProviderAccountAdminView   { id, mailbox_id, owner_user_id, provider,
                             provider_account_email, scopes_granted, status,
                             connected_at, last_verified_at, disconnected_at,
                             mismatch_reason }        # NO vault_ref / token
JobAdminView               { id, job_type, status, tenant_id, mailbox_id, attempt,
                             max_attempts, created_at, started_at, finished_at,
                             next_retry_at, progress_safe: dict, summary,
                             error_category }         # NO params / error_message / worker_id
AuditEventView             { actor, action, scope, ts, message_count,
                             mailbox_id, package_id }  # NO sync_token
ExclusionSummaryView       { by_type: [{ exclusion_type, aggregate_label, count }],
                             total_excluded }          # counts only
ReadinessSummaryView       { ready: bool, checks: [{ name, status, message }] }
OpsHealthView              { db_reachable, alembic_at_head, queue_observable,
                             queued, running, stuck, last_worker_activity_seconds }
TenantOpsOverview          { package_counts_by_status, active_provider_accounts,
                             recent_job_counts_by_status, degraded_readiness: bool }
```

`progress_safe` is a re-projection of `Job.progress` keeping only scalar
counters/phase (reusing `services/jobs/sanitize.py` guarantees); it never passes a
content-like value. `PackageAdminDetail.counts` are aggregate integers, not rows.

---

## 5. Proposed endpoints

All under `/api/admin/*`, **tenant-scoped**, guarded by new role dependencies in
`services/api/auth.py` (`require_admin`, `require_security_reviewer`,
`require_operator`) layered on the shipped `get_principal`. **Cross-tenant lookups
return 404, not 403** (no cross-tenant existence oracle, S19 §4). **Read endpoints
are GET; the two mutations are POST with a mandatory reason.** No endpoint returns
content; each maps to a §4 DTO.

**Read (S29 — read-only viewer):**
- `GET  /api/admin/overview` → `TenantOpsOverview` (admin)
- `GET  /api/admin/packages` (+ filters: status, lineage, date) → `[PackageAdminSummary]` (admin, security_reviewer)
- `GET  /api/admin/packages/{package_id}` → `PackageAdminDetail` (admin, security_reviewer)
- `GET  /api/admin/packages/{package_id}/audit` → `[PackageAuditEventView]` (admin, security_reviewer)
- `GET  /api/admin/provider-accounts` → `[ProviderAccountAdminView]` (admin; security_reviewer meta)
- `GET  /api/admin/jobs` (+ filters) → `[JobAdminView]` (admin, operator)
- `GET  /api/admin/jobs/{job_id}` → `JobAdminView` (admin, operator)
- `GET  /api/admin/audit` (+ filters) → `[AuditEventView]` (admin, security_reviewer)
- `GET  /api/admin/exclusions/summary` → `ExclusionSummaryView` (security_reviewer, admin)
- `GET  /api/admin/readiness` → `ReadinessSummaryView` (operator; admin meta)
- `GET  /api/admin/ops/health` → `OpsHealthView` (operator)

**Actions (S30 unless §17 shows they are small/safe enough for S29):**
- `POST /api/admin/packages/{package_id}/revoke` body `{ reason: str (required, 1..500) }` → 200 `{status:"revoked"}` (admin)
- `POST /api/admin/provider-accounts/{account_id}/disconnect` body `{ reason: str (required, 1..500) }` → 200 `{status:"disconnected"}` (admin)

**Recipient endpoints are explicitly NOT extended.** There is **no**
`/api/admin/packages/{id}/view`, no recipient-session read, no impersonation route.
The recipient surface (`services/api/routers/handoff_recipient.py`) is untouched.

---

## 6. Audit events and safe-metadata rules

Every governance **read of sensitive scope** and **every action** is itself audited,
extending the S19 §9 / S20 §10 / S21 §10 catalogs. Reuse `HandoffAuditEvent`
(package-scoped) and `AuditLog` (mailbox/tenant-scoped); **no new audit table**.

| Event | When | Key safe fields |
|---|---|---|
| `admin.audit.viewed` | Admin/security reviewer reads audit/exclusion data | actor, tenant_id, filter (category), ts |
| `admin.packages.viewed` *(throttled/optional)* | Governance lists/reads package metadata | actor, tenant_id, count |
| `package.revoked_by_admin` | Admin revokes a package (governance) | package_id, admin_user_id, **reason**, ts |
| `oauth.disconnect.requested` / `oauth.disconnect.completed` | Admin disconnects a provider account | mailbox_id, provider, admin actor, **reason**, ts |
| `admin.readiness.viewed` *(optional)* | Operator/admin reads readiness/ops health | actor, ts |

**Safe-metadata rules (hard):**
- Audit metadata carries **only** ids, actions, categories, timestamps, counts, and
  the admin-authored **reason** (governance text, length-capped, treated as safe
  metadata — never mailbox content).
- **Never** in any audit row/metadata: email body/subject/snippet, excluded content
  or its "how much/about what" beyond aggregate counts, OAuth token/code/refresh/
  access/`state`/`code_verifier`, `vault_ref`, capability/session token or its hash,
  raw provider/LLM response, prompt/response text, stack trace, or raw `Job.params`.
- The `HandoffAuditEvent.metadata_` **read projection** is a **whitelist** (allowed
  keys enumerated in the S29 build); unknown keys are dropped, never passed through.
- Errors recorded as **category/type-name only** (matches `services/jobs` +
  `hosted_readiness` posture; S27 `/readyz` fallback logs only the exception type).

---

## 7. Admin actions and required reason fields

Exactly **two** mutations, both admin-only, both requiring a non-empty `reason`,
both producing an audit event, neither touching content or tokens:

1. **Revoke package** — `POST /api/admin/packages/{id}/revoke`, `reason` required
   (1..500 chars). Resolves package → tenant, checks tenant `admin`, sets
   `HandoffPackage.revoked_at` (reusing the existing owner-revoke transition/service
   so behavior matches creator revoke), and writes `package.revoked_by_admin` with
   the reason. **After revoke, recipient access is blocked at request time** (S17
   already re-checks revoked state per request — no new recipient logic). Idempotent:
   revoking an already-revoked package is a no-op success. **No** edit/generate/
   publish/prune capability is added.

2. **Disconnect provider account** — `POST /api/admin/provider-accounts/{id}/disconnect`,
   `reason` required. Resolves account → tenant, checks tenant `admin`, performs the
   S20 §8 admin-forced disconnect (**revoke at provider + purge vault entry**, set
   `status=disconnected`, `disconnected_at`), and writes `oauth.disconnect.*` with
   the reason and `actor=admin`. **Admin never sees the token, never reconnects
   silently, and cannot connect on a user's behalf** (S20 §9). Dependent jobs stop
   per existing provider-revocation handling.

**Reason handling:** trimmed, length-capped, stored in audit metadata as safe
governance text. A missing/blank reason → **422** (the action never proceeds).

---

## 8. Privacy / security invariants (must survive every S28+ change)

- **No admin route is a content backdoor.** No `/api/admin/*` route returns mailbox
  bodies/subjects/snippets, package **evidence bodies** (`HandoffEvidence.body_snapshot`),
  `HandoffClaim.text`, scope selection detail, excluded content, Gmail/source links,
  `source-message` detail, OAuth tokens/codes/`state`/`code_verifier`, `vault_ref`,
  provider/LLM responses, prompt/response text, stack traces, or raw `Job.params`.
- **Admin package access is metadata-only.** Content access for legal review, if
  ever needed, is a **separate feature** requiring explicit approval + higher
  scrutiny + its own consent/audit design (S19 §6) — not part of S28/S29.
- **Governance ≠ silent mailbox browsing** (S18 §2/§3, S19 §6). Admin/security can
  read metadata and govern lifecycle; they cannot open a mailbox or read as a recipient.
- **No token/secret ever crosses the admin surface** (D6). DTOs are allow-lists;
  `vault_ref`/hashes/tokens are never fields.
- **Tenant isolation:** every admin query is filtered by the caller's `tenant_id`;
  cross-tenant → 404. No role reads across tenants through the product API (S19 §1/§4).
- **Fail closed:** an admin/ops route with no explicit role match denies by default;
  `AUTH_MODE=production` requires a real principal (S22); a new admin route without
  the role dependency must fail a lint/test gate (define in the S29 build).
- **Every sensitive read + every action is audited** with safe metadata only (§6).
- **No productivity/performance scoring** — admin/ops data is never repurposed as a
  per-person monitoring signal (S21 §13).

---

## 9. Recipient snapshot-only invariant (restated)

The recipient package view reads **only package-local snapshot rows** and is
**unchanged by S28** (S18 §7, S19 §5, S27 restatement). Specifically:
- Recipients remain **non-tenant, capability-code + session** authenticated; they are
  not admin/ops users and never gain a tenant role.
- **No admin route reads a recipient session, its token/hash, or opens the recipient
  package view.** There is no impersonation path.
- `services/api/routers/handoff_recipient.py` gains **no import** of the admin/jobs/
  pipeline/oauth/vault modules (the S27 `check_recipient_snapshot_only` static
  assertion continues to guard this; S29 keeps it green).
- An admin **revoke** only flips lifecycle state; the recipient's request-time
  revoked-check (already shipped) does the blocking — no new recipient code path.

---

## 10. Job and readiness visibility rules

**Jobs (`JobAdminView`):** expose only `job_type`, `status`, timestamps, `attempt`/
`max_attempts`, `progress_safe` (scalar counters/phase), `summary` (sanitized),
`error_category`, and `tenant_id`/`mailbox_id` per role (operator sees tenant-wide;
security reviewer sees metadata). **Never** raw `params`, `error_message`,
`idempotency_key`, `worker_id`, bodies, subjects, snippets, provider responses,
tokens, OAuth codes, prompts, tracebacks, or raw exception messages. Jobs are
creator/operator-side; **recipients never see jobs** (S21 §13) — S28 does not change
that.

**Readiness (`ReadinessSummaryView` / `OpsHealthView`):** reuse the S27
`services/hosted_readiness` checks, which already return **safe** `PreflightCheck`
messages (presence/state/category, no secrets). Expose `{name, status, message}` per
check + overall ready/degraded + safe worker/queue counts. **Never** env values,
`DATABASE_URL`, OAuth secret/config values, full CORS origins if sensitive (count
only), stack traces, raw exception text, or vault refs. The public `/readyz` stays a
bare status; the detailed admin readiness view is **role-guarded** (operator/admin),
not public.

---

## 11. Provider-account / OAuth visibility rules

- **Visible metadata (admin):** provider, connected account email, `scopes_granted`,
  `status` (`connected`/`refresh_failed`/`revoked`/`disconnected`/`mismatch_blocked`),
  `connected_at`/`last_verified_at`/`disconnected_at`, and a safe `mismatch_reason`
  **category**.
- **Never visible:** access/refresh tokens (vault-only), `vault_ref`, authorization
  `code`, `state`, PKCE `code_verifier` (`OAuthState` is never surfaced), or any raw
  provider response.
- **Actions:** admin **disconnect** only (§7) — provider revoke + vault purge, audited.
  **No** token viewing, **no** silent reconnect, **no** admin OAuth **connect** on a
  user's behalf (connect stays owner-initiated, S20 §9).
- Security reviewer sees connection **status metadata** for posture, not the ability
  to disconnect.

---

## 12. Package lifecycle visibility rules

- **Visible lifecycle metadata:** `status` (`draft`/`published`/`revoked`/superseded),
  `version` + `lineage_id` + `supersedes_package_id` (version chain), `title`,
  `creator_email`/`creator_user_id`, `recipient_email`, and the lifecycle timestamps
  `created_at`/`published_at`/`expires_at`/`revoked_at`, plus a derived `exported_at`
  and `recipient_state` (granted/consumed/expired/revoked — **derived flags, no
  hashes**), and aggregate `claim_count`/`evidence_count`.
- **NOT visible:** `HandoffClaim.text`, `HandoffEvidence` bodies/subjects/snippets/
  headers, `HandoffScope` selection detail and `keyword_filters`, the package
  free-text `reason` (§15.2), capability/session hashes, and any evidence source text.
- **Package audit trail** (`HandoffAuditEvent`): actor/action/ts + whitelisted safe
  metadata (§6), giving governance the full create→scope→generate→publish→export→
  revoke→new-version history **as metadata**, never content.

---

## 13. Tests expected for the S29+ implementation

- **Role/authz matrix:** each `/api/admin/*` route allows exactly its roles and
  returns **404** cross-tenant, **401** unauthenticated (`AUTH_MODE=production`),
  **403**/hidden for a wrong in-tenant role; dev principal still works locally.
- **No-content assertions (the core suite):** for every read DTO, assert the response
  **cannot** contain `HandoffEvidence.body_snapshot`/subject/snippet,
  `HandoffClaim.text`, scope selection detail, excluded content, `vault_ref`, any
  token/hash, `Job.params`/`error_message`/`worker_id`, `sync_token`, or a stack
  trace — driven by seeding rows with sentinel secret-like values and asserting their
  absence in the serialized response.
- **Exclusion aggregation:** counts only; assert no `target_ref`/subject/message id
  leaks.
- **Revoke action:** requires reason (422 without), flips `revoked_at`, is idempotent,
  writes `package.revoked_by_admin` with the reason, and **blocks recipient access at
  request time** (reuse the S17 recipient revoked-state test).
- **Disconnect action:** requires reason, performs provider revoke + vault purge,
  sets `status=disconnected`, writes `oauth.disconnect.*`, exposes **no** token; a
  non-admin is denied.
- **Readiness/ops view:** reuses S27 checks; assert no env/DB-URL/secret/CORS-origin/
  traceback leaks; public `/readyz` still bare.
- **Recipient invariant unchanged:** the S27 `check_recipient_snapshot_only` static
  assertion stays green; S17 recipient/handoff suite still passes; recipient
  responses still expose no jobs/provider/live-mailbox/exclusion-count data.
- **Audit safety:** every audited event carries only whitelisted safe fields; a
  fuzz/whitelist test rejects any non-whitelisted metadata key.
- **No live Voyage/Anthropic call** anywhere; the two live-call guards stay skipped.

---

## 14. Manual validation plan (for the S29+ build)

1. As a tenant **admin**, list packages and open a package detail + its audit trail;
   confirm you see lifecycle metadata and **cannot** find any evidence body, claim
   text, scope detail, or Gmail/source link in the payload.
2. As a **security reviewer**, open the exclusion summary; confirm **counts only** —
   no excluded subjects/bodies/ids.
3. As an **operator**, open readiness + ops health; confirm safe check messages and
   worker/queue counts; confirm no env value, DB URL, secret, or vault ref.
4. Attempt an admin **revoke** without a reason → 422; with a reason → package
   revoked, audit event written, and the recipient link now returns the neutral
   "no longer available" response.
5. Attempt an admin **disconnect** without a reason → 422; with a reason → account
   `disconnected`, vault purged, audit event written, **no token shown anywhere**.
6. Cross-tenant probes (another tenant's package/account/job id) → **404**.
7. Confirm a **recipient** session still reads only the package snapshot and cannot
   reach any `/api/admin/*` route.
8. Confirm the public `/readyz` is still a bare `{"status":...}` and the detailed
   readiness view requires an operator/admin role.

---

## 15. Open questions (for product/eng before the S29 build)

1. **Operator authentication.** S19 §6 says operator is **not** a product-API role.
   Options: (a) add `operator` to the `TenantMembership` role enum (a small S30
   migration widening `ck_tenant_membership_role`), scoping ops to a tenant; or (b)
   keep operator at the **infra layer** with a separate, non-tenant ops authn for
   system-wide health, and expose only *tenant-scoped* ops metadata to `admin`.
   **Recommend (b) for read-only S29** (admin sees tenant ops; system-wide health
   stays operator/infra), revisiting (a) if a product-level operator role is needed.
2. **Package `reason` visibility.** The creator-authored `HandoffPackage.reason` may
   hint at content. **Default: excluded** from the admin view. Confirm, or expose a
   truncated/reviewed form only.
3. **`recipient_email` exposure.** Default visible to admin (governance needs to know
   who a package went to). Confirm this is acceptable under the tenant's privacy policy.
4. **Read auditing volume.** Should every metadata *read* be audited, or only
   sensitive reads (audit/exclusion) + all actions? Default: audit actions + sensitive
   reads; throttle/aggregate routine list reads (§6 `admin.packages.viewed` optional).
5. **Security-reviewer scope on provider accounts.** Default: status metadata only
   (no disconnect). Confirm reviewers need no more.
6. **Ops health breadth.** How much system-wide health (cross-tenant queue depth) may
   an operator see without it becoming a cross-tenant oracle? Default: aggregate,
   non-tenant-identifying counts only.
7. **Where do the read models live?** Default: a new `services/admin/` read-service +
   `services/api/routers/admin.py`, reusing existing ORM rows — **no new table** for
   the read-only viewer (see §16 migration expectation).

---

## 16. Acceptance criteria (this docs/spec sprint)

- Docs/spec-only; **no** backend/frontend/schema/migration/dependency changes in this
  sprint. New `docs/s28-admin-audit-ops-plan.md` (this file).
- README / AGENTS / CLAUDE / `docs/implementation-plan.md` updated with
  **pointer/status lines only** — S28 shipped as this docs/spec-only plan; **the
  spec has since been implemented by S29 (read-only viewer) + S30 (audited
  actions)**.
- `git diff --check` clean.
- *(Historical, at the time of the S28 spec sprint:)* S28 shipped no code — every
  role, DTO, endpoint, event, and action above was *proposed for the S29+ build*.
  **Since then S29 (read set) and S30 (actions) have implemented them** — see the
  status block at the top of this doc.
- **S17.2–S17.20 shipped; S18–S21 docs-only specs; S22–S27 implemented; S28
  docs-only** — stated in the status block.
- **Migration expectation:** the read-only viewer (S29) needs **no migration** — it
  reads existing `HandoffPackage`/`MailboxProviderAccount`/`Job`/`AuditLog`/
  `HandoffAuditEvent`/`HandoffExclusion` rows. A migration is required **only** if a
  product-level `operator` role is added (§15.1) — a single widening of the
  `ck_tenant_membership_role` constraint, deferred to S30.
- **Recipient package snapshot-only invariant untouched**; existing creator/recipient
  behavior unchanged by this docs-only sprint.
- Specific enough that the S29 build can proceed from §2 (roles), §3 (visibility),
  §4 (DTOs), §5 (endpoints), §6 (audit), and §13 (tests) without guessing.

---

## 17. S29+ implementation sequence recommendation

1. **S29 — Read-only Admin / Audit Viewer (minimal).** The `/api/admin/*` **GET**
   endpoints (§5 read set) + their DTOs (§4) + the role dependencies
   (`require_admin`/`require_security_reviewer`) + the no-content test suite (§13).
   **No migration** (reads existing rows). Operator readiness/ops views land here too
   if operator is handled via option §15.1(b) (admin-tenant ops + infra health); the
   system-wide operator surface can follow. This is the smallest safe, high-value slice.
2. **S30 — Admin actions.** `revoke` + `disconnect` (§7), each with a mandatory
   reason and audit event, reusing the shipped owner-revoke and S20 forced-disconnect
   paths. If §15.1 chose a product `operator` role, its single-constraint migration
   lands here. *Optionally*, if the S29 build shows revoke/disconnect are small and
   safe enough (they reuse existing service paths), they may be pulled into S29 —
   decide at S29 build time, not now.
3. **After S30 — Admin/audit UI + security/privacy hardening.** A read-only frontend
   governance surface over the safe DTOs, then the retention-enforcement + external
   security-review sprint (threat-model the admin surface, S24-map hardening).

*(Numbering continues the S21 §14 sequence, which ended at S27; S28 is this spec,
S29+ implement it. Final renumbering is deferred to whoever opens those sprints.)*

---

## 18. Resolved defaults for S29 implementation

The §15 open questions may still be revisited on detail, but the S29 build **has a
clear default path** and does not need to wait on them. These defaults are
authoritative for the S29 (read-only Admin/Audit Viewer) build; deviating from one
requires an explicit decision.

1. **Operator role (resolves §15.1).** Keep **operator infra-level for S29** — it is
   **not** added to the `TenantMembership` enum yet. S29 implements the **tenant
   admin** and **security reviewer** read models first. Any product-level operator
   role, and any cross-tenant operator console, is **deferred** (and would carry the
   later single `ck_tenant_membership_role` widening, S30+).

2. **Package `reason` visibility (resolves §15.2).** `HandoffPackage.reason` is
   **DB-constrained to a safe enum** by migration `0007_handoff_package_tables`
   (`vacation` / `leave` / `transfer` / `delegation` / `other`) — it is not free
   text and cannot carry content. It is therefore **exposed as `reason_category`**
   in the admin list/detail DTOs (a structured safe enum, per the "expose a
   `reason_category` only if a structured safe enum exists" rule). **If** a
   free-text reason field is ever added later, that raw free-text value remains
   **forbidden** from admin DTOs (expose only the structured category). *(Shipped
   this way in S29: `reason_category = pkg.reason`.)*

3. **Recipient email visibility (resolves §15.3).** **Tenant admin** may see
   `recipient_email` as package metadata. **Security reviewer** sees the recipient
   **domain or a masked email only** (e.g. `j***@example.com`), unless product/legal
   explicitly approves full recipient email for reviewers.

4. **Audit coverage (resolves §15.4).** **Audit all admin mutations.** For reads,
   audit **sensitive detail reads and exports**, but **not every routine list view**.
   Add **throttling / rate-limiting** on list-view endpoints (and coalesce any
   list-read audit into a throttled/aggregated event) to avoid audit spam.

5. **Security-reviewer provider visibility (resolves §15.5) — Option A (stricter).**
   A security-reviewer-only principal sees **provider + connection status +
   timestamps only** (`provider`, `status`, `connected_at`, `last_verified_at`,
   `disconnected_at`). The account/mailbox/owner **ids**, `provider_account_email`,
   `scopes_granted`, `mismatch_reason`, `vault_ref`, and any token metadata are
   **omitted (null)** — governance posture without provider identity. Tenant
   **admin** sees the full connection metadata (email, scopes, ids, mismatch
   category) but still **never** `vault_ref` or any token. *(Shipped this way in
   S29: `ProviderAccountAdminView` nulls the identity fields when
   `full_access=False`.)*

6. **Operator / system-wide visibility (resolves §15.6).** **No cross-tenant customer
   metadata in S29.** Operator sees **deployment / readiness / job-system health at
   aggregate safe levels only**. Any tenant-specific drill-down requires a tenant
   `admin`/`security_reviewer` context, or a later explicit **support-access** feature.

7. **Read-model location (resolves §15.7).** Add a new **`services/admin/`
   read-service** plus **`services/api/routers/admin.py`**. **No new table** for S29
   unless implementation proves an audit/read-projection table is necessary (report
   why if so).

8. **Admin-actions sequencing (resolves §17).** **S29 is read-only** Admin/Audit
   Viewer. **Revoke-package** and **disconnect-provider-account** are specified but
   **deferred to S30**, unless they are proven trivial and low-risk **after** the
   read-only surface is complete (decide at S29 build time, not now).

9. **Scope guard (the S29 no-leak invariant).** S29 **must not** expose:
   `HandoffEvidence.body_snapshot`, `HandoffClaim.text`, raw `HandoffScope` detail,
   `source_message_id_headers`, Gmail/source links, raw `Job.params`, raw
   `Job.error_message`, `OAuthState` (incl. `code_verifier`), recipient session
   hashes, `vault_ref`, tokens, OAuth codes, DB URLs, env values, tracebacks, or
   prompt/response text. The S29 no-content test suite (§13) asserts each of these is
   absent from every `/api/admin/*` response.
