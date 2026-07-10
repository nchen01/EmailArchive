# S15 — MVP Verification Matrix

The one canonical place that defines what **"green" means** for this repo in
different environments. Use it to verify the app without reading chat history.

There is **no single universal test count**. "Green" depends on what you can
reach (no DB, a local Postgres, the demo mailbox, or paid live APIs), so this
doc defines four **green tiers** plus a manual UI checklist and a cleanup policy.

All commands are **PowerShell** and use the blessed venv interpreter
`.\.venv\Scripts\python.exe` — never a bare `python` on PATH (S10).

## Tiers at a glance

| Tier | Needs | Proves | Live keys? |
|---|---|---|---|
| 1. Minimum local green | venv only | Code imports + logic + FE build | No |
| 2. DB-gated green | + Postgres (throwaway `ekc_test`) | Backend correctness end-to-end | No |
| 3. Demo mailbox green | + seeded `puluo` in `ekc_dev` | Demo data is ready | No (read-only preflight) |
| 4. Live integration green | + `VOYAGE_API_KEY` / `ANTHROPIC_API_KEY` | Paid embedding + synthesis paths | **Yes (billed)** |
| 5. Frontend / manual UI | running stack | Product-demo confidence | Cover-for-me needs keys |

Live integration (tier 4) is **not** required for a normal local dev run.

---

## Current known baseline (2026-07-10)

Baselines are environment-specific and dated so they don't rot silently. Re-run
and update the date/commit when the suite changes materially.

- **Minimum local green (no DB, no keys):** `481 passed, 115 skipped` — measured
  on branch `s15.2-verification-matrix`. The 115 skips are DB-gated tests that
  skip when `DATABASE_URL` is unset.
