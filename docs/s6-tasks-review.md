# S6 Task Breakdown — Reviewer Sign-Off

Source spec: `docs/s6-real-mailbox-quality-pass.md`
Reviewer sign-off: 2026-06-06 — S6.1–S6.5 and S6.7 approved. S6.6 deferred to L2.

---

## Dependency chain

```
S6.1 (report script)
  └── S6.2 (label template)
        └── S6.3 (eval script)
              └── S6.4 (L0 tuning)    ← only if eval reveals issues
              └── S6.5 (identity/graph inspection)
                    └── S6.7 (docs update)

S6.6 (optional clustering) — DEFERRED to L2 sprint
```

S6.4 and S6.5 can run in parallel once S6.3 is done.
S6.7 is a wrap-up step that closes the sprint.

---

## Global implementation note — determinism and timestamps

Deterministic reports (same seed → byte-identical output) must not include
`generated_at` or any wall-clock timestamp in report files unless that
timestamp is either excluded from the byte-identical check or fixed by a
CLI input. A `generated_at` field in `summary.json` would silently break the
determinism criterion on every run. Either omit it or add a `--timestamp`
flag so callers control the value.

Required determinism practices for every output file:
- Sort every row and list before writing (do not rely on DB or dict ordering).
- Use stable sample ordering: sort candidate rows by a fixed key before
  applying the seeded sample, not after.
- `json.dumps(..., sort_keys=True)` for all JSON output.
- Fixed newline behavior: open files with `newline="\n"` or normalize
  explicitly; do not let the platform decide.
- No implicit timestamps anywhere in the output — not in filenames, not in
  JSON fields, not in CSV headers.

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
  (see global note on timestamps above)
- `--include-sensitive` defaults false; sensitive-tagged messages are excluded
  from CSV samples unless explicitly opted in
- Script queries Postgres only. It must not call Gmail or any external API.

**Decisions:**

1. **Output location policy:** All outputs stay under `.local/` by default and
   are never committed. If committed example reports are ever needed, use
   synthetic fixtures only — not throwaway or real mailbox output.
2. **Sample size:** Default `--sample-size 50` is fine for triage. For eval
   confidence, recommend labeling 100+ total rows, stratified across predicted
   noise/not-noise buckets and all sensitivity-tagged samples. Document this
   recommendation in the script's help text.
3. **Fewer rows than sample size:** Warn, do not fail. Emit the actual
   denominator in output so the reviewer knows coverage.

**Acceptance criteria:**
- Script runs against the live throwaway mailbox and produces all seven output
  files.
- No raw subjects, bodies, addresses, Message-IDs, or token values appear in
  any output file with default flags.
- Re-running with the same `--seed` produces byte-identical output (no
  wall-clock timestamps in report files — see global note).
- If mailbox has fewer messages than `--sample-size`, script warns and records
  actual denominator; it does not fail.
- Unit tests cover: hashing/redaction logic, deterministic sampling, empty
  bucket handling, fewer-rows-than-sample-size warning path.

---

## S6.2 — Local Label Template Workflow

**Builds:** Label workflow convention + `label_template.csv` emitted by S6.1.

**What it does:** Defines the local-only labeling file pattern and column
schema that a human fills in after inspecting messages directly in the DB or
mailbox client.

**Label file location:** `.local/labels/live-mailbox-labels.csv` (git-ignored)

**Label schema:**

| Column | Allowed values | Notes |
|---|---|---|
| `sample_id` | From report | Links back to `noise_samples.csv` |
| `actual_noise` | `noise` / `not_noise` / `unsure` | |
| `actual_sensitivity` | `none` / `hr` / `legal` / `privileged` / `personal` / `unsure` | |
| `project_relevant` | `yes` / `no` / `unsure` | renamed from `project_like` |
| `notes` | Free text | Local only |
| `reviewed_by` | Free text | Optional; blank for solo review |
| `reviewed_at` | ISO date | Optional; blank for solo review |

**Decisions:**

