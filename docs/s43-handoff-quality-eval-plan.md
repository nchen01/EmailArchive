# S43 - Handoff Quality Evaluation Harness

Status: implemented.

## Purpose

A repeatable, offline, deterministic harness that tells us whether generated
handoff packages are accurate, safe, cited, and usable enough to trust. This is the
first "quality-first" item on the post-S41 roadmap
(`docs/product-roadmap-quality-first.md`). It is **not** a new product feature and
**not** an integration; it measures the existing deterministic generator
(`services/handoff/generator.py`) against a small synthetic gold corpus.

## Non-goals

- No product-behavior change (the generator, routes, DTOs, and schema are
  unchanged; the harness only drives and measures them). If the harness finds a
  bug, it is reported first, not silently fixed.
- No privacy gates, creator wizard, coverage contracts, or integrations (calendar,
  Jira/Linear, Slack/Teams). Limitations the harness surfaces are logged as
  candidate S44 work, not built here.
- No network / external API (Anthropic, Voyage, Gmail). No `ekc_schemas` change, no
  migration.

## Shape

- **Corpus:** small JSON scenarios under `fixtures/handoff_eval/` (3-5 in the first
  pass), each defining a synthetic mailbox (projects, threads, messages with
  sensitivity/noise flags, extracted events) plus **gold labels** (expected project
  labels, decisions, open loops, blockers, stakeholders, exclusions, and citations).
- **Harness:** `services/handoff/eval/` seeds a scenario into a **throwaway
  mailbox**, runs the real `generate_candidate`, reads back the resulting
  `handoff_claim` / `handoff_evidence` / `handoff_exclusion` rows, evaluates them
  against the gold labels, and tears the mailbox down. `evaluate()` is a pure
  function over collected data (unit-testable without a DB).
- **Runner:** `scripts/eval_handoff_quality.py` runs the whole corpus and prints a
  console report (optionally `--json`). Requires a local `DATABASE_URL` (offline
  Postgres); it creates and destroys its own mailboxes and never touches real data.
- **Tests:** `tests/test_s43_handoff_eval.py` - DB-free corpus-coherence + pure
  metric tests, plus DB-gated end-to-end gate tests.

## Metrics

Hard gates (a failure is a real problem, exits non-zero):

- **every_claim_cited** - every generated claim cites at least one package header
  (no-citation-no-claim).
- **citations_in_evidence** - every claim citation header exists in the package
  evidence.
- **excluded_material_absent** - no gold-excluded (sensitive/noise) header appears
  in package evidence or any claim citation.

Quality signals (reported, inform trust; not a hard failure on their own):

- expected decisions found / missing; expected open loops found / missing.
- **blocker content** found / missing (kind-agnostic - blocker-shaped work is
  matched even though it surfaces as an open_loop/decision), plus
  **blocker_kind_present** (whether a true `blocker`-kind claim exists; currently
  always false - see Limitations).
- project labels present where expected (S39 frozen `project_label`).
- stakeholders / related domains present in evidence senders.
- **claim precision proxy** = generated claims matching a gold category / total
  generated claims; and the list of unexpected claims.

Known limitations (reported as limitations, never as a hard failure, and flagged as
candidate S44 work):

- **Blocker-kind extraction not implemented.** The generator maps events
  `proposed -> open_loop`, `did`/`outcome -> decision`; there is no `blocker` kind,
  so blocker content surfaces as an open loop/decision and is not labeled a blocker.
- **Stale/conflict detection not implemented.** Contradictory or outdated claims are
  surfaced without any flag; the harness reports the scenario as a known limitation.

## Boundary

Creator/package evaluation is allowed (this operates on the creator side, before
publish). The recipient boundary is untouched: nothing here gives a recipient live
mailbox access, and the harness never runs against real user data - only synthetic
throwaway mailboxes it creates and deletes.
