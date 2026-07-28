# S20 — OAuth + Token Vault (spec)

> **Docs/spec-only sprint.** No backend/frontend/schema/migration/dependency
> changes. This document defines how a production tenant user safely connects a
> **Gmail** mailbox, how OAuth credentials are stored / refreshed / revoked, and
> how token handling is kept out of the app DB and logs.

**Source docs (authoritative, in precedence order):** `docs/decisions.md` (D2
providers, D6 "OAuth tokens never touch the app DB or logs"), then
`docs/s18-hosted-product-readiness-plan.md` (§3 auth boundary, §4 OAuth model),
`docs/s19-auth-tenant-boundary-plan.md` (owner/tenant binding, `AUTH_MODE`), and
`README.md` / `AGENTS.md` for shipped status. Where they disagree, that order
wins.

**Status of the arc:**
- **S17.2–S17.20 — shipped** (the audited Handoff Package MVP; behavior unchanged
  by this doc).
- **S18 — shipped as a docs-only** hosted-readiness plan.
- **S19 — shipped as a docs-only** auth/tenant boundary spec.
- **S20 — this spec, NOT implemented.** Every object, field, and flow here is
  *proposed for the S20 build*; none exist yet. Today's token path is still the
  D6 env-var seam (`services/ingest/providers/gmail.py::get_token`).
- **S21+ — not started** (§12).

**Untouched invariant:** the recipient package view still reads **only
package-local snapshot rows** (S18 §7, S19 §5). OAuth and provider tokens are a
**creator-side connect** concern; a recipient session never touches provider
tokens, mailbox rows, or this flow. Nothing in S20 changes that.

---

## 1. Provider scope

- **Gmail is the only provider S20 specifies and (later) implements.** It
  replaces the current dev token seam (`get_token` reading `GMAIL_TOKEN_<id>` from
  the environment, D6) with a real authorization-code OAuth flow + token vault.
- **Microsoft 365 / Graph is explicitly out of scope for S20.** `providers/
  msgraph.py` remains the `NotImplementedError` stub (D2); the `MailProvider`
  protocol boundary keeps it a later drop-in. **S20 does not implement M365** —
  no M365 OAuth, no M365 vault entries, no M365 UI.
- The connected-account model (§4) is written provider-generically (a `provider`
  discriminator) so M365 slots in later without reshaping it, but only `gmail` is
  a valid value in S20.

---

## 2. OAuth flow (production Gmail connect)

A confidential **web-application** OAuth 2.0 **authorization-code** flow (with a
registered redirect URI on the hosted origin; the client secret lives in the
secrets manager, never in the frontend or app DB). Steps:

1. **Start.** An **authenticated creator** (`AUTH_MODE=production`, real
   Principal per S19) initiates "Connect Gmail" for a mailbox they will own.
   → audit `oauth.connect.started`.
2. **State/nonce.** The app mints a single-use, short-TTL `state` (CSRF token)
   and a `nonce`, **bound server-side to `{tenant_id, user_id, session_id,
   intended_mailbox}`**, stored server-side (not a bare signed blob the client
   can replay). `state` is opaque and unguessable.
3. **Redirect** to Google's authorization endpoint with the least-privilege
   scopes (§6), `access_type=offline` + `prompt=consent` (to obtain a refresh
   token), `state`, and PKCE (`code_challenge`).
4. **Callback.** Google redirects back with `code` + `state`. The app:
   - **Validates `state`** against server-side storage; rejects unknown / expired
     / already-used `state` (replay/CSRF → fail closed, §11).
   - Exchanges `code` (+ PKCE `code_verifier`) for tokens **server-side**.
5. **Verify provider account.** Resolve the connected Google account's verified
   email/subject (via the id-token / userinfo, §6). If it does not match the
   expected owner identity, **fail closed** (§5) → audit `oauth.account_mismatch`.
