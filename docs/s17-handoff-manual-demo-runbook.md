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

> **The two demos are complementary, not interchangeable.** The `handoff-demo`
> mailbox seeds messages + events only, **not** the L1 identity/edge graph — so
> its **Network** and **Relationship Map** tabs show an empty "No contacts yet"
> state (the API returns an empty graph, not an error). That is expected. Use
> `puluo` for a populated Network / Relationship Map / Cover-for-me demo, and the
> `handoff-demo` mailbox for the Handoff package flow.
>
> **Overview readiness is whole-workspace, not handoff-generation, readiness.**
> On the `handoff-demo` mailbox the Overview strip can read People = 0,
> Projects = 0, and Retrieval unavailable/limited **even though Handoff generation
> works** — because that mailbox is seeded for handoff package generation
> (messages + events), not the full L1 graph/projects/embeddings profile. This is
> expected and does not block publishing. The Handoff tab now carries an inline
> "Why does Overview show 0 people / projects?" note saying the same thing.

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

**Auth mode (S22).** `run_backend.ps1` defaults the local launcher to
`AUTH_MODE=dev`, so a fixed dev principal owns the local/demo mailboxes and the
"type a mailbox id and load" workflow works without a login. The launcher prints
`auth   : AUTH_MODE=dev` at startup.

**Access-log redaction (S23).** The Gmail OAuth callback
(`GET /api/oauth/gmail/callback?code=…&state=…`) carries the authorization code in
the query string. `services/api/main.py` installs a filter on the `uvicorn.access`
logger that replaces `code`/`state`/token query-param values with `REDACTED`, so
the code never reaches stdout/logs. A hosted deployment must keep this filter and
must **not** add raw request/query logging that would re-expose it.

**Optional — verify the production fail-closed boundary.** With
`AUTH_MODE=production` (or unset — the fail-closed default), every creator/mailbox
route must reject unauthenticated calls with **HTTP 401**. `Invoke-RestMethod`
hides the status code in its exception, so use `Invoke-WebRequest` with a
try/catch to see it:

```powershell
try {
  Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/projects/7996bc63-5739-4321-85b0-bb2664595e47" -UseBasicParsing
} catch {
  "status: $($_.Exception.Response.StatusCode.value__)"
  "body: $($_.ErrorDetails.Message)"
}
```

Expected (this is a **PASS**):

```
status: 401
body: {"detail":"Authentication required."}
```

`401 Authentication required.` is the correct S22 behavior — production has no
login wired yet (that lands with the OAuth slice), so it fails closed. Recipient
package routes are **not** affected: they keep their capability-code + session
auth and stay package-local snapshot only.

---

## Window 2b — background worker (only for Gmail date-range ingest)

The core handoff demo (seeded mailbox) needs **no** worker. But since S25/S26, the
Gmail **date-range ingest** and the **post-ingest pipeline** (Status → "Post-ingest
pipeline": enrichment / event extraction / embedding backfill / project
materialization) enqueue background jobs instead of running inline — so to run any
of those you also need a worker draining the queue:

```powershell
cd C:\Users\PC\EmailArchive
.\.venv\Scripts\python.exe -m scripts.run_worker            # loop; Ctrl+C to stop
# or: .\.venv\Scripts\python.exe -m scripts.run_worker --once   # drain then exit
```

The worker uses the same `AUTH_MODE` / `DATABASE_URL` as the API, resolves Gmail
credentials via the S23 vault seam (env token under `AUTH_MODE=dev`), and logs only
safe job metadata (never tokens, OAuth codes, provider responses, or content). The
frontend polls `GET /api/jobs/{id}` for status/progress.

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

---

## Return handoff / coverage delta demo (S34)

A **return handoff** is reciprocal: after coverage ends, the coverer (Alex) hands
work back to the original employee (Dana) from **Alex's own mailbox** — it is not a
new version of Dana's package. The seed script now creates **two** mailboxes.

**Seed (same day you demo — the coverer activity is dated "today" so it falls in the
default return window):**

```powershell
$env:DATABASE_URL='postgresql+psycopg2://ekc:...@localhost:5432/ekc_dev'
.\.venv\Scripts\python.exe -m scripts.seed_handoff_demo --verify
```

The output prints both ids:
- **original (Dana / covered)** → `handoff-demo@example.com`
- **coverer (Alex / return)** → `coverer-demo@example.com`

Expected: original ~7 claims / 7 evidence; return **~4 claims** (Nexus Auth key
rotation, SOC2 MFA closure, wiki-migration open loop, Northwind SSO), with **1
sensitive + 1 noise item excluded** (both inside a carried Nexus Auth area, so the
gates are demonstrably at work).

> **Date-window note (important):** the return's default coverage window is the
> original package's `published_at` date → **today**, and the coverer's activity is
> dated the **day you seed**. So **seed and run the return demo on the same day.** If
> you demo on a later day, publishing the original (dated that later day) yields a
> window that no longer contains the coverer activity and the return **Generate will
> come back empty** — just **re-run the seed** and try again. Note that
> `--verify` dry-runs generation with an *empty* scope (no date window), so a green
> `--verify` does **not** rule out an aged-out return window; an empty return Generate
> is the signal to re-seed.

**Manual steps:**

1. **Publish the original.** Load Dana's mailbox id → **Handoff** → *Create draft* →
   *Generate* → *Publish* to `coverer-demo@example.com`. Copy the **original package
   id** (it is in the `/app/handoff/<id>` URL).
2. **Create the return.** Load Alex's mailbox id (paste the coverer id) → **Handoff**
   → **"Create a return handoff"** panel → paste the original package id → *Create
   return handoff*. (This is reciprocal, distinct from "Create a revised version".)
3. **Review.** The **Return handoff** banner shows the carried coverage areas
   (Nexus Auth, Security Audit) preselected automatically, the coverage window
   (Dana's publish date → today), and that claims come from **your mailbox only**.
   Copy reads *"what changed while Dana was away / remove anything that should not
   travel back."* Click *Generate* → review the ~4 delta claims → *Publish* (the
   recipient defaults to `handoff-demo@example.com`, the original creator).
4. **Open the return link.** It reads **"Return handoff — what changed while you were
   away."** The package-local **Ask** still works, evidence is attached to claims, and
   there are no live mailbox / Gmail / source links. Sensitive + noise content is
   absent from the recipient payload.

**Mailbox roles:** `handoff-demo@example.com` = Dana / original covered employee;
`coverer-demo@example.com` = Alex / coverer who returns the delta. The original
outbound package is **never** revoked, superseded, or mutated by publishing the return.
