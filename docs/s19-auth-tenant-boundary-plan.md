# S19 — Auth + Tenant Boundary (spec)

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes. This document defines the production identity, tenancy, and
> authorization model that S20+ implements. It is the first buildable slice of
> the hosted track opened by `docs/s18-hosted-product-readiness-plan.md` (S18 §2,
> §3, §7, §11) — read that first; where this doc and S18 disagree, S18 wins.

**Status:** S19 is **shipped as a spec; not implemented in code** (implemented by
S22, per S21 §14). S17.2–S17.20 remains the
**shipped** MVP and its behavior is unchanged by this document; S18 remains the
docs-only hosted-readiness plan.

---

## 0. Scope & non-goals

**In scope (what S19 implementation must deliver):**
- A tenant/workspace model and a user/identity model.
- Mailbox ownership + tenant binding.
- A request **Principal** abstraction and an **authorization layer** enforced on
  every creator/mailbox-scoped endpoint.
- Role definitions and per-role permissions (creator, recipient,
  admin/security-reviewer, operator).
- The **dev-only mailbox-id mode** and exactly how it is disabled in production.
- The audit events for auth-sensitive actions.

**Explicitly deferred to S20+ (do not build in S19):**
- Email-provider (Gmail/M365) **OAuth consent + token vault + refresh/revocation**
  — that is S20 (S18 §4). S19 assumes a mailbox can be *owned*; it does not
  specify how its provider tokens are obtained or stored.