1. **Column name:** `project_like` renamed to `project_relevant`.
2. **Reviewer metadata:** Add optional `reviewed_by` and `reviewed_at` columns.
   Both may be blank; they are there for multi-reviewer passes.
3. **Local raw review file:** Add `--emit-local-review-file` flag to S6.1.
   When set, the script writes a separate file under `.local/` that includes
   raw subject/body excerpts for labeling convenience. This file must:
   - Write only under `.local/` (never committed)
   - Hard-fail if the resolved output path is not under `.local/` — do not
     fall back silently or write elsewhere
   - Print a privacy warning before writing
   - Not appear in normal report output
   - Default off

**Acceptance criteria:**
- `label_template.csv` is emitted deterministically by S6.1 alongside other
  reports.
- `.local/` is confirmed git-ignored (already done, commit `8ee8101`).
- No raw content appears in any committed artifact.
- `--emit-local-review-file` writes only under `.local/` and prints a privacy
  warning.

---

## S6.3 — Noise and Sensitivity Eval Script

**Builds:** `scripts/eval_live_quality.py`

**What it does:** Reads S6.1 reports and the S6.2 label file; computes
precision/recall metrics; enforces hard S6 checks; reports soft targets
without failing on them.

**Hard checks (script must fail on these):**

| Check |
|---|
| No raw private content in generated report files |
| Reports are deterministic (same seed → byte-identical) |
| Malformed label file → eval fails, not silently skips |
| No sensitive false negatives in the reviewed sample |
| No undocumented project-relevant messages wrongly dropped as noise |

Note on the "no raw private content" check: the eval script cannot determine
what has or hasn't been committed without shelling out to git, which is out of
scope. This check means "no raw private content appears in the generated report
files themselves." Human review before committing any output remains required.

**Soft reporting targets (this sample only — not product thresholds):**

| Metric | Target |
|---|---|
| Noise precision | >= 0.85 |
| Project-like false-noise rate | <= 0.10 |
| HR/legal/privileged sensitivity recall | >= 0.90 |

**Decisions:**

1. **Location:** `scripts/eval_live_quality.py`. Live-mailbox analysis tooling
   belongs in `scripts/`, not service runtime code.
2. **Output:** Print a short human-readable summary to stdout and write the
   full metrics dict to `live_quality_eval.json`.
3. **Minimum label thresholds:**
   - Fewer than 30 reviewed rows → fail with a clear error.
   - Fewer than 100 reviewed rows → warn that metrics may be low-confidence.
   - Class denominator under 10 → suppress or mark that metric as
     low-confidence in output rather than reporting a misleading number.
   - Class denominator under 20 → report with a low-confidence flag.

**Acceptance criteria:**
- Eval runs offline; no Gmail credentials needed.
- Output includes counts and denominator sizes alongside every metric.
- Hard checks enforce failure; soft targets are informational.
- Malformed or missing label file → clear error message, not a traceback.
- Fewer than 30 labeled rows → eval fails. Fewer than 100 → warns.
- Low-confidence metrics are flagged in output, not silently reported as valid.

---

## S6.4 — L0 Tuning Pass

**Touches:** `services/ingest/normalize/noise.py`,
`services/ingest/normalize/sensitivity.py`, `services/ingest/params.py`

**What it does:** Adjust noise and sensitivity rules based on S6.3 findings.
Gated — do not start until the label/eval loop from S6.1–S6.3 exists.

**Decisions:**

1. **If 66% noise rate is correct:** Skip rule changes and document the
   finding. No tuning without evidence from labels.
2. **Pre-listed hypotheses to test** (do not implement until labels confirm a
   problem):
   - Mailing list messages (list headers, bulk precedence)
   - Auto-replies and out-of-office messages
   - Calendar invites, notifications, and reminder mail
   - Transactional tool noise: GitHub, Jira, DocuSign, CI systems
   - Marketing / unsubscribe-pattern messages

**Ground rules:**
- High precision on noise; do not chase recall at the cost of dropping
  project-relevant mail.
