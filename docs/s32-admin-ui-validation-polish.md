# S32 — Admin UI Validation Polish

**Status:** implemented as a small frontend/docs polish pass after the S31 manual
validation.

## What Was Validated

Manual S31 testing confirmed the Admin / Audit Viewer could be opened in dev at
`/app/admin`, the package list/detail/audit surfaces rendered, a throwaway
published package could be revoked from the admin UI, and the revoked recipient
link returned the neutral unavailable state.

The production build also passed, and the production bundle contained no Admin
console strings (`Admin & Audit`, `Tenant governance`, `Revoke package`,
`Disconnect account`), confirming the `/app/admin` console is route-gated out of
production until production role-gated sign-in exists.

## Expected Empty States

The seeded Handoff demo mailbox is designed for package generation. It does not
necessarily create provider-account rows or job rows.

- **Providers:** `No provider accounts in this tenant` is expected unless a Gmail
  OAuth account has been connected. Provider disconnect should only be tested with
  a throwaway connected provider account.
- **Jobs:** `No jobs in this tenant` is expected unless Gmail date-range ingest or
  post-ingest pipeline jobs have been run. The seeded Handoff package flow itself
  does not enqueue background jobs.

S32 updates the UI empty states to say this directly so reviewers do not treat a
quiet seeded tenant as a broken admin surface.

## Polish

The admin package revoke success state is now more visible. After a successful
revoke, the package detail panel shows a clear `Package revoked` status banner
and notes that recipient access is blocked and the audit trail has refreshed.

## Scope

Frontend and docs only. No backend, schema, migration, dependency, recipient
route, provider-vault, or admin-permission changes.