- Background job orchestration (S21), hosted deployment/secrets manager (S22),
  admin **UI** (S23 renders what S19's audit/authorization model records).
- Any change to the shipped recipient capability-code + session flow beyond the
  hardening already itemized in S18 §3 (kept as-is in S19).

**Non-goal:** S19 does not weaken the shipped **recipient package-local snapshot
invariant** (S18 §7). A stronger creator/admin identity must never become a new
path to live mailbox rows. Recipient authorization stays snapshot-scoped.

---

## 1. Tenant / workspace model

A **Tenant** (a.k.a. workspace) is the top-level isolation boundary: one customer
organization. Every persisted, mailbox-derived row belongs to exactly one tenant.

Design for multi-tenant from the schema down, even if the first hosted pilot runs
a single tenant (S18 open question 1). "Single-tenant pilot" = one `Tenant` row;
no code path assumes global/tenant-less data.

**Proposed entity (not migrated in S19):**

```
Tenant
  id            uuid  pk
  name          text
  status        enum(active, suspended)
  created_at    timestamptz
```

**Tenant-scoping rule:** every query that reads or writes mailbox-derived data
MUST be filtered by the caller's resolved `tenant_id`. Cross-tenant reads are a
bug, not a permission — there is no role that reads across tenants except the
**operator** at the infrastructure layer (§6), never through the product API.

---

## 2. User roles

Roles mirror S18 §2. A **User** is an authenticated identity within one tenant.
Roles are assigned per user within a tenant (a membership); a user may hold more
than one role.

```
User
  id                 uuid  pk
  tenant_id          uuid  fk -> Tenant
  idp_subject        text  -- stable subject ("sub") from the identity provider
  email              text
  status             enum(active, disabled)
  created_at         timestamptz
  unique (tenant_id, idp_subject)

Membership (role grant)
  user_id            uuid  fk -> User
  role               enum(creator, admin, security_reviewer)
  granted_by         uuid  fk -> User
  granted_at         timestamptz
  primary key (user_id, role)
```

Roles:

| Role | Who | Core capability |
|---|---|---|
| **creator** (covered employee) | Mailbox owner stepping away | Act only on mailboxes they own (§4) |
| **recipient** | Person covering | Not a `User`/role in S19 — authorized by capability code + session, package-scoped (§5) |
| **admin** | Workspace admin | Governance: memberships, connections, revoke; **no** content/mailbox read (§6) |
| **security_reviewer** | Security/compliance | Read audit metadata within tenant; **no** content/mailbox read (§6) |
| **operator** | Us / deploying team | Infra only; not a product API role (§6) |

`manager / approver` from S18 §2 stays **deferred** (off by default; only for
segments that require pre-publish approval — S18 open question 9). S19 leaves a
seam (a package may reference an optional `approved_by`) but does not implement
approval gating.

**Recipient is deliberately not a tenant User.** Recipients may be external to
the tenant; they authenticate to a *package*, not to the workspace (§5). Whether
recipients ever get company accounts is S18 open question 2, deferred.

---

## 3. Mailbox ownership and tenant binding

The existing `Mailbox` (identified today by `mailbox_id`) gains an **owner** and a
**tenant**. This is the pivot the whole authorization model turns on.

**Proposed additions (not migrated in S19):**

```
Mailbox (existing)
  id               uuid  pk         -- today's mailbox_id
  + tenant_id      uuid  fk -> Tenant
  + owner_user_id  uuid  fk -> User  -- the covered employee who owns this mailbox
  + connection_status enum(...)      -- provider-connection state (detail = S20)
```

Rules:
- A mailbox is **owned by exactly one User** (the covered employee) and lives in
  **exactly one Tenant**. `owner_user_id.tenant_id == mailbox.tenant_id` is an
  invariant.
- All descendant rows (L0 messages, L1 people/projects/events, L2 embeddings,
  HandoffPackage snapshots, audit events) inherit the mailbox's `tenant_id`. A
  package's owner/tenant is resolved via its `mailbox_id`.
- **Ownership is established at connect time.** When an authenticated owner
  connects a mailbox (the provider-OAuth flow is S20), the resulting `Mailbox`
  row is bound to that `owner_user_id` + `tenant_id`. The connected provider
  account identity must match the owner (S18 §4 "provider account mismatch");
  the mismatch check is specified in S20, but S19 requires the binding to exist.
- **Migration note (for the S19 build, not this doc):** existing dev/demo
  mailboxes have no owner/tenant. The implementing sprint backfills a synthetic
  dev tenant + dev owner for them (see §7), so shipped local/demo flows keep
  working. No production data exists yet, so there is no production backfill.

---

## 4. Creator permissions

The creator is the covered employee acting on **their own** mailbox. Authorization
predicate for any creator/mailbox-scoped request:

> **`ALLOW` iff** there is an authenticated `Principal` with a `creator` role
> whose `user_id == mailbox.owner_user_id` **and** whose `tenant_id ==
> mailbox.tenant_id`. Otherwise **`DENY` (404 for cross-tenant / not-owned,
> 401 if unauthenticated)** — return `404`, not `403`, for resources in another
> tenant so the API is not an existence oracle across tenants.

Creator capabilities (all restricted to owned mailboxes): ingest / scope / review
generated package / publish / revoke / view own package / ask (creator preview) /
export (if enabled by policy, S18 §10). A creator may **not** act on another
user's mailbox, read another user's package, or see any tenant-wide governance
data.

The concrete endpoint checks are in §8.

---

## 5. Recipient permissions (unchanged from shipped S17)

Recipient access is **already** the right shape and S19 keeps it:

- A published package mints a **one-time capability code** (URL fragment).
  Redeeming it (`POST /handoff/recipient/session`) consumes it once and returns a
  **short-lived bearer session token** bound to that single package.
- `GET /handoff/recipient/package` and `POST /handoff/recipient/ask` authorize
  **only** via that session token and read **only** package-local snapshot rows.
- A recipient is **not** a tenant `User`, has **no** tenant/role, and cannot reach
  any creator/mailbox endpoint or any other package.

S19 changes nothing here; it only records the S18 §3 hardening items as the
recipient-side backlog (TLS-only, short session TTL, server-side session
revocation on package revoke, rate-limited redemption, one-package binding) —
each is a small, separate task, not part of the S19 authorization layer.

**Invariant restated:** the session authorizes a **frozen snapshot**, never the
mailbox. Any new recipient-reachable query path must be provably snapshot-only.

---

## 6. Admin / security-reviewer permissions

Governance without content access. This is the sharpest boundary in S19.

- **admin** may: manage `Membership` (grant/revoke roles) within their tenant;
  manage mailbox connections (e.g., force-disconnect); **revoke** a published
  package for governance; view **audit metadata** (§9) for their tenant.
- **security_reviewer** may: read **audit metadata** for their tenant.
- **Neither** may: open/browse a mailbox; read L0/L1/L2 rows; read package
  **content** (claims/evidence/snapshots); create a recipient session; or act as
  a creator. There is **no admin path to mailbox content** — "governance ≠ silent
  mailbox browsing" (S18 §2/§3). Admin actions operate on *metadata and lifecycle
  state*, not on the mailbox-derived content itself.
- **operator** is not a product-API role: migrations, worker ops, secrets, and
  incident response happen at the infrastructure layer (S22), audited separately.

Any future need for an admin to *see* content (e.g., legal hold) is a distinct,
higher-scrutiny feature requiring its own consent/audit design — **out of scope
for S19**, flagged to HR/Legal (S18 §8).

---

## 7. Dev-only mailbox-id mode and how it is disabled in production

Shipped S17 runs locally by typing a `mailbox_id`; no login. S19 must **preserve
that for dev** while making it **impossible in production**.

**Mechanism — a single fail-closed switch:**

- An `AUTH_MODE` setting with values `dev` and `production`.
  - **Unset or unrecognized ⇒ treated as `production`** (fail closed). A hosted
    build never accidentally runs in dev mode.
- **`AUTH_MODE=dev`** (local engineering/demo only):
  - The authorization layer resolves a **synthetic dev Principal** (a fixed dev
    `User` in a fixed dev `Tenant`, owning the dev/demo mailboxes) instead of
    requiring a login. Ownership checks still run, but against the synthetic
    owner, so **today's localhost/demo flow is unchanged** (type a mailbox id,
    act on it).
  - The dev principal is confined to the dev tenant; it cannot be minted by any
    request header from outside.
