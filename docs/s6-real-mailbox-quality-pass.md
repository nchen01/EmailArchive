# S6 Real-Mailbox Quality Pass

## Recommendation

The next product step should be a real-mailbox quality pass over the live Gmail
ingest output before starting L2/vector retrieval.

The MVP surfaces are shipped and the production-hardening demo proved the live
Gmail plumbing:

- Full baseline: 2161 messages / 2153 threads.
- Incremental sync: 1 new message fetched via stored Gmail historyId.
- OAuth, audit start/finish, capped runs, sync-token persistence, and privacy-safe
  logging all worked on a throwaway mailbox.
- Verification after hardening: `python -m pytest -q` -> 161 passed; frontend
  build -> 357.13 kB.

That is enough to move past "can the system touch Gmail?" The next question is
"does the structured output look trustworthy on real mail?"

## Why This Comes Before L2

L2/vector retrieval is important, but it will amplify whatever L0 and L1 get
wrong. If noise filtering drops important project mail, if sensitivity tagging
misses HR/legal messages, if identity merges unrelated people, or if the graph is
sparse for the wrong reason, then a better retriever will only retrieve better
looking mistakes.

The live run already gave a signal worth investigating:

- 1427 / 2161 messages were flagged as noise.
- 2 messages were tagged HR.
- 60 people and 31 edges were produced from 2161 messages.

Those numbers might be correct for the throwaway mailbox, but they need sampling
before we tune retrieval, project quality, or synthesis behavior around them.

S6 should answer:

1. Are project-like messages being incorrectly marked as noise?
2. Are sensitive messages being caught with acceptable recall?
3. Are people and identities merging/splitting correctly?
4. Are edges sparse because the mailbox is spam-heavy, or because graph rules are
   too conservative?
5. Does the live mailbox produce enough clean structured signal for project
   clustering and cover-for-me to be useful?

## Scope

S6 is a quality and instrumentation sprint. It should add tools, reports, and
tests that let the team inspect real-mailbox behavior without leaking private
content.

In scope:

- Sampling reports for noise, sensitivity, identity, and graph output.
- Local-only labeled sample files for the throwaway mailbox.
- Precision/recall style evals for noise and sensitivity on sampled data.
- Small rule/config fixes if the samples reveal obvious misses.
- Documentation of findings and known limitations.
- Optional local rerun of clustering on the live baseline if the clean-message
  sample looks healthy.

Out of scope:

- L2/vector retrieval and `message_embedding`.
- New migrations except for strictly necessary metadata tables. Prefer files and
  reports first.
- Real customer mailboxes.
- Secrets manager/Vault.
- Redis queue.
- Full OAuth web app.
- M365 provider.
- Learned classifiers. S6 may prepare labeled data for one, but should not build
  a model unless a later decision explicitly scopes it.

## Privacy Rules

This work touches third-party email content, even in a throwaway mailbox. Treat
the output as sensitive.

Rules:

- Do not commit raw subjects, bodies, Message-IDs, email addresses, or token
  values.
- Sampling reports committed to the repo must contain counts, IDs hashed or
  redacted, classifier labels, and short safe metadata only.
- Any local review file that includes readable subject/body excerpts must live
  outside the repo or under an ignored local path.
- Prefer message UUIDs or stable redacted row numbers in committed reports.
- Keep the throwaway mailbox address out of docs.
- Audit-log retention remains intentional. Do not add an FK cascade to
  `audit_log.mailbox_id`.

## Proposed Build Order

### S6.1 Add A Local Quality Export Script

Add:

```text
scripts/live_quality_report.py
```

Purpose:

Generate privacy-safe CSV/JSON reports from an already-ingested mailbox. The
script must not call Gmail. It reads Postgres only.

CLI:

```text
python scripts/live_quality_report.py --mailbox-id <uuid> --out .local/reports/live-quality
```

Flags:

| Flag | Purpose |
|---|---|
| `--mailbox-id UUID` | Required mailbox to inspect. |
| `--out PATH` | Output directory. Default `.local/reports/live-quality`. |
| `--sample-size N` | Per-bucket sample size. Default 50. |
| `--seed N` | Deterministic sample seed. Default existing project seed if available. |
| `--include-sensitive` | Default false. When false, do not emit sensitive-message samples. |
| `--hash-ids` | Default true. Hash message headers and emails in output. |

Outputs:

```text
summary.json
noise_samples.csv
sensitivity_samples.csv
identity_samples.csv
edge_samples.csv
thread_samples.csv
```

`summary.json` should include:

- message count
- thread count
- person count
- edge count
- noise count and percentage
- sensitivity distribution
- top sender domains by count
- top sender domains by noise rate
- messages with missing or synthetic Message-ID count
- thread size distribution
- owner-replied thread count
- graph density summary

CSV files must avoid raw content by default. Suggested columns:

`noise_samples.csv`

```text
sample_id, message_pk, message_header_hash, sender_domain, noise, sensitivity,
has_owner_reply_in_thread, thread_message_count, subject_chars, clean_text_chars,
date_day
```

`sensitivity_samples.csv`

```text
sample_id, message_pk, message_header_hash, sender_domain, sensitivity,
noise, subject_chars, clean_text_chars, date_day
```

`identity_samples.csv`

```text
person_id, canonical_email_domain, identity_count, display_name_count,
message_count, role, role_confidence
```

`edge_samples.csv`

```text
person_id, role, weight, thread_count, msg_count, last_ts_day
```

`thread_samples.csv`

```text
thread_id, message_count, participant_count, noise_count, sensitivity_set,
t_start_day, t_end_day, subject_chars
```

Acceptance:

- Running the script on the live test mailbox produces reports.
- Reports contain no raw email addresses, subjects, bodies, token values, or
  message headers.
- Re-running with the same seed produces byte-identical reports.
- Unit tests cover hashing/redaction and deterministic sampling.

### S6.2 Add Local Labeling Workflow

Add a local-only labeling file pattern:

```text
.local/labels/live-mailbox-labels.csv
```

Ensure `.local/` is ignored.

Manual labeling columns:

```text
sample_id, actual_noise, actual_sensitivity, project_like, notes
```

Allowed values:

`actual_noise`

```text
noise
not_noise
unsure
```

`actual_sensitivity`

```text
none
hr
legal
privileged
personal
unsure
```

`project_like`

```text
yes
no
unsure
```

The script should emit a blank label template:

```text
label_template.csv
```

That template should use `sample_id` values from the redacted reports. If a
human needs raw content to label, they should inspect it locally in the database
or mailbox, then write only the label values into the local CSV.

Acceptance:

- Label template is deterministic.
- Label files are ignored by git.
- No raw content is required in committed artifacts.

### S6.3 Add Eval For Noise And Sensitivity

Add:

```text
services/ingest/eval/live_quality.py
```

or, if keeping all live-mailbox tooling out of service code:

```text
scripts/eval_live_quality.py
```

Input:

```text
--report-dir .local/reports/live-quality
--labels .local/labels/live-mailbox-labels.csv
```

Output:

```text
live_quality_eval.json
```

Metrics:

- noise precision
- noise recall
- not-noise false positive rate
- sensitivity precision per class
- sensitivity recall per class
- project-like false noise rate
- sample coverage counts

**Hard checks for S6** (eval must enforce these):

| Check | Gate |
|---|---|
| No raw private content committed | hard |
| Reports are deterministic (same seed → byte-identical output) | hard |
| Malformed label file causes eval to fail, not silently skip | hard |
| No sensitive false negatives in the reviewed sample | hard |
| No project-like messages wrongly dropped as noise without a documented finding or fix | hard |

**Soft reporting targets for this sample** (smoke metrics, not product thresholds):

| Metric | Target |
|---|---:|
| Noise precision | >= 0.85 |
| Project-like false-noise rate | <= 0.10 |
| HR/legal/privileged sensitivity recall | >= 0.90 |