- **DB-gated green (throwaway `ekc_test`):** `594 passed, 2 skipped, 0 failed,
  0 errors` — this figure requires the S15.1 fix (`fix(tests): isolate S9
  materialization DB state`, commit `3d42367`, PR #3). Before S15.1 the full
  suite reported S9/`test_synthesis` contamination (2 failed / 6 errors).

Local no-DB runs and DB-gated runs have **different** counts by design — do not
treat one number as valid everywhere.

---

## Tier 1 — Minimum local green

**Purpose:** quick sanity with no Postgres and no live API keys. FakeEmbedClient
is the default for all offline tests, so nothing bills.

```powershell
# 0. Confirm branch + interpreter
git rev-parse --abbrev-ref HEAD
.\.venv\Scripts\python.exe --version          # expect Python 3.12.x

# 1. Backend tests that do NOT require external APIs or Postgres.
#    Leave DATABASE_URL unset so DB-gated tests skip cleanly.
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q

# 2. Frontend build (tsc + vite)
npm.cmd --prefix frontend run build
```

**Expected:** `481 passed, 115 skipped` (see baseline; count moves as tests are
added). The frontend build prints `built in …` and emits `frontend/dist/`.

**Expected skips/warnings:** the 115 skips are the DB-gated backend tests
(they self-skip on "DATABASE_URL not set or Postgres unreachable"). A
`StarletteDeprecationWarning` from the FastAPI TestClient is expected and
harmless.

---

## Tier 2 — DB-gated green

**Purpose:** backend correctness against a real Postgres + pgvector.

> **Run the full suite against a throwaway database, never `ekc_dev`.** The
> suite creates and deletes mailboxes, and a DB whose name ends in `_test`
> is auto-wiped at session start (conftest). `ekc_dev` holds the seeded `puluo`
> demo data — see the Cleanup policy.

```powershell
# 1. Start Postgres (pgvector/pgvector:pg16), service name "db"
docker compose up -d db

# 2. Create the throwaway test DB once (safe to re-run; ignore "already exists")
docker exec emailarchive-db-1 psql -U ekc -d ekc_dev -c "CREATE DATABASE ekc_test;"

# 3. Point at the throwaway DB and apply migrations
$env:DATABASE_URL = "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_test"
.\.venv\Scripts\python.exe -m alembic upgrade head       # -> head is 0006_message_embedding

# 4. Full suite
.\.venv\Scripts\python.exe -m pytest -q
```

**Expected (with S15.1 applied):** `594 passed, 2 skipped, 0 failed, 0 errors`.

**The two intentional skips (both expected, neither is a failure):**

1. `tests/test_s14_source_message.py` — the whole-thread endpoint test skips with
   *"fixture has no non-sensitive sibling in a sensitive thread"* when the
   fixture mailbox does not naturally contain that shape. That exact case is
   covered deterministically by the synthetic test
   `test_source_message_whole_thread_exclusion_synthetic`, so the skip is safe.
2. `tests/test_s7_embed_client.py` — the **live Voyage** integration test skips
   with *"VOYAGE_API_KEY not set"*. This is skip-guarded on purpose so CI and
   local runs never make a billed call (CLAUDE.md Voyage authorization rule).
   It only runs under Tier 4.

If you see failures/errors clustered in `test_s9_materialize.py` and
`test_synthesis.py`, you are on a branch without the S15.1 fix.

---

## Tier 3 — Demo mailbox green

**Purpose:** prove the `puluo` demo mailbox is ready for a product demo. This is
**read-only** and makes **no billed API calls** (preflight constructs the Voyage
client but does not call it without `--live-embed`).

- Demo mailbox id: **`e21c187a-956a-47ee-92aa-b21badd16f4d`** (lives in `ekc_dev`).

```powershell
# Point at the demo DB (ekc_dev) — read-only for preflight
$env:DATABASE_URL = "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev"
.\.venv\Scripts\python.exe -m scripts.preflight --mailbox-id e21c187a-956a-47ee-92aa-b21badd16f4d
```

**Expected preflight categories (all `[pass]`, plus one `[info]`):**

- `voyage_api_key` — VOYAGE_API_KEY configured (from `.env`).
- `anthropic_api_key` — ANTHROPIC_API_KEY configured.
- `database` — reachable.
- `alembic_head` — at migration head (`0006_message_embedding`).
- `embeddings` — **voyage-4 embeddings exist for this mailbox** (puluo currently
  reports ~67). This must be > 0 for demo readiness; if it fails, run
  `scripts/embed_backfill.py` (Tier 4, billed).
- `embed_client` — Voyage client constructs OK (no live call unless `--live-embed`).
- `enable_reranking` — off (correct for MVP).
- `voyage_rate_limits` — `[info]` only.

Exit code 0 = "Preflight OK." To start the demo stack (two PowerShell windows):

```powershell
# Backend (validates env, runs preflight, then uvicorn on :8000)
.\scripts\run_backend.ps1 -MailboxId e21c187a-956a-47ee-92aa-b21badd16f4d

# Frontend (Vite on :5173, strictPort) — second window
.\scripts\run_frontend.ps1 -MailboxId e21c187a-956a-47ee-92aa-b21badd16f4d
```

`.env` supplies `DATABASE_URL` / `VOYAGE_API_KEY` / `ANTHROPIC_API_KEY` to the
backend automatically (loaded in `services/api/main.py`).

---

## Tier 4 — Live integration green

**Purpose:** exercise the paid/live paths (real embeddings and real synthesis).
**These commands cost money and require explicit authorization** (CLAUDE.md).

- Requires `VOYAGE_API_KEY` for live embedding calls (`embed_backfill.py`, the
  live embed test, `preflight --live-embed`, and any real Cover-for-me retrieval).
- Requires `ANTHROPIC_API_KEY` for L3 synthesis (Cover-for-me answer generation,
  project/contact summaries).
- `ENABLE_RERANKING` must stay **off** (unset or `0`) unless you are
  deliberately validating the optional, deferred S7.12 hosted Voyage reranker.
- **Production** needs a Voyage AI payment method for standard rate limits; the
  free tier (3 RPM / 10K TPM) is only appropriate for fixture/demo-scale runs.

```powershell
# Live Voyage credential probe (ONE tiny billed embed call)
$env:DATABASE_URL = "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev"
.\.venv\Scripts\python.exe -m scripts.preflight --mailbox-id e21c187a-956a-47ee-92aa-b21badd16f4d --live-embed

# The live Voyage integration test (skipped in Tiers 1-2) runs only when the key is set
.\.venv\Scripts\python.exe -m pytest -q tests\test_s7_embed_client.py -k live
```

Live integration is **not** part of a routine local dev loop — only run it when
intentionally validating billed services.

---

## Tier 5 — Frontend / manual UI green

**Purpose:** product-demo confidence. No frontend test runner exists, so the UI
is verified by `npm run build` (Tier 1) plus this manual checklist against the
running demo stack (Tier 3). Cover-for-me answer generation needs live keys
(Tier 4); the rest is DB-only.

Load `http://localhost:5173/` and walk through:

- [ ] Landing page loads.
- [ ] App loads the mailbox (`/app`).
- [ ] Overview loads (counts, readiness, suggested questions, top projects).
- [ ] Network Map loads.
- [ ] Relationship Map — **owner tree** loads with the owner at the root.
- [ ] Relationship Map — **project tree**: structural edges (project / thread /
      domain) show a provenance note instead of fabricated Message-IDs.
- [ ] Cover-for-me — a cited answer renders and a citation chip opens the
      evidence drawer (subject / sender / date / snippet / Message-ID).
- [ ] **Copy Message-ID** works in the drawer.
- [ ] **Search in Gmail** appears for gmail mailboxes and is understood as
      *best-effort* (an rfc822msgid search in the signed-in account), not an
      exact-email guarantee.
- [ ] A sensitive / HR-style query does **not** expose source content, and the
      source-message endpoint 404s for excluded headers.
- [ ] Network Map still works unchanged after using the other tabs.

---

## Cleanup policy

- **`ekc_test` is throwaway.** Drop it after verification whenever you like.
- **`ekc_dev` / `puluo` demo data must not be dropped** without explicit product
  approval — it holds the seeded demo mailbox and its embeddings.
- The Postgres **container may stay up** during an active sprint's verification.
- **Any destructive cleanup command must name the exact database.** Never run a
  wipe or `DROP DATABASE` without the DB name spelled out, and never target
  `ekc_dev`.

```powershell
# Safe: drop only the throwaway test DB
docker exec emailarchive-db-1 psql -U ekc -d ekc_dev -c "DROP DATABASE IF EXISTS ekc_test;"

# Optional: stop the container (keeps the pgdata volume, incl. ekc_dev/puluo)
docker compose stop db
```