- **`AUTH_MODE=production`**:
  - Every creator/mailbox-scoped request requires a real authenticated Principal
    (§8). There is **no** header, query param, or body field that re-enables the
    synthetic principal. The dev-principal code path is guarded by
    `AUTH_MODE=dev` and is unreachable otherwise.
- **Startup assertion:** the app **refuses to boot** if it detects a hosted
  context (e.g., production config present) together with `AUTH_MODE=dev`, and
  logs a loud auth-mode banner at startup in all modes.
- **Frontend:** the `mailbox_id` entry field and the "type an id to load" path
  are shown **only** in dev builds; the production build routes creators through
  login and mailbox-ownership selection (S18 §3). No production UI exposes raw
  mailbox-id entry.

This keeps the acceptance-critical property: **S17.2–S17.20 local behavior is
unchanged in dev mode; production cannot fall back to it.**

---

## 8. Authorization checks required on every creator endpoint

Every endpoint below must pass through the authorization layer **before** any
handler logic. `owner(mailbox)` = the §4 predicate (authenticated `creator`
Principal owns the mailbox, same tenant). Package routes resolve
`mailbox = package.mailbox` first, then apply `owner(mailbox)`; a package in
another tenant returns **404**.

| Method + path | Check | Deny → |
|---|---|---|
| `POST /handoff/{mailbox_id}` (create draft) | `owner(mailbox_id)` | 401/404 |
| `PATCH /handoff/{package_id}/scope` | `owner(package.mailbox)` + package `mutable` | 401/404/409 |
| `POST /handoff/{package_id}/generate` | `owner(package.mailbox)` + `mutable` | 401/404/409 |
| `GET /handoff/{package_id}` (creator view) | `owner(package.mailbox)` | 401/404 |
| `POST /handoff/{package_id}/publish` | `owner(package.mailbox)` + `mutable` (+ optional `approved_by`, deferred) | 401/404/409 |
| `POST /handoff/{package_id}/revoke` | `owner(package.mailbox)` **or** tenant `admin` (governance) | 401/404 |
| `POST /handoff/{package_id}/new-version` | `owner(package.mailbox)` | 401/404 |
| `GET /handoff/{package_id}/export.html` | `owner(package.mailbox)` + exports enabled (S18 §10) | 401/404 |
| `POST /cover-for-me/{mailbox_id}` | `owner(mailbox_id)` | 401/404 |
| `POST /gmail-ingest/{mailbox_id}/preview` | `owner(mailbox_id)` | 401/404 |
| `POST /gmail-ingest/{mailbox_id}/ingest` | `owner(mailbox_id)` | 401/404 |
| `GET /network-map/{mailbox_id}` (+ contact detail) | `owner(mailbox_id)` | 401/404 |
| `GET /projects/{mailbox_id}` and `/{project_id}` | `owner(mailbox_id)` | 401/404 |
| `GET /relationship-map/{mailbox_id}` | `owner(mailbox_id)` | 401/404 |
| `GET /source-message/{mailbox_id}` | `owner(mailbox_id)` | 401/404 |
| `GET /preflight?mailbox_id=…` | `owner(mailbox_id)` | 401/404 |
| `POST /synthesis/*` (mailbox-scoped) | `owner(mailbox)` | 401/404 |

Recipient endpoints (`POST /handoff/recipient/session`, `GET …/package`,
`POST …/ask`) are **excluded** from `owner()`; they use the shipped
session-token check and stay package-local (§5).

Implementation requirements:
- The check is a shared dependency (e.g., a FastAPI dependency) applied uniformly;
  no endpoint may opt out silently. Adding a new mailbox-scoped route without the
  dependency should fail a lint/test gate (define that gate in the S19 build).
- **Fail closed:** an unhandled/unknown route touching mailbox data denies by
  default.
