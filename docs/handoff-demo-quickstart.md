# Handoff Demo Quickstart

Canonical investor-demo path. Follow the "Investor demo talk track" below for a
clean end-to-end walkthrough. For the complete terminal-by-terminal runbook, see
`docs/s17-handoff-manual-demo-runbook.md`.

## The three demo mailboxes

| Mailbox | ID | Best for | Expected behavior |
|---|---|---|---|
| Compact Handoff demo (`handoff-demo@example.com`) | `7996bc63-5739-4321-85b0-bb2664595e47` | Fast creator-to-recipient Handoff smoke test, and the canonical return-handoff demo | About 7 claims / 7 evidence cards; sensitive and noise content excluded. |
| Rich full-product demo (`rich-handoff-demo@example.com`) | `5faa306a-61d5-4e63-a270-8c4a0a0eaaca` | Investor / full-product walkthrough with more projects, people, threads, and evidence | 12 projects, 72 threads, 288 messages, 72 events; verification currently yields about 68 claims / 68 evidence cards. |
| Puluo graph/retrieval demo (`puluo1938@gmail.com`) | `e21c187a-956a-47ee-92aa-b21badd16f4d` | Cover-for-me, Relationship Map, Network Map, and Project View smoke testing | Real mailbox graph/retrieval demo. It has zero Handoff `Event` rows, so Handoff Generate is expected to show `no_events_for_mailbox`. |

Use this for / do not use this for:

- **Compact Handoff demo** - use for a quick creator-to-recipient smoke test and
  the canonical return-handoff demo. Do not use for the dense investor
  walkthrough (it is deliberately small).
- **Rich full-product demo** - use for the investor walkthrough: Overview,
  Projects, Relationship Map, Cover-for-me, and the Handoff creator/recipient
  project grouping. Do not use as the return-handoff source (see "Return handoff"
  below) unless intentionally testing a non-canonical path.
- **Puluo graph/retrieval demo** - use for Cover-for-me, Relationship Map,
  Network Map, and Projects on a real mailbox. Do not use for Handoff generation
  (it has no extracted events).

The IDs above are the current `ekc_dev` IDs (verified against `ekc_dev`). If your
local database was recreated, re-run the relevant seed script and use the printed
mailbox id. The `coverer-demo@example.com` return mailbox has no fixed id here; it
is created and printed by `scripts\seed_handoff_demo.py --verify`.

## Start the local stack

Open three PowerShell windows.

### Window 1 - setup / seed

You can close this window after the commands finish.

```powershell
cd C:\Users\PC\EmailArchive

docker compose up -d db

$env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev'
.\.venv\Scripts\python.exe -m alembic upgrade head

# Compact Handoff + return-handoff seed (prints Dana + Alex mailbox ids).
.\.venv\Scripts\python.exe scripts\seed_handoff_demo.py --verify

# Richer mailbox for the fuller product demo.
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

## Investor demo talk track (rich mailbox)

The clean end-to-end path for an investor / product walkthrough. Use the rich
mailbox `5faa306a-61d5-4e63-a270-8c4a0a0eaaca`.

1. **Load** the rich mailbox in the Workspace (paste the id into the Mailbox ID
   box, then Load).
2. **Overview** - confirm the mailbox feels populated.
3. **Projects** - scan the 12 seeded work areas (Nexus Auth Platform, Security
   Audit Remediation, Harbor Billing Migration, Atlas Data Pipeline, Mobile
   Checkout, Northwind SSO, Search Relevance, Partner API, and more).
4. **Relationship Map** (or **Network**) - show the people / domain context.
5. **Cover-for-me** - ask about a project by its full name (see "Cover-for-me
   requirements" below). Needs `ANTHROPIC_API_KEY`.
6. **Handoff** - create a **fresh** draft and Generate (see the fresh-package
   warning below).
7. **Confirm creator grouping / filtering** - the review groups claims and
   evidence by real project (S37), and the project filter narrows the review to
   one project.
8. **Publish** to a recipient (for example `cover@acme.dev`) and copy the
   one-time link.
9. **Recipient view** - open the link in a private / incognito window. Do not
   open it in the creator browser first; the code is one-time.
10. **Confirm S39 project grouping** - the recipient coverage rail groups by the
    frozen project label, and clicking a project changes the brief and its
    evidence.
11. **Optional S40 Ask** - exercise the recipient "Ask about this handoff" (see
    the deferred final QA checklist below).

> **Fresh-package warning.** Packages generated before S39 do not carry the frozen
> `project_label`, so recipient project grouping and the S40 Ask tests must use a
> **freshly generated** package. Always Generate a new package during the demo
> rather than reusing an older one.

## Deferred final QA - recipient Ask (S40)

**Deferred final demo QA, not a merge blocker.** Run these on a **fresh** rich
handoff package, in the recipient view, under "Ask about this handoff":

1. "What is the status of Nexus Auth Platform?"
2. "What are next steps for Nexus Auth Platform?"
3. "What is blocked for Harbor Billing Migration?"
4. "What decisions were made for Security Audit Remediation?"

Expected:

- Status and next steps produce **different** answers.
- Next steps returns only open loops / actions (it does not restate completed
  work as to-dos).
- Blocked returns blocker-shaped items or a truthful "no blockers found" - it does
  not invent blockers.
- Decisions are decision / outcome-focused.
- Supporting evidence stays collapsed under each answer item (a per-claim
  "Show N supporting messages" toggle), not dumped as a wall.

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
10. Open it in a private / incognito window. Do not open it first in the creator
    browser because the code is one-time.
11. Confirm the recipient view is read-only, has package-local Ask, and shows no
    mailbox id, Gmail/source link, raw code, token, exclusion counts, or live
    mailbox link.

## Flow B - rich full-product demo

Use mailbox `5faa306a-61d5-4e63-a270-8c4a0a0eaaca`. This mirrors the ordered
"Investor demo talk track" above with more detail per surface.

1. Load the mailbox in the Workspace.
2. Start on **Overview** and confirm the mailbox feels populated.
3. Open **Projects** and scan the 12 seeded work areas.
4. Open **Relationship Map** or **Network** to show people / domain context.
5. Open **Cover-for-me** and ask about a project **by its full name** - the router
   matches the whole project label as a phrase, so a partial name will not route.
   Use one of the seeded labels verbatim, for example:
   `What's the state of the Atlas Data Pipeline?`,
   `What is happening with the Nexus Auth Platform?`, or
   `Summarize the Harbor Billing Migration.` You can also ask about a person by
   name (for example `What is Mira Patel working on?`).
   See "Cover-for-me requirements" below - this path needs `ANTHROPIC_API_KEY`,
   and broad semantic questions additionally need embeddings.
