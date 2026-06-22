# S8 Live Validation Results

**Date:** 2026-06-22
**Sprint:** S8 — Real-Mailbox Demo Readiness

---

## Mailbox

| Field | Value |
|---|---|
| Account | puluo smoke/live mailbox |
| Mailbox ID | e21c187a-956a-47ee-92aa-b21badd16f4d |
| Total messages | 460 |
| Voyage-4 embeddings | 67 (non-noise, sensitivity=none) |
| Excluded (noise + sensitive) | 393 |

Backfill was idempotent: a second dry-run after the live run showed
`already_embedded=67, to_embed=0`.

---

## S8 Task Status

| Task | Status | Notes |
|---|---|---|
| S8.1 Backfill validation | COMPLETE | 67 embeddings, idempotent |
| S8.2 Supporting evidence in response | COMPLETE | Subject/date chips in UI |
| S8.3 Operational preflight | COMPLETE | `scripts/preflight.py`, `GET /api/preflight` |
| S8.4 Graceful failure UX | COMPLETE | `retrieval_status` enum, six distinct states |
| S8.5 Smoke eval (voyage-4) | COMPLETE | 8/8 hard gates, MRR=0.900, top-1=0.800 |

---

## Live Cover-For-Me Query Validation

Tested against the live stack (uvicorn + Vite dev proxy) with
`VOYAGE_API_KEY` and `ANTHROPIC_API_KEY` configured, model
`claude-sonnet-4-6`.

### Q1 — Retrieval-positive, project thread

**Query:** `production P1 API latency spike database connection pool index`

**Result:** PASS
- Cited answer returned with multiple claims.
- Supporting evidence showed decoded subject: `INCIDENT P1: prod-api p99 latency spike — triaging` (em dash rendered correctly after MIME repair).
- No sensitive or noise messages in results.

### Q2 — Retrieval-positive, real mail (SSA)

**Query:** `Social Security benefit amount cost of living adjustment`

**Result:** PASS
- Cited answer returned.
- Supporting evidence showed decoded subject: `View Your New Benefit Amount Using Your my Social Security Account` (Q-encoded SSA subject decoded correctly after MIME repair).
- No sensitive messages in results.

### Q3 — Insufficient evidence (unanswerable sentinel)

**Query:** `xyzzy frobnicator spaghetti`

**Result:** PASS
- UI displayed: *"No email evidence found for this query. Try asking about a specific project or contact name from the mailbox."*
- No model call made; no hallucinated answer.

### Q4 — Sensitive content gate

**Query:** `H1 performance review Lattice`

**Result:** PASS
- UI displayed the no-evidence message (HR messages are excluded from embedding and SQL-filtered).
- No HR content exposed in response or supporting evidence.

---

## Post-Validation Repair Step

Existing DB rows ingested before the outbound subject decode fix (commit
`b4a1365`) may still hold raw RFC 2047 encoded-word subjects. Run the
repair script once per mailbox after deploying:

```bash
python scripts/repair_encoded_subjects.py \
  --mailbox-id e21c187a-956a-47ee-92aa-b21badd16f4d \
  --dry-run

# If the dry-run count looks right:
python scripts/repair_encoded_subjects.py \
  --mailbox-id e21c187a-956a-47ee-92aa-b21badd16f4d \
  --confirm
```

The script is safe to re-run; already-decoded rows are skipped.

---

## Known Follow-Up (Polish Only)

- **Repeated citation labels:** the same source message may appear as
  multiple citation chips when cited by more than one claim. Grouping
  or collapsing repeated chips would reduce visual noise. This is a UI
  polish item and does not affect correctness.

---

## Test Baseline at S8 Close

```
python -m pytest -q   →  452 passed, 79 skipped
npm.cmd --prefix frontend run build  →  358 kB JS, 17.8 kB CSS
```