- Sensitivity errs conservative; false positives acceptable, false negatives
  are not.
- Configurable inputs go in `IngestParams`, not hardcoded.
- Rule changes based on live mailbox examples encode only generalized patterns
  — never raw private text in fixtures.

**Acceptance criteria:**
- All 161 existing pytest tests still pass after any rule change.
- Each changed rule has a new fixture test.
- Live quality eval score improves, or tradeoff is explicitly documented.
- No committed fixture contains throwaway mailbox content.
- If no rule changes are made, S6.4 closes with a written finding.

---

## S6.5 — Identity and Graph Quality Inspection

**Touches:** `services/enrich/identity.py`, `services/enrich/graph.py`,
`services/enrich/roles.py`, `services/enrich/params.py`

**What it does:** Inspect the 60-person / 31-edge output from the live
baseline for merge/split errors, role misclassification, and missing edges.
Adds targeted report columns to S6.1 `summary.json`.

**Questions the inspection should answer:**
- Are aliases for the same person merged correctly?
- Are unrelated people being merged on display-name similarity?
- Is the owner excluded exactly once from the graph?
- Are edges missing because messages are one-way spam, or because graph rules
  are too conservative?
- Do high-message contacts with clear role signals end up as UNKNOWN?

**Decisions:**

1. **31 edges / 60 people:** Treat as a hypothesis to test, not a bug to fix.
   The throwaway mailbox is spam-heavy; sparse edges may be correct.
2. **Display-name similarity:** Start with exact normalized matching in
   reports. If fuzzy matching is needed after reviewing results, use RapidFuzz
   with a threshold around 92 and report candidates before changing any merge
   behavior.
3. **Scope separation:** Identity/graph inspection output is a separate section
   in `summary.json` or a dedicated report file — not part of the S6.3
   noise/sensitivity eval output.

**Suggested additions to S6.1 `summary.json`:**
- People with high identity count (possible over-merge)
- People with identical or near-identical display names (possible duplicate)
- High-message people with UNKNOWN role
- Non-noise senders with no outbound edge
- Edge weights by domain and role

**Acceptance criteria:**
- Each identity merge/split rule change has a test.
- Each role rule change has a test.
- Expected limitations of rules-based role inference are documented.
- Anomalies are either fixed with tests or documented as known limitations.
- If no rule changes are made, S6.5 closes with a written finding.

---

## S6.6 — Optional Live Clustering Diagnostic

**Status: DEFERRED to L2 sprint.**

Running fake/hash embedding clustering on live mail risks producing misleading
conclusions about project quality. The embedding model decision will be made
during L2 planning, and clustering results will change substantially based on
that choice. Do not build S6.6 unless S6.1–S6.5 surface a specific need that
cannot wait for L2.

The ticket remains in the spec for reference.

---

## S6.7 — Documentation Update

**Builds:** Updates to this doc and `docs/s6-real-mailbox-quality-pass.md`.

**What it does:** Summarizes S6 findings — what the eval showed, what was
fixed, what was documented as a known limitation, and the recommendation for
what comes next.

**Acceptance criteria:**
- Findings from S6.3 eval are summarized.
- Any L0/L1 rule changes are noted with their rationale.
- Known limitations are listed.
- A clear next-step recommendation is written: proceed to L2 if quality is
  acceptable, or another quality pass if not.

---

## Summary Table

| Ticket | Title | Depends on | Status |
|---|---|---|---|
| S6.1 | Redacted live quality report script | — | approved |
| S6.2 | Local label template workflow | S6.1 | approved |
| S6.3 | Noise/sensitivity eval script | S6.2 | approved |
| S6.4 | L0 tuning pass | S6.3 | approved |
| S6.5 | Identity/graph quality inspection | S6.3 | approved |
| S6.6 | Optional live clustering diagnostic | S6.4, S6.5 | deferred to L2 |
| S6.7 | Documentation update | S6.4, S6.5 | approved |

Pre-work already complete: `.local/` added to `.gitignore` (`8ee8101`).