6. **Bind.** Create/update the `mailbox_provider_account` (§4) and the S19
   `Mailbox.owner_user_id` / `tenant_id` binding for the authenticated user +
   tenant. → audit `mailbox.owner_bound` (S19 §9) + `oauth.connect.succeeded`.
7. **Store tokens only in the vault** (§3). The **refresh token** goes to the
   vault; the app DB stores only a **`vault_ref`** + safe provider metadata. The
   authorization `code`, tokens, and raw provider responses are never persisted
   to the app DB or logged.
8. **Failure** at any step → audit `oauth.connect.failed` with a **safe error
   category only** (no code/token/response body), and no partial binding is left
   behind (transactional: bind + vault-write succeed together or roll back).

Connection **authorizes access only** — it does **not** ingest anything (§7).

---

## 3. Token vault boundary

**Principle (D6, non-negotiable):** raw OAuth access/refresh tokens **never**
touch the app DB, logs, audit metadata, exceptions, API responses, or frontend
state.

- **Vault = separate from the app DB where possible.** Recommended default: a
  managed **secrets manager / KMS-backed vault** (the S22 secrets manager).
  Acceptable interim: a dedicated, access-restricted store **outside** the normal
  app tables, with refresh tokens **envelope-encrypted** under a KMS-held key —
  never plaintext, never in a row a normal app query returns.
- **App DB stores only:** a `vault_ref` (opaque handle) + provider account
  **metadata** (§4). Never the access token, never the refresh token, never the
  client secret.
- **Access tokens** are short-lived. They may be **cached in memory / a short-TTL
  cache** for the duration of a job, but are **not persisted** to the app DB and
  are dropped on process exit. If a cache is used it must be per-tenant-scoped and
  never logged.
- **Redaction everywhere:** a central logging redactor drops any field named like
  a token/secret; provider HTTP clients must not log request/response bodies for
  the token endpoint. Exceptions raised through the token path carry categories,
  not values.
- **Rotation:** refresh-token rotation (if Google issues a new refresh token) is
  written to the vault, updating the same `vault_ref`; the old value is
  destroyed. Client secret rotation is an S22 secrets-manager procedure.
- **Revocation:** on disconnect/offboarding (§8), the app calls Google's token
  revocation endpoint **and** deletes the vault entry; the app-DB row is marked
  `revoked` (metadata only).
- **Backup/restore:** the vault is backed up **separately** from the app DB under
  its own encryption + access policy; app-DB backups contain only `vault_ref`s,
  which are inert without the vault. A restore must never resurrect a token that
  was revoked (revocation state lives in the app DB row and is authoritative).

The single code seam: today's `get_token(mailbox_id)` becomes a **vault-backed
resolver** that, given a `mailbox_provider_account`, returns a *short-lived access
token* (refreshing via the vault-held refresh token as needed) — callers
(`GmailProvider`, `gmail_windowed`) never see the refresh token.

---

## 4. Required DB objects / fields (proposed — not implemented in S20)

A new **`mailbox_provider_account`** (a.k.a. `connected_account`) object. Proposed
fields:

```
mailbox_provider_account
  id                     uuid  pk
  tenant_id              uuid  fk -> Tenant        (S19)
  owner_user_id          uuid  fk -> User          (S19; the connecting owner)
  mailbox_id             uuid  fk -> Mailbox       (S19 owner/tenant binding)
  provider               enum(gmail)               -- only 'gmail' valid in S20
  provider_account_email text                       -- verified connected address
  provider_account_id    text  null                 -- provider 'sub' if available
  vault_ref              text                       -- opaque handle to the vault; NOT a token
  scopes_granted         text[]                     -- exact scopes Google granted
  status                 enum(connected, refresh_failed, revoked, disconnected, mismatch_blocked)
  connected_at           timestamptz
  last_refresh_at        timestamptz null
  revoked_at             timestamptz null
  -- mismatch/diagnostic (safe metadata only)
  expected_account_email text null                  -- what the owner was expected to connect
  mismatch_reason        text null                  -- category, never raw provider data
  unique (tenant_id, mailbox_id, provider)          -- one live provider account per mailbox
```

