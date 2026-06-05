# Production Hardening Demo

Branch: `production-hardening-demo`

## Live Gmail Validation — 2026-06-05

End-to-end smoke test run against a throwaway Gmail account
(`johncartergpt2024@gmail.com`) using the real OAuth flow.

| Check | Result |
|---|---|
| OAuth token flow | PASS |
| Dry-run path | PASS |
| Capped baseline (--max-messages 1500) | PASS |
| Full baseline | PASS — 2161 messages / 2153 threads |
| Incremental sync | PASS — 1 new message fetched via stored token |
| Sync token saved after full baseline | PASS (fixed via `getProfile().historyId`) |
| Sync token saved after incremental | PASS |
| Token / body / subject leak in output | None observed |
| Audit start + finish rows | Clean pairs for every run |
| Audit retention after mailbox delete | Intentional — no FK cascade (spec 04) |

### Sync token fix

`users.messages.list` does not return `historyId` in its response.
`GmailProvider.list_ids()` now calls `users.getProfile(userId="me")` before
the listing loop to capture a reliable baseline `historyId`.
Capped runs (`--max-messages` hit) still do not save the token.

### Audit log retention

`audit_log.mailbox_id` carries no foreign key intentionally (spec 04):
audit rows are retained indefinitely after a mailbox is deleted.
Tests that write audit rows clean up after themselves explicitly.

## Verification

```
python -m pytest -q          # 161 passed
npm --prefix frontend build  # 357.13 kB bundle
```