These numeric targets are soft and specific to this throwaway corpus. The
throwaway mailbox is skewed — spam/newsletter-heavy in a way a real work inbox
is not. A real work inbox will also vary widely by role, company, and mailbox
age. Do not treat these as universal product thresholds; treat them as
reporting targets for this sample only. If the numbers miss the target,
document the finding and the tradeoff — do not automatically treat a miss as a
release blocker.

Reasoning:

- Noise precision matters because downstream layers skip noisy messages.
- Sensitivity recall matters more than sensitivity precision; missing HR/legal is
  worse than over-tagging a few safe messages.
- Hard global thresholds would be misleading until there is a broader labeled set
  spanning multiple real inboxes.

Acceptance:

- Eval runs offline.
- Eval output includes counts and denominator sizes.
- Eval enforces the hard checks above; soft targets are reported but do not fail
  the eval script.
- Eval does not require Gmail credentials.

### S6.4 Inspect And Tune L0 Rules

Only after the label/eval loop exists, tune:

```text
services/ingest/normalize/noise.py
services/ingest/normalize/sensitivity.py
services/ingest/params.py
```

Rules:

- Keep noise classifier high precision. Do not chase recall if it risks dropping
  project-like mail.
- Keep sensitivity conservative. False positives are acceptable; false negatives
  are not.
- Configurable tenant inputs belong in `IngestParams`, not hardcoded in logic.
- Add fixture tests for each changed rule.
- If a rule is based on live mailbox examples, encode only generalized patterns,
  never raw private text.

Acceptance:

- Existing fixture tests still pass.
- Live quality eval improves or explicitly documents unchanged tradeoffs.
- No committed test fixture contains private throwaway mailbox content.

### S6.5 Inspect Identity And Graph Quality

Focus modules:

```text
services/enrich/identity.py
services/enrich/graph.py
services/enrich/roles.py
services/enrich/params.py
```

Questions:

- Are aliases for the same person merged?
- Are unrelated people merged because of display-name similarity?
- Is the owner excluded exactly once?
- Are edges missing because most messages are one-way spam/newsletters?
- Are important fake-project contacts assigned UNKNOWN when they have obvious
  role signals?

Suggested report additions:

- duplicate-looking people by display name similarity
- people with many identities
- high-message people with UNKNOWN role
- non-noise senders with no edge
- edge weights by domain and role

Acceptance:

- Add tests for any identity merge/split rule changes.
- Add tests for any role keyword or threshold changes.
- Document expected limitations of rules-based role inference.

### S6.6 Optional Project-Clustering Smoke On Live Baseline

Do this only after S6.1-S6.5 show enough clean project-like signal.

Current hardening runner intentionally does not run clustering because the
production embedding model is deferred. For S6, the goal is not to solve
production embeddings. The goal is a local diagnostic run.

Options:

1. Use the existing test `FIXTURE_PARAMS` and fake/hash embeddings for a local
   diagnostic only.
2. Use a documented local embedding model if the team explicitly chooses one.

Output:

```text
.local/reports/live-quality/project_cards.json
.local/reports/live-quality/similarity_graph.graphml
```

Do not persist these project assignments into the main DB unless the embedding
choice is made and documented.

Acceptance:

- Diagnostic output is local/ignored.
- Report states parameters and embedding mode.
- No product claim is made that live clustering is production-ready.

## Suggested Tickets

| Ticket | Title | Done Condition |
|---|---|---|
| S6.1 | Redacted live quality report script | Reports generated, deterministic, no raw content. |
| S6.2 | Local label template workflow | `.local` labels ignored, template generated. |
| S6.3 | Noise/sensitivity eval | Metrics computed from labels, malformed labels fail. |
| S6.4 | L0 tuning pass | Rule changes tested; eval findings documented. |
| S6.5 | Identity/graph quality report | Merge/split/edge anomalies visible and sampled. |
| S6.6 | Optional live clustering diagnostic | Local-only project cards and graph output. |
| S6.7 | Documentation update | Findings summarized in this doc and README pointer added. |

## Database Queries For Manual Checks

Mailbox-specific counts:

```sql
select count(*) from message where mailbox_id = :mailbox_id;
select count(*) from thread where mailbox_id = :mailbox_id;
select count(*) from person where mailbox_id = :mailbox_id;
select count(*) from edge where mailbox_id = :mailbox_id;
```

Noise distribution:

```sql
select noise, count(*)
from message
where mailbox_id = :mailbox_id
group by noise
order by noise;
```

Sensitivity distribution:

```sql
select unnest(sensitivity) as tag, count(*)
from message
where mailbox_id = :mailbox_id
group by tag
order by count(*) desc;
```

Top sender domains by volume:

```sql
select split_part(sender_email, '@', 2) as domain, count(*)
from message
where mailbox_id = :mailbox_id
group by domain
order by count(*) desc
limit 25;
```

High-volume non-noise domains:

```sql
select split_part(sender_email, '@', 2) as domain, count(*)
from message
where mailbox_id = :mailbox_id and noise = false
group by domain
order by count(*) desc
limit 25;
```

Threads with mixed sensitivity/noise:

```sql
select thread_id,
       count(*) as messages,
       count(*) filter (where noise) as noisy,
       array_agg(distinct unnest_tag.tag) as sensitivity_tags
from message
cross join lateral unnest(sensitivity) as unnest_tag(tag)
where mailbox_id = :mailbox_id
group by thread_id
order by messages desc
limit 25;
```

Audit sanity:

```sql
select action, message_count, sync_token is not null as has_sync_token,
       started_at, finished_at
from audit_log
where mailbox_id = :mailbox_id
order by started_at desc
limit 20;
```

## Definition Of Done

S6 is complete when:

- A redacted live quality report can be generated from the validated Gmail
  mailbox.
- A local human label sample exists outside git.
- Noise and sensitivity eval metrics are produced from that sample.
- Any changed L0/L1 rules have tests and documented tradeoffs.
- Identity/graph anomalies are sampled and either fixed or documented.
- The team can say which path comes next:
  - L2/vector retrieval, if L0/L1 quality is acceptable.
  - Another quality/tuning pass, if project-like mail is being dropped or
    sensitive content is being missed.

## Recommendation After S6

If S6 shows acceptable L0/L1 quality, proceed to L2:

- Decide embedding model and dimension.
- Add `message_embedding` as migration 0006 per spec 04 ticket 4.5.
- Build HNSW index and retrieval integration.
- Upgrade cover-for-me from bounded L1-only to hybrid L1 + L2 retrieval without
  changing the surface API.

If S6 finds serious L0/L1 quality issues, fix them first. Retrieval should not be
used to hide weak structure.

> **Status as of 2026-06-11 (post-S7.10):** The L2 recommendation above has been
> completed. D12 resolved the embedding model (Voyage AI `voyage-4`, 1024-dim);
> migration 0006 implements `message_embedding` and the HNSW index; S7.1–S7.10
> deliver vector retrieval, FTS retrieval, hybrid merge, retrieval contracts, and
> the retrieval eval (all hard gates pass on the fixture mailbox).
> S7.11 (cover-for-me L1+L2 upgrade) is the remaining step.
> See `docs/decisions.md` D12 and `docs/s7-implementation-plan.md` for current state.

---

## Implementation Status (S6.7 — 2026-06-07)

**Tooling complete.** S6.1–S6.5 are built and reviewed. A smoke dataset
(59 messages of known ground truth) has been added to validate the pipeline
and eval tooling without a manual labeling pass. Real-inbox calibration
against a work mailbox remains future work.

### Scripts built

| Script | Purpose |
|---|---|
| `scripts/live_quality_report.py` | Generates privacy-safe CSV/JSON reports from Postgres. Never calls Gmail. |
| `scripts/eval_live_quality.py` | Reads reports + human labels; enforces hard checks; reports soft targets. |
| `scripts/gmail_oauth_write_token.py` | Obtains a `gmail.insert` OAuth token for the smoke dataset generator. |
| `scripts/generate_smoke_dataset.py` | Injects 59 curated project email threads into a fresh throwaway Gmail account. Writes `ground_truth.json`. |
| `scripts/fill_smoke_labels.py` | Auto-fills the S6.2 label template from ground truth after ingest. No human labeling required. |