**Placement — service DB only, NOT `ekc_schemas`.** Recommendation and rationale:
`ekc_schemas` is the **shared L0/L1 message/enrichment contract** generated across
services; a provider-connection/credential-reference object is a **service-
internal** concern of the API/ingest layer with **no cross-service contract
reason** to be shared, and it references secret-adjacent material. Keep it in
`services/db/` (new model + a future migration, in the S20 build), alongside the
S19 `Tenant`/`User`/`Mailbox` additions. Do **not** add token/credential shapes
to `ekc_schemas`. (If a future sprint needs a *safe* connection-status DTO for a
shared surface, expose a derived, token-free view then — not now.)

---

## 5. Provider account mismatch handling (fail-closed)

All cases **fail closed** with a **safe, non-leaking** message and an audit event;
no binding or token is persisted on a mismatch.

| Case | Behavior |
|---|---|
| Authenticated app user connects a **different** Gmail account than expected | Reject; do not bind; audit `oauth.account_mismatch` (safe: expected vs. connected **domain/email**, category only). Message: "The Google account you connected does not match your account. Connect the mailbox that belongs to you." |
| Callback email **≠ expected** owner email | Same as above — reject, no bind. |
| **Reconnecting an already-connected** mailbox (same owner, same account) | Allowed as a **re-consent/refresh**: update the existing row's `scopes_granted`/`last_refresh_at`, rotate the vault entry; do **not** create a duplicate; audit `oauth.scope_changed` if scopes differ, else `oauth.token_refresh.succeeded`. |
| Account already bound to **another tenant/user** | Reject hard; never rebind across tenants/users; audit `oauth.account_mismatch` (reason `cross_owner`). Return 404-style "not available" (no existence oracle across tenants, S19 §4). |
| Provider returns **no verified email** | Reject; cannot establish ownership; audit `oauth.connect.failed` (reason `no_verified_email`). |

Error messages shown to the user are generic and actionable; they never echo the
provider response, the other account's identity beyond what the user already
knows, tokens, or codes.

---

## 6. OAuth scopes (least privilege)

MVP requests the **minimum** needed to (a) verify the account and (b) read for
ingest. **No write/send scopes.**

| Purpose | Scope | Notes |
|---|---|---|
| Account identity / verification | `openid`, `https://www.googleapis.com/auth/userinfo.email` | Confirm the connected account's verified email/`sub` (§2 step 5, §5). |
| Read-only ingest (L0) | `https://www.googleapis.com/auth/gmail.readonly` | Bodies are needed for L0 normalization + evidence snapshots, so `gmail.metadata` is insufficient. **Restricted scope** — requires Google **app verification / security assessment** (S18 §4 "email provider review"); track that as an S20 external dependency. |
| Future send/write | *(none)* | **Not requested in MVP.** Any future `gmail.send`/modify scope requires an explicit, separately justified spec + consent-copy + security review; do not add speculatively. |

`access_type=offline` + `prompt=consent` obtain the refresh token; PKCE is used on
the code exchange. `scopes_granted` records exactly what Google returned (which
may be narrower than requested — handle downgrade gracefully, §8).

---

## 7. Date-range ingest integration (S16.0)

OAuth changes **authorization**, not the ingest UX:

- **Connecting only authorizes access.** It ingests nothing on its own.
- **Ingestion still requires the explicit S16.0 flow:** the owner picks a **date
  range**, runs **preview** (volume/estimate), then **confirms** ingest. This is
  unchanged from shipped S16.0 behavior; connect simply provides the access token
  the ingest job uses.
- **Date-windowed runs do not save sync tokens** (shipped S16.0 behavior); S20
  does not change that. Any future incremental-sync-token behavior is a separate
  spec.
- **Large-mailbox ingest is a background job (S21):** the connect flow must be
  fast/synchronous, but ingest/enrichment/backfill run on the worker with progress
  and resumability. The vault-backed token resolver (§3) is what the worker calls;
  the worker never holds the refresh token.

