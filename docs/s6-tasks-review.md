# S6 Task Breakdown — Reviewer Sign-Off

Source spec: `docs/s6-real-mailbox-quality-pass.md`

This document lists every proposed task for S6, the open questions on each,
and the dependency chain. Add comments inline or in the **Reviewer notes**
section of each task before implementation begins.

---

## Dependency chain

```
S6.1 (report script)
  └── S6.2 (label template)
        └── S6.3 (eval script)
              └── S6.4 (L0 tuning)    ← only if eval reveals issues
              └── S6.5 (identity/graph inspection)
                    └── S6.6 (optional clustering diagnostic)
                          └── S6.7 (docs update)
```

S6.4 and S6.5 can run in parallel once S6.3 is done.
S6.6 is explicitly optional and gated on S6.1–S6.5 showing healthy signal.
S6.7 is a wrap-up step that closes the sprint.

---

## S6.1 — Redacted Live Quality Report Script

**Builds:** `scripts/live_quality_report.py`

**What it does:** Reads an already-ingested mailbox from Postgres (no Gmail
call) and writes privacy-safe CSV/JSON reports to `.local/reports/`.

**Outputs:**

| File | Contents |
|---|---|
| `summary.json` | Counts, distributions, domain stats, graph density |
| `noise_samples.csv` | Sampled messages with noise/sensitivity labels, no raw content |
| `sensitivity_samples.csv` | Sampled sensitive messages, no raw content |
| `identity_samples.csv` | Per-person merge stats, domain only |
| `edge_samples.csv` | Per-person edge weights and counts |
| `thread_samples.csv` | Per-thread shape metrics |
| `label_template.csv` | Blank label file keyed on `sample_id` (input to S6.2) |

**Key constraints:**
- `--hash-ids` defaults true; raw email addresses and message headers never
  appear in output
- `--seed` makes sampling deterministic; same seed → byte-identical reports
- `--include-sensitive` defaults false; sensitive-tagged messages are excluded
  from CSV samples unless explicitly opted in

**Open questions for reviewer:**

1. Should `summary.json` be safe to commit as a snapshot, or is even domain
   distribution data too sensitive for a real work mailbox? (For the throwaway
   corpus this is low risk; worth deciding the policy now.)
2. `--sample-size` defaults 50 per bucket. Is 50 enough to get a useful noise
   precision estimate, or should the default be higher?
3. Should the script warn (not fail) if the mailbox has fewer messages than
   `--sample-size`, or error out?

**Acceptance criteria:**
- Script runs against the live throwaway mailbox and produces all six output
  files.
- No raw subjects, bodies, addresses, Message-IDs, or token values appear in
  any output file with default flags.
- Re-running with the same `--seed` produces byte-identical output.
- Unit tests cover: hashing/redaction logic, deterministic sampling, empty
  bucket handling.

**Reviewer notes:** _(add comments here)_

---

## S6.2 — Local Label Template Workflow

**Builds:** Label workflow convention + `label_template.csv` emitted by S6.1.

**What it does:** Defines the local-only labeling file pattern and column
schema that a human fills in after inspecting messages directly in the DB or
mailbox client.

**Label file location:** `.local/labels/live-mailbox-labels.csv` (git-ignored)

**Label schema:**

| Column | Allowed values |
|---|---|
| `sample_id` | From report (links back to `noise_samples.csv`) |
| `actual_noise` | `noise` / `not_noise` / `unsure` |
| `actual_sensitivity` | `none` / `hr` / `legal` / `privileged` / `personal` / `unsure` |
| `project_like` | `yes` / `no` / `unsure` |
| `notes` | Free text, local only |

**Open questions for reviewer:**

1. Is `project_like` the right column name, or should it be `project_signal`
   to make it clear this is a weak heuristic label, not ground truth?
2. Should the template include a `reviewed_by` column to track who labeled
   what, in case multiple people share a labeling pass?
3. The workflow requires a human to look up message content in the DB or
   mailbox directly. Is that acceptable friction, or should S6.1 emit a
   separate local-only file (never committed) with raw content to make
   labeling faster?

**Acceptance criteria:**
- `label_template.csv` is emitted deterministically by S6.1 alongside the
  other reports.
- `.local/` is confirmed git-ignored (already done as of `8ee8101`).
- No raw content appears in any committed artifact.

**Reviewer notes:** _(add comments here)_

---

## S6.3 — Noise and Sensitivity Eval Script

**Builds:** `scripts/eval_live_quality.py`

**What it does:** Reads the S6.1 reports and the S6.2 label file and computes
precision/recall metrics. Enforces hard S6 checks; reports soft targets
without failing on them.

**Hard checks (script must fail on these):**

| Check |
|---|
| No raw private content committed |
| Reports are deterministic (same seed → byte-identical) |
| Malformed label file → eval fails, not silently skips |
| No sensitive false negatives in the reviewed sample |
| No undocumented project-like messages wrongly dropped as noise |

**Soft reporting targets (this sample only — not product thresholds):**

| Metric | Target |
|---|---|
| Noise precision | >= 0.85 |
| Project-like false-noise rate | <= 0.10 |
| HR/legal/privileged sensitivity recall | >= 0.90 |

**Output:** `live_quality_eval.json` — counts, denominators, pass/fail per
hard check, metric values.

**Open questions for reviewer:**

1. Should `eval_live_quality.py` live in `scripts/` or
   `services/ingest/eval/`? The spec offers both; `scripts/` keeps live-mailbox
   tooling isolated from service code, which seems cleaner.
2. Should the script print a human-readable summary to stdout in addition to
   writing `live_quality_eval.json`?
3. What is the minimum label count before the eval is considered meaningful?
   Should it warn (or refuse) if fewer than N messages are labeled?

