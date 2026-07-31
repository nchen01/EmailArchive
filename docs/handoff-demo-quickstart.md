# Handoff Demo Quickstart

Use this when you want to pick the right demo mailbox quickly before a product
walkthrough. For the complete terminal-by-terminal runbook, see
`docs/s17-handoff-manual-demo-runbook.md`.

## The three demo mailboxes

| Mailbox | ID | Best for | Expected behavior |
|---|---|---|---|
| Compact Handoff demo | `7996bc63-5739-4321-85b0-bb2664595e47` | Fast creator-to-recipient Handoff smoke test | About 7 claims / 7 evidence cards; sensitive and noise content excluded. |
| Rich Handoff demo | `5faa306a-61d5-4e63-a270-8c4a0a0eaaca` | Investor/full-product demo with more projects, people, threads, and evidence | 12 projects, 72 threads, 288 messages, 72 events; verification currently yields about 68 claims / 68 evidence cards. |
| Puluo | `e21c187a-956a-47ee-92aa-b21badd16f4d` | Cover-for-me, Relationship Map, Network Map, and Project View smoke testing | Real mailbox graph/retrieval demo. It has zero Handoff `Event` rows, so Handoff Generate is expected to show `no_events_for_mailbox`. |

If your local database was recreated, re-run the relevant seed script and use
the printed mailbox id. The IDs above are the current `ekc_dev` IDs.

## Start the local stack

Open three PowerShell windows.

### Window 1 - setup / seed

You can close this window after the commands finish.

```powershell
cd C:\Users\PC\EmailArchive

docker compose up -d db

$env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev'
.\.venv\Scripts\python.exe -m alembic upgrade head

# Compact Handoff + return-handoff seed.
.\.venv\Scripts\python.exe scripts\seed_handoff_demo.py --verify

# Optional richer mailbox for fuller product demos.
.\.venv\Scripts\python.exe scripts\seed_rich_handoff_demo.py --verify
```

Expected rich seed output: `12 projects / 72 threads / 288 messages / 72 events`
and `verify : OK`.

### Window 2 - backend

Keep this window open.

```powershell
cd C:\Users\PC\EmailArchive
.\scripts\run_backend.ps1
```

### Window 3 - frontend

Keep this window open.

```powershell
cd C:\Users\PC\EmailArchive
.\scripts\run_frontend.ps1
```

Open `http://localhost:5173/app` unless Vite prints a different port.

## Flow A - compact Handoff package

Use mailbox `7996bc63-5739-4321-85b0-bb2664595e47`.

1. Load the mailbox in the Workspace.
2. Go to **Handoff**.
3. Create draft with reason `vacation` and an optional title.
4. Generate package.
5. Expect about 7 claims and 7 evidence cards.
6. Remove one evidence card and confirm the unsupported claim disappears.
7. Use **Restore all removed evidence** to bring the count back.
8. Publish to `cover@acme.dev`.
9. Copy the recipient link.
10. Open it in a private/incognito window. Do not open it first in the creator
    browser because the code is one-time.
11. Confirm the recipient view is read-only, has package-local Ask, and shows no
    mailbox id, Gmail/source link, raw code, token, exclusion counts, or live
    mailbox link.

## Flow B - rich full-product demo

Use mailbox `5faa306a-61d5-4e63-a270-8c4a0a0eaaca`.

1. Load the mailbox in the Workspace.
2. Start on **Overview** and confirm the mailbox feels populated.
3. Open **Projects** and scan the 12 seeded work areas.
4. Open **Relationship Map** or **Network** to show people/domain context.
5. Open **Cover-for-me** and ask a grounded question, for example:
   `What is happening with Nexus Auth?`
6. Open **Handoff**, create a draft, and generate.
7. Expect a much denser package than the compact seed: about 68 claims/evidence
   on verification, with realistic workplace subjects and project names grounded
   in message bodies.
8. Use project/date scope to narrow the package before publish if the package is
   too large for the walkthrough.

This is the best mailbox for investor demos because it exercises the broader
product shape without requiring a real mailbox or external API calls.

## Flow C - puluo graph/retrieval demo

Use mailbox `e21c187a-956a-47ee-92aa-b21badd16f4d`.

1. Load the mailbox in the Workspace.
2. Use **Cover-for-me** for realistic mailbox questions.
3. Use **Relationship Map**, **Network**, and **Projects** for the populated real
   mailbox graph/project surfaces.
4. Do not use puluo as the Handoff generation demo unless you are intentionally
   showing the empty-generation diagnostic.

Expected Handoff behavior on puluo: Generate returns an empty package with
`no_events_for_mailbox`, because puluo has no extracted Handoff `Event` rows.
That is expected and is not a failure.

## Return handoff note

Return handoff uses the compact seed's second mailbox, `coverer-demo@example.com`.
That id is printed by `scripts\seed_handoff_demo.py --verify`. Use it only for
the return-coverage flow:

1. Publish a normal package from the compact Handoff demo mailbox to
   `coverer-demo@example.com`.
2. Open the recipient link as the coverer.
3. Click **Create return handoff**.
4. In the authenticated Workspace, load the printed coverer mailbox id.
5. Create the return handoff, generate, publish, and confirm it is framed as
   "what changed while you were away."

The return package is reciprocal. It does not revoke, supersede, or mutate the
original coverage package.
