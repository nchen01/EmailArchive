# S17 Handoff Demo Quickstart

Use this short runbook when you only need to restart the local Handoff demo.
For the full click-through, see `docs/s17-handoff-manual-demo-runbook.md`.

## Mailboxes

- Handoff demo mailbox: `7996bc63-5739-4321-85b0-bb2664595e47`
- Puluo mailbox: `e21c187a-956a-47ee-92aa-b21badd16f4d`

Use the Handoff demo mailbox for Handoff package generation. It is seeded with
Event rows and should generate about 7 claims and 7 evidence cards.

Use puluo for Cover-for-me, Relationship Map, and Network Map smoke testing.
Puluo has zero Event rows, so Handoff generation on puluo is expected to return
the `no_events_for_mailbox` diagnostic.

## PowerShell Window 1 - setup / seed / checks

You can close this window after the commands finish.

```powershell
cd C:\Users\PC\EmailArchive

git pull

docker compose up -d db

.\scripts\check_local_env.ps1

.\.venv\Scripts\alembic.exe upgrade head

$env:DATABASE_URL = "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev"

.\.venv\Scripts\python.exe scripts\seed_handoff_demo.py --verify

$env:EKC_MAILBOX_ID = "7996bc63-5739-4321-85b0-bb2664595e47"

.\.venv\Scripts\python.exe -m scripts.preflight --mailbox-id $env:EKC_MAILBOX_ID
```

Expected note: preflight can show `embeddings` as `FAIL` for the seeded Handoff
mailbox. That is acceptable for this Handoff demo because package generation
uses seeded L1 `Event` rows, not L2 embeddings.

## PowerShell Window 2 - backend

Keep this window open.

```powershell
cd C:\Users\PC\EmailArchive

.\scripts\run_backend.ps1
```

## PowerShell Window 3 - frontend

Keep this window open.

```powershell
cd C:\Users\PC\EmailArchive

.\scripts\run_frontend.ps1
```

Open the printed frontend URL, usually `http://localhost:5173/app`. If Vite
prints a different port, use the printed URL.

## Browser flow

1. Load mailbox `7996bc63-5739-4321-85b0-bb2664595e47`.
2. Open the Handoff tab.
3. Create a draft with reason `Vacation coverage` and title `Manual demo handoff`.
4. Generate package. Expect about 7 claims and 7 evidence cards.
5. Remove one evidence card and confirm the related unsupported claim disappears.
6. Publish to recipient email `cover@acme.dev`.
7. Copy the recipient link. Do not open it in the creator browser first because
   the code is one-time.
8. Open the recipient link in an incognito/private browser window.
9. Confirm the recipient package renders read-only, with no mailbox id, exclusion
   counts, Gmail/source links, raw code, or session token.
10. Ask `What is the status of Nexus Auth?` and expect a grounded answer.
11. Ask `payroll reimbursement policy` and expect a neutral no-evidence answer.
12. Refresh the recipient tab and confirm the package reloads from sessionStorage.
13. Open the same link in a fresh incognito window and expect the neutral
   unavailable state because the one-time code is spent.
14. In the creator browser, export HTML and confirm the file opens offline.
15. Create a revised version, generate, publish, and confirm the new link works
   while the old link/session is blocked.