**Acceptance criteria:**
- Eval runs offline; no Gmail credentials needed.
- Output includes counts and denominator sizes alongside every metric.
- Hard checks enforce failure; soft targets are reported as informational.
- Malformed or missing label file produces a clear error message, not a
  traceback.

**Reviewer notes:** _(add comments here)_

---

## S6.4 — L0 Tuning Pass

**Touches:** `services/ingest/normalize/noise.py`,
`services/ingest/normalize/sensitivity.py`, `services/ingest/params.py`

**What it does:** Adjust noise and sensitivity rules based on S6.3 findings.
Gated — do not start until the label/eval loop from S6.1–S6.3 exists.

**Ground rules:**
- High precision on noise; do not chase recall at the cost of dropping
  project-like mail.
- Sensitivity errs conservative; false positives acceptable, false negatives
  are not.
- Configurable inputs go in `IngestParams`, not hardcoded.
- Rule changes based on live mailbox examples must encode generalized patterns
  only — never raw private text in fixtures.

**Open questions for reviewer:**

1. If the throwaway mailbox's 66% noise rate turns out to be correct (it's
   spam-heavy), should S6.4 still run, or is it skipped with a finding
   documented instead?
2. Are there known noise rule weaknesses already suspected before S6.3 runs —
   e.g., mailing-list detection, auto-reply patterns — that should be
   pre-listed as candidates?

**Acceptance criteria:**
- All 161 existing pytest tests still pass after any rule change.
- Each changed rule has a new fixture test.
- Live quality eval score improves or tradeoff is documented.
- No committed fixture contains throwaway mailbox content.

**Reviewer notes:** _(add comments here)_

---

## S6.5 — Identity and Graph Quality Inspection

**Touches:** `services/enrich/identity.py`, `services/enrich/graph.py`,
`services/enrich/roles.py`, `services/enrich/params.py`

**What it does:** Inspect the 60-person / 31-edge output from the live
baseline for merge/split errors, role misclassification, and missing edges.
Adds targeted report columns to S6.1 output (or a separate script section).

**Questions the inspection should answer:**
- Are aliases for the same person merged correctly?
- Are unrelated people being merged on display-name similarity?
- Is the owner excluded exactly once from the graph?
- Are edges missing because messages are one-way spam, or because graph rules
  are too conservative?
- Do high-message contacts with clear role signals end up as UNKNOWN?

**Suggested additions to S6.1 `summary.json`:**
- People with high identity count (possible over-merge)
- People with identical or near-identical display names (possible duplicate)
- High-message people with UNKNOWN role
- Non-noise senders with no outbound edge

**Open questions for reviewer:**

1. 31 edges for 60 people (~0.52 edges/person) on a spam-heavy throwaway
   inbox may simply be correct. Should the inspection assume this is a bug to
   fix, or treat it as a hypothesis to test?
2. Should display-name similarity comparison use exact match, or a fuzzy
   threshold? If fuzzy, what library and threshold?
3. Should identity/graph inspection findings feed back into S6.3's eval
   output, or remain a separate report?

**Acceptance criteria:**
- Each identity merge/split rule change has a test.
- Each role rule change has a test.
- Expected limitations of rules-based role inference are documented.
- Anomalies are either fixed with tests or documented as known limitations.

**Reviewer notes:** _(add comments here)_

---

## S6.6 — Optional Live Clustering Diagnostic

**Status: Optional.** Proceed only if S6.1–S6.5 show enough clean
project-like signal.

**What it does:** Runs a local-only clustering diagnostic against the live
mailbox without persisting results to the main DB. Production embeddings are
deferred; this is an observability run only.

**Embedding options (decision needed):**
1. Fake/hash embeddings using existing test `FIXTURE_PARAMS` — no model
   choice required, purely structural.
2. A documented local embedding model if the team explicitly chooses one now.

**Output (local/ignored):**
- `.local/reports/live-quality/project_cards.json`
- `.local/reports/live-quality/similarity_graph.graphml`

**Open questions for reviewer:**

1. Should S6.6 be deferred entirely to the L2 sprint, where the embedding
   model decision will be made anyway? It risks doing duplicate work if the
   embedding choice changes the clustering significantly.
2. If fake embeddings are used, how useful is the structural clustering output
   as a quality signal? Is it worth the implementation cost?

**Acceptance criteria:**
- All output is local and git-ignored.
- Report states the embedding mode and parameters used.
- Results are not persisted to the main DB.
- No product claim is made that live clustering is production-ready.

**Reviewer notes:** _(add comments here)_

---

## S6.7 — Documentation Update

**Builds:** Updates to this doc and a pointer in `docs/` or `README.md`.

**What it does:** Summarizes S6 findings — what the eval showed, what was
fixed, what was documented as a known limitation, and the recommendation for
what comes next (L2 or another quality pass).

**Acceptance criteria:**
- Findings from S6.3 eval are summarized.
- Any L0/L1 rule changes are noted with their rationale.
- Known limitations are listed.
- A clear next-step recommendation is written: L2 if quality is acceptable,
  or another pass if not.

**Reviewer notes:** _(add comments here)_

---

## Summary Table

| Ticket | Title | Depends on | Status |
|---|---|---|---|
| S6.1 | Redacted live quality report script | — | not started |
| S6.2 | Local label template workflow | S6.1 | not started |
| S6.3 | Noise/sensitivity eval script | S6.2 | not started |
| S6.4 | L0 tuning pass | S6.3 | not started |
| S6.5 | Identity/graph quality inspection | S6.3 | not started |
| S6.6 | Optional live clustering diagnostic | S6.4, S6.5 | optional |
| S6.7 | Documentation update | S6.4, S6.5 | not started |

Pre-work already complete: `.local/` added to `.gitignore` (`8ee8101`).