---

## 8. Token lifecycle

| Event | Behavior |
|---|---|
| **Initial connect** | §2; refresh token → vault, `vault_ref` + metadata → app DB, `status=connected`. |
| **Refresh** | Access token expired → resolver refreshes via vault-held refresh token; update `last_refresh_at`; audit `oauth.token_refresh.succeeded`. |
| **Refresh failure** | Mark `status=refresh_failed`; stop dependent jobs; surface a reconnect prompt to the owner; audit `oauth.token_refresh.failed` (category only). Do not delete the row (owner may reconnect). |
| **User disconnect** | Owner-initiated: revoke at Google + delete vault entry, `status=disconnected`, `revoked_at` set; audit `oauth.disconnect.requested` → `oauth.disconnect.completed`. |
| **Admin/security forced disconnect** | Admin governance action (S19 §6) — same revoke+delete, `status=disconnected`; audit with `actor=admin`. Admin **cannot** read tokens or mailbox content; they can only sever the connection. |
| **Provider revocation** (user/admin revokes at Google, or Google invalidates) | Detected on the next refresh/API 401; mark `status=revoked`; stop jobs; audit `oauth.provider_revoked`; prompt reconnect. |
| **Expired grant** | Treated as provider revocation; same handling. |
| **Scope downgrade** | If `scopes_granted` lacks `gmail.readonly`, connection is unusable for ingest; mark `refresh_failed`/prompt reconnect; audit `oauth.scope_changed`. |
| **Tenant offboarding** | All of the tenant's provider accounts revoked + vault entries deleted; rows marked `revoked`; ties into retention (S24). |
| **Employee departure** | The departing owner's mailbox connections revoked + vault-purged; packages/snapshots handled per retention policy (S18 open q.10, S24). Ownership does not silently transfer. |
| **Deletion / retention interaction** | Vault purge is immediate on revoke; app-DB metadata retention follows the S24 policy. A revoked row's `vault_ref` is inert; restore never resurrects a revoked token (§3). |

---

## 9. Authorization checks (build on S19)

- **Only the mailbox owner may connect their own mailbox** in MVP: the connect
  start + callback require an authenticated `creator` Principal, and the resulting
  binding sets `owner_user_id = principal.user_id` in the principal's tenant
  (S19 §4). No one connects a mailbox on another user's behalf in MVP.
- **Admin cannot silently connect or read a mailbox.** Admin may force-disconnect
  (§8) as governance, but cannot initiate a connect that binds a mailbox to
  someone, cannot read tokens, and cannot read mailbox content (S19 §6).
- **`AUTH_MODE=dev` bypasses real OAuth only in dev:** the dev/fixture path
  continues to use the D6 env-var / fixture token seam (no Google round-trip), so
  local demos keep working unchanged. This bypass is unreachable when
  `AUTH_MODE=production` (S19 §7, fail-closed).
- **Production refuses raw mailbox-id loading without authenticated owner
  context** (S19 §7): there is no production path that ingests or reads a mailbox
  from a typed id absent an owner Principal + a live provider connection.

---

## 10. Audit events (safe metadata only)

| Event | When |
|---|---|
| `oauth.connect.started` | Owner initiates connect (§2.1) |
| `oauth.connect.succeeded` | Binding + vault write committed (§2.6–7) |
| `oauth.connect.failed` | Any connect-flow failure (safe category) |
| `oauth.account_mismatch` | §5 mismatch cases |
| `oauth.token_refresh.succeeded` | Refresh ok (§8) |
| `oauth.token_refresh.failed` | Refresh failed (§8) |
| `oauth.disconnect.requested` | Disconnect initiated (owner/admin) |
| `oauth.disconnect.completed` | Revoke + vault purge done |
| `oauth.provider_revoked` | Provider-side revocation/expiry detected |
| `oauth.scope_changed` | Granted scopes differ from prior/expected |