- Checks run in `AUTH_MODE=dev` too, against the synthetic owner (§7), so the code
  path is exercised locally and cannot rot.

---

## 9. Audit events for auth-sensitive actions

Extend the existing handoff audit trail (lifecycle events already recorded) with
**auth-sensitive** events. All record **safe metadata only** — actor, action,
target id, tenant, timestamp, outcome — and **never** mailbox content, excluded
material, capability codes, or session tokens (S18 §8).

Event catalog (proposed):

| Event | When | Key safe fields |
|---|---|---|
| `auth.session.created` | A creator/admin login session is established | user_id, tenant_id, method |
| `auth.session.revoked` | Session ended / invalidated | user_id, tenant_id, reason |
| `authz.denied` | An authorization check failed | actor (if any), endpoint, target_id, reason |
| `mailbox.owner_bound` | A mailbox is bound to an owner + tenant (connect) | mailbox_id, owner_user_id, tenant_id |
| `mailbox.disconnected` | Owner/admin disconnects a mailbox | mailbox_id, actor |
| `role.granted` / `role.revoked` | Membership change | subject_user_id, role, granted_by |
| `admin.audit.viewed` | Admin/security reviewer reads audit metadata | actor, tenant_id, filter |
| `recipient.session.created` | One-time code redeemed (already effectively logged) | package_id, outcome (no code/token) |
| `package.revoked_by_admin` | Governance revoke (vs. owner revoke) | package_id, admin_user_id |

Existing package-lifecycle audit events (create/generate/publish/revoke/
new-version) are retained and gain the resolved `actor_user_id` + `tenant_id`
once identity exists. `authz.denied` is the key new signal for the later admin/
audit viewer and for security review (S18 §9).

---

## 10. What stays deferred to S20+ (OAuth / token-vault and beyond)

S19 defines *who you are* and *what you may touch*. It deliberately does **not**
define *how a mailbox's provider tokens are obtained or stored*:

- **Email-provider OAuth** (Gmail first, M365 later): consent flow, least-
  privilege scopes, redirect URIs, provider app verification — **S20** (S18 §4).
- **Token vault / storage** of provider access/refresh tokens outside the app DB;
  refresh + provider-side revocation handling — **S20** (S18 §4, open question 6).
- **Provider account-mismatch enforcement** at connect time — **S20** (S19 only
  requires that ownership binding *exists*, §3).
- **Production IdP selection + SSO wiring** for app login: S19 specifies the
  `Principal`/session **abstraction** and the authorization layer behind it;
  choosing and integrating the concrete IdP may be finalized alongside S20's
  provider work. S19 is implementable against a minimal session backend + the
  dev synthetic principal.
- **Manager/approver approval gating** — deferred (seam only, §2).
- **Admin audit UI** — **S23** renders §9's events; S19 only records them.
- **Recipient company accounts** — S18 open question 2, deferred.
- **Data-retention / employee-exit deletion** of tokens/packages/derived data —
  **S24** (S18 §8, open questions 4/10).

---

## 11. Downstream dependencies

> **Sprint numbering superseded by S21 §14.** The concrete implementation order is
> **S22 auth (implements this spec) → S23 OAuth/vault → S24 job infra → S25
> ingest→jobs → S26 enrichment→jobs → S27 hosted deploy**. Read the dependency
> notes below as *dependencies*, not sprint numbers — their older S22/S23/S24
> labels predate S21 §14, and S21 §14 wins.

- **S20 (OAuth / token vault)** depends on S19's `Mailbox.owner_user_id` +
  `tenant_id` binding — tokens attach to an owned mailbox.
- **S21 (jobs)** attaches `tenant_id` + `requested_by` to every job from S19's
  identity model.
- **S22 (hosted deploy)** provides the secrets/config that carry `AUTH_MODE` and
  the IdP/session backend.
- **S23 (admin/audit viewer)** renders §9's audit events and enforces §6's
  read-only, no-content boundary.
- **S24 (security/privacy hardening)** threat-models the §5 capability-code +
  session flow and the §7 dev/prod switch.

---

## Acceptance (this sprint)

- Docs/spec-only; **no** backend/frontend/schema/migration/dependency changes.
- `git diff --check` clean.
- **S17.2–S17.20 remains described as shipped**, and its behavior is unchanged
  (dev mode preserves the current localhost flow, §7).
- **S18 remains the docs-only hosted-readiness plan** and is cited as the source
  of truth throughout.
- **S19 is described as an auth/tenant spec, not implemented** — every entity,
  column, and check here is *proposed for the S19 build*, none exist yet.
- Specific enough that S20 (and the S19 build itself) can proceed from §3, §4,
  §7, and §8.