### Why a smoke dataset instead of labeling the spam mailbox

The original throwaway mailbox (2,162 messages, 66% noise) is spam-heavy in
a way that makes the human labeling pass unreliable as a pipeline test:

- The 66% noise rate may be correct for that mailbox but tells us nothing
  about project-email quality.
- Labeling spam to get sensitivity recall figures is misleading — there is
  almost no sensitive content to find.
- The eval soft targets (noise precision ≥ 0.85, etc.) were calibrated for
  that corpus and should not be treated as product thresholds.

The smoke dataset provides 59 messages of known ground truth — 14 project
threads and 4 noise messages — to validate that the pipeline and eval
tooling work correctly end-to-end. It does not calibrate real inbox noise
rates or project-email behavior, and it does not replace a real labeled
workmail corpus for tuning. Some soft metrics may be low-confidence or not
evaluated (e.g. `privileged_recall` has only 4 examples; `legal_recall`
has none because `LEGAL` tagging requires `cfg.legal_domains`).

### Smoke dataset workflow

**Prerequisite:** use a fresh, empty throwaway Gmail account.  
The Gmail API returns messages newest-first. If the account already has
thousands of messages, the smoke messages may not be fetched within
`--max-messages 100`. A fresh account guarantees the 59 injected messages
are the only ones.

```text
# Step 1 — obtain a gmail.insert write token (NOT gmail.readonly)
python scripts/gmail_oauth_write_token.py

# Export — bash/zsh:
export GMAIL_WRITE_TOKEN='{"token":...}'

# Export — PowerShell:
$env:GMAIL_WRITE_TOKEN = @'
{"token":...}
'@

# Step 2 — dry-run to preview
python scripts/generate_smoke_dataset.py \
    --owner-email you@gmail.com \
    --company-domain acme.corp \
    --dry-run --confirm

# Step 3 — inject into Gmail (59 messages, ~3-second run)
python scripts/generate_smoke_dataset.py \
    --owner-email you@gmail.com \
    --company-domain acme.corp \
    --confirm

# Step 4 — ingest the 59 messages into EKC
# Use --max-messages 100 (not 500) so only smoke messages are ingested
python scripts/gmail_smoke_ingest.py \
    --owner-email you@gmail.com \
    --internal-domains acme.corp \
    --max-messages 100 --confirm

# Step 5 — generate quality reports (note the uuid printed by ingest)
python scripts/live_quality_report.py \
    --mailbox-id <uuid> \
    --out .local/reports/smoke-quality \
    --sample-size 100 \
    --include-sensitive

# Step 6 — auto-fill gold labels from ground truth
python scripts/fill_smoke_labels.py \
    --mailbox-id <uuid> \
    --report-dir .local/reports/smoke-quality \
    --ground-truth .local/smoke-dataset/ground_truth.json \
    --out .local/labels/smoke-labels.csv

# Step 7 — eval (should pass all hard checks; soft targets near 1.0)
python scripts/eval_live_quality.py \
    --report-dir .local/reports/smoke-quality \
    --labels .local/labels/smoke-labels.csv
```

**Mixed-inbox variant** (account already has non-smoke messages):

If the report was generated from a mailbox that contains both smoke messages
and other mail, most report rows will not match the smoke ground truth.
Pass `--allow-unmatched` to write the label file anyway — unmatched rows
get blank labels and are excluded from eval metrics:

```text
python scripts/fill_smoke_labels.py \
    --mailbox-id $env:EKC_MAILBOX_ID \
    --report-dir .local/reports/smoke-quality \
    --ground-truth .local/smoke-dataset/ground_truth.json \
    --out .local/labels/smoke-labels.csv \
    --allow-unmatched
```

### Smoke dataset design

59 messages across 18 threads, date-anchored to 90 days before today:

