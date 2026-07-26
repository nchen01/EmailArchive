# S17 Handoff Package — Manual Demo Runbook

A step-by-step, copy-pasteable runbook for demoing the full audited handoff
package flow end to end: **seed → create → generate → review/prune → publish →
recipient (ask, refresh, consumed-link) → export → new-version/supersede.**

**Which mailbox to use**

- **Handoff generation demo → the seeded `handoff-demo` mailbox** (below).
  Real mailboxes only have handoff `Event` rows if the Anthropic-LLM event
  extraction has been run for them.
- **`puluo` (`e21c187a-956a-47ee-92aa-b21badd16f4d`) → Cover-for-me /
  Relationship Map / Network smoke only.** It has **zero `Event` rows**, so
  Handoff → Generate on it correctly yields an empty package and shows the
  `no_events_for_mailbox` diagnostic ("this mailbox has no extracted handoff
  events … widening the date range will not help"). That is expected, not a bug.

All commands assume the repo root and the blessed venv (`.venv\Scripts`). Never
use a bare `python`.

---

## Window 1 — setup / seed (one-off, then closeable)

```powershell
cd C:\Users\PC\EmailArchive

# 1. Seed the deterministic Handoff demo mailbox (no LLM, no Gmail, no key).
#    --verify also dry-runs a generate to confirm the mailbox is demo-ready,
#    with NO publish/token side effects.
$env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev'
.\.venv\Scripts\python.exe scripts\seed_handoff_demo.py --verify
```

Copy the printed **`mailbox_id`** — that is what you load in the UI. Expected:
6 threads / 9 messages / 9 events → **~7 claims / 7 evidence** on Generate, with
**1 sensitive** (comp review) and **1 noise** (newsletter) thread excluded.
`--verify` should print `verify : OK`.

The script is idempotent and only ever touches its own `handoff-demo@example.com`
mailbox — it never modifies puluo or any other mailbox.

---

## Window 2 — backend (keep open)

```powershell
cd C:\Users\PC\EmailArchive
.\scripts\run_backend.ps1
```

Uvicorn serves on `http://localhost:8000`; the Vite dev server proxies `/api`
there. `.env` supplies `DATABASE_URL` (point it at the same DB you seeded). Leave
this window running.

---

## Window 3 — frontend (keep open)

```powershell
cd C:\Users\PC\EmailArchive
.\scripts\run_frontend.ps1
```

Vite serves on `http://localhost:5173` (strictPort). Leave this window running.

---

## Creator click-through (in the main browser)

1. Open **`http://localhost:5173/app`**.
2. Paste the seeded **`mailbox_id`** into the **Mailbox ID** box → **Load**.
   (The mailbox is now remembered for this browser session, so a refresh keeps it.)
3. Go to the **Handoff** tab → **Create draft** (reason `vacation`, optional title).
   The URL becomes `/app/handoff/<package_id>`; refreshing it stays on the package.
4. (Optional) set a date scope, then **Generate package**. Expect ~7 claims
   grouped by kind and ~7 evidence cards; the amber exclusion summary shows the
   sensitive thread was withheld.
5. **Remove** one evidence card → it regenerates; a claim left with no evidence
   disappears. (This is the "prune" step.)
6. Enter a **recipient email** (e.g. `cover@acme.dev`), keep the 30-day expiry,
   click **Publish package**.
7. In the published panel, click **Copy link** and paste it somewhere safe. The
   link is `http://localhost:5173/handoff/recipient#c=<code>`. **The code is shown
   once** — there is no "open" button on the creator side (opening it would
   consume the one-time code before the recipient can).
8. Refresh the creator page: it stays **published**, scope/generate/remove are
   locked, and the panel honestly says the raw link cannot be recovered (use
   **Create revised version** if a new link is needed).

## Recipient click-through (in an **incognito / private** window)

9. Paste the copied recipient link into an incognito window. The package renders
   read-only: title, creator, reason, dates, privacy posture, an **"In this
   handoff"** navigation outline, grouped claims, and evidence cards. The `#c=…`
   fragment disappears from the address bar immediately.
10. In **Ask about this handoff**, ask something covered (e.g. *"What is the
    status of Nexus Auth?"*) → a grounded answer with package-evidence citations.
    Ask something off-topic (e.g. *"payroll reimbursement policy"*) → the neutral
    "doesn't include anything on that" reply.
11. **Refresh** the incognito tab → the package still renders (the short-lived
    session resumes from `sessionStorage`).
12. Open the **same link in a *fresh* incognito window** (empty sessionStorage) →
    neutral **"This handoff is no longer available"** (the one-time code is spent).

## Export + versioning (back in the creator browser)

13. On the published package, click **Export HTML** → downloads
    `handoff-package-v1.html`. Open it — it renders offline with the same content
    and **no** mailbox links, codes, or excluded (sensitive/noise) content.
14. Click **Create revised version** → lands on a new **draft (v2)** with the
    scope copied. Generate → Publish → copy the **fresh** link.
15. Reload the **v1** package (or the recipient's old link): v1 is now
    **superseded** and its old recipient link/session is blocked with the same
    neutral unavailable state.

---

## Cleanup notes

- Re-running `scripts\seed_handoff_demo.py` **resets** the demo mailbox's
  threads/messages/events (idempotent); handoff packages you created in the UI
  are left as-is. To clear them, delete the `handoff-demo@example.com` mailbox's
  packages, or drop/recreate the demo DB.
- The seed only touches its own mailbox; **puluo and other mailboxes are never
  modified.** No secrets or tokens are printed by any step here.
- For a real mailbox, Handoff generation requires running L1 event extraction
  first (Anthropic-LLM `services/enrich/events_llm.py`, needs `ANTHROPIC_API_KEY`)
  — out of scope for this demo and not required for the seeded mailbox.