**Safe fields only:** `tenant_id`, `user_id`, `mailbox_id`, `provider`,
`provider_account_email`/`domain`, `status`/error **category**, `scopes_granted`,
timestamps. **Never** token values, authorization codes, refresh/access tokens,
`state`/`nonce`/PKCE secrets, raw provider responses, stack traces, or email
content (S18 §8, D6). This extends the S19 §9 auth audit catalog.

---

## 11. Threat model (focused)

| Threat | Mitigation |
|---|---|
| **Stolen refresh token** | Never in app DB/logs; vault-only, envelope-encrypted under a KMS key; revocable at Google + vault purge; access tokens short-lived. |
| **Token accidentally logged** | Central log redactor; token-endpoint client bodies never logged; exceptions carry categories; review gate in the S20 build asserts no token field is serialized. |
| **OAuth `state`/CSRF** | Server-side, single-use, short-TTL `state` bound to `{tenant,user,session}`; unknown/expired/used `state` rejected. |
| **Confused deputy / account mismatch** | §5 verify-connected-account before bind; fail closed; no cross-user/tenant rebind. |
| **Cross-tenant mailbox binding** | Binding always uses the authenticated principal's tenant; `unique(tenant_id, mailbox_id, provider)`; account already owned elsewhere → hard reject (§5). |
| **Replayed callback** | `state` single-use + PKCE; authorization `code` is one-time at Google; replays rejected. |
| **Overbroad scopes** | Least-privilege set (§6); no write/send; `scopes_granted` recorded; downgrade handled; verification review gates the restricted `gmail.readonly`. |
| **Employee leaves company** | Departure revokes connections + purges vault (§8); retention policy governs derived data (S24). |
| **Provider revokes token silently** | Detected on next refresh/401 → `oauth.provider_revoked`, jobs stopped, reconnect prompt. |
| **Local dev mode enabled in a hosted deployment** | S19 fail-closed `AUTH_MODE` (unset/unknown ⇒ production; app refuses to boot hosted+dev); the OAuth-bypass path is only reachable under `AUTH_MODE=dev`. |

---

## 12. S21+ dependency map (not started)

> **Sprint numbering superseded by S21 §14.** The authoritative implementation
> sequence is **S22 auth → S23 OAuth/vault (implements this spec) → S24 job infra →
> S25 ingest→jobs → S26 enrichment→jobs → S27 hosted deploy** (admin/audit viewer
> + security/privacy hardening after). Read the entries below as *dependencies*,
> not sprint numbers — their older S22/S23/S24 labels predate S21 §14, and S21 §14
> wins.

- **S21 — Background job orchestration** for ingest / enrichment / embedding
  backfill / project materialization; the worker calls the §3 vault-backed token
  resolver and never holds a refresh token.
- **S22 — Hosted deployment / runbook**: provides the secrets manager that hosts
  the OAuth client secret and (recommended) the token vault + KMS key.
- **S23 — Admin / audit viewer**: renders §10 events; enforces S19 §6 (governance,
  no content/token read).
- **S24 — Security / privacy hardening**: threat-model review of §11, retention +
  employee-exit deletion (token/vault purge + derived-data policy).
- **S25 — Real-mailbox quality / event-extraction pipeline**: exercises the real
  Gmail read path at scale once connect + jobs exist.

None of these are started.

---

## Acceptance (this sprint)

- Docs/spec-only; **no** backend/frontend/schema/migration/dependency changes.
- New `docs/s20-oauth-token-vault-plan.md`.
- README / AGENTS / CLAUDE / implementation-plan updated with **pointer/status
  lines only**.
- `git diff --check` clean.
- **S20 is clearly not implemented** (every object/field/flow is proposed).
- **S17.2–S17.20 shipped; S18 docs-only readiness (doc) shipped; S19 docs-only
  auth/tenant spec (doc) shipped; S21+ not started** — stated in the status block.
- **Recipient package snapshot-only invariant untouched** (stated up top; OAuth is
  creator-side only).
- Specific enough that S21 (and the S20 build) can proceed from §2, §3, §4, §8.