| Threads | Count | Sensitivity | Purpose |
|---|---|---|---|
| Project (nexus-design, nexus-launch, incident-api, vendor-cloudbase, budget-q3, roadmap-h2, security-audit, customer-esc, oncall-handoff, board-prep) | 10 × 3-5 msgs | none | Cover the main project-email surface |
| Legal MSA (legal-msa) | 4 msgs | privileged | "attorney-client" keyword in every message |
| HR performance (hr-perf) | 4 msgs | hr | "performance review" keyword |
| Hiring (hiring-backend) | 4 msgs | hr | "salary"/"compensation" keywords |
| 1:1 with manager (oneone-manager) | 2 msgs | hr | "compensation"/"salary" keywords |
| Noise | 4 msgs | none (noise=True) | noreply@ pattern and List-Unsubscribe headers |

**Sensitivity note:** `legal-msa` is tagged `privileged` (not `legal`).
The `LEGAL` tag requires `cfg.legal_domains` to be configured; keyword
detection alone produces `PRIVILEGED`. The eval will show `legal_recall=n/a`
and `privileged_recall=1.0`. This is expected and correct.

### Current status of S6 tickets

| Ticket | Status | Notes |
|---|---|---|
| S6.1 | **Done** | Reports generated, deterministic, no raw content. |
| S6.2 | **Done** | Label template emitted by S6.1; `.local/` gitignored; schema locked. |
| S6.3 | **Done** | Eval enforces all hard checks; 8 hard checks + 5 soft targets. |
| S6.4 | **Deferred** | Smoke dataset validates the eval tooling. Real-inbox L0 tuning requires a labeled workmail corpus — deferred to post-L2. |
| S6.5 | **Done** | Identity/graph signals in `summary.json`. |
| S6.6 | **Deferred to L2** | Clustering requires a production embedding model. |
| S6.7 | **Done** | Smoke dataset tooling committed. First live eval run complete — see findings below. |

### Known limitations

- `LEGAL` sensitivity requires `cfg.legal_domains` to be configured with
  the sender's domain. The smoke dataset tests keyword-based detection only.
- `PERSONAL` sensitivity requires sender domain in `cfg.personal_domains`.
  The smoke dataset does not exercise this path.
- Soft metric targets (noise precision ≥ 0.85, etc.) are for this synthetic
  corpus only. Real inboxes will vary.
- PII scan in the eval is a best-effort @-scan. Human review before
  committing any output is still required.

### Live eval findings (2026-06-09)

First smoke dataset run against a mixed Gmail inbox (smoke messages
injected alongside existing mail).

**Ingest:**
- 460 messages, 419 threads ingested
- Sync token saved (incremental-ready)

**Label fill:**
- 70 / 197 report rows matched from smoke ground truth
- 127 unmatched rows (expected — mailbox contains non-smoke messages)
- `--allow-unmatched` used; unmatched rows excluded from eval metrics

**Eval result: PASS** — all hard checks passed.

**Soft targets (evaluated on matched smoke rows only):**
- `legal_recall`: n/a [not evaluated] — no `legal` ground-truth examples
  in the smoke dataset; `LEGAL` tagging requires `cfg.legal_domains`
- Other targets: evaluated against the 70 matched rows

**Do not tune classifiers from this run.** The 70 matched labels are
synthetic and partial (mixed inbox means the report sample contains
non-smoke messages with blank labels). This run validates that the
pipeline and eval tooling work end-to-end. Real inbox noise thresholds
and sensitivity calibration require a labeled workmail corpus.

### Next step

S6 is complete. L2 retrieval through S7.10 is now complete. Remaining S7 work:

- S7.11 cover-for-me L1+L2 upgrade (eval gates are green; precondition satisfied).
- S7.12 hosted Voyage reranker (optional, feature-flagged).

> **Status as of 2026-06-11:** The L2 items listed in the original draft of this
> section (decide embedding model, add migration 0006, build HNSW, upgrade
> cover-for-me) have been completed by D12/S7.1–S7.10. See `docs/decisions.md`
> D12 and `docs/s7-implementation-plan.md` for current state.