6. Open **Handoff**, create a **fresh** draft, and generate.
7. Expect a much denser package than the compact seed: about 68 claims/evidence
   on verification, with realistic workplace subjects and project names grounded
   in message bodies.
8. Confirm the creator review groups claims and evidence by real project (S37) and
   that the project filter narrows to one project.
9. Use project/date scope to narrow the package before publish if the package is
   too large for the walkthrough.

This is the best mailbox for investor demos because it exercises the broader
product shape without requiring a real mailbox. Handoff generation, Projects,
Relationship Map, and Network are fully deterministic and need no external API.

### Cover-for-me requirements

Cover-for-me is the one rich-demo surface that reaches an external API, so plan
for it before a live walkthrough:

- **Synthesis needs `ANTHROPIC_API_KEY`.** With a key set, asking about a
  project or person **by full name** (see Flow B step 5) routes through L1 and
  produces a grounded answer with no embeddings required. Without a key the
  surface returns a typed "summaries not configured" state (HTTP 503), not a
  crash.
- **Broad semantic questions need embeddings.** A question that does not name a
  project/person falls back to L2 vector retrieval. The rich mailbox ships with
  **no** embeddings, so those questions return a graceful "run embed_backfill"
  message rather than an answer. To enable them, backfill embeddings once:

  ```powershell
  # Costs money and sends message text to Voyage AI. Run only with explicit
  # authorization for that run. Omit --dry-run to actually embed.
  .\.venv\Scripts\python.exe scripts\embed_backfill.py --mailbox 5faa306a-61d5-4e63-a270-8c4a0a0eaaca --dry-run
  ```

For a no-key, fully offline investor walkthrough, favor Handoff generation and
the project-name Cover-for-me prompts (with a key), and skip broad semantic Ask.

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

## Return handoff (use the compact seed)

Use the **compact** seed for the canonical return demo, **not** the rich seed. The
compact seed provisions two mailboxes and prints both ids via
`scripts\seed_handoff_demo.py --verify`:

- **Dana / original (covered):** `handoff-demo@example.com`
- **Alex / coverer (return):** `coverer-demo@example.com`

Steps:

1. Load **Dana's** mailbox; go to **Handoff** -> Create draft -> Generate.
2. **Publish the original** package to `coverer-demo@example.com`; note the
   original package id.
3. Load **Alex's** (coverer) mailbox id (printed by the seed); go to **Handoff**
   -> **Create a return handoff** -> paste the original package id -> Create.
   (You can also reach this from the recipient link's "Create return handoff",
   which routes into the authenticated Workspace carrying the original package id.)
4. Generate the return, review "what changed while Dana was away", and publish
   (recipient defaults to Dana). Expect about 4 delta claims (key rotation, SOC2
   close, wiki migration, Northwind SSO), with 1 sensitive and 1 noise excluded.
5. Open the return link: it reads as "Return handoff / what changed while you were
   away"; package-local Ask still works; no live mailbox links.

Notes:

- **Do not use the rich mailbox as the return source** unless you are
  intentionally testing a non-canonical path. The rich mailbox is not the compact
  seed's coverer mailbox, so its return output is arbitrary (often a single
  same-day event). That is mechanically valid but is not the intended return demo.
- **Default return window:** the original package's `published_at` through today.
  A small or same-day return result usually means the source mailbox has little
  coverer-side activity in that window, not a bug.
- The return package is reciprocal. It does not revoke, supersede, or mutate the
  original coverage package.
