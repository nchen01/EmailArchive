# Coverage → Offboarding — transition guide

> Read this when you're ready to extend the shipped **coverage** product into the **offboarding**
> product. It records what carries over unchanged, what has to change or evolve, and the one
> decision that must be settled first. It is a roadmap, not a build-ready spec — each change below
> becomes its own spec when scheduled.

---

## TL;DR

> **D14 update:** The MVP direction is now employee-initiated audited handoff
> packages. This doc remains a v2 transition guide for admin-side offboarding,
> not the primary MVP path. Build the D14 handoff package first; return here when
> adding manager/HR/IT initiated offboarding workflows.

The **engine transitions cleanly**. Coverage and offboarding run the *same* L0→L1→L2→L3 pipeline
over the same `ekc_schemas` contracts (this is the "two products, one engine" premise in
`README.md` and `implementation-plan.md` §2). Offboarding is **not a second build** — it is:

1. a different **consent + trigger** path into ingestion,
2. the existing **privacy/sensitivity controls turned up**, not rebuilt, and
3. a **freshness + feature-emphasis** shift (historical data; accomplishment summaries move to center).

The thing that gates offboarding is **legal/retention policy and a privacy review — not code.**

## What carries over unchanged

The pipeline is product-agnostic: L0 ingests a mailbox, L1 structures it, L2 retrieves, L3 synthesizes
with citations. None of it cares *why* the mailbox is being processed — a departed employee's inbox
and a vacationing employee's inbox are the same shape going in.

- **All data contracts** (`packages/ekc_schemas/models.py`) — no schema changes required.
- **L0 normalization, L1 enrichment (identity, graph, roles, clustering, events), L2 retrieval, L3
  grounded synthesis** — reused as-is.
- **The citation contract and grounding discipline** — identical, and arguably more valuable here.

## What changes or evolves

### 1. Consent & trigger layer  *(new surface, not an engine change)*
Coverage assumes the employee is present and opted in. Offboarding is admin-side, after the fact,
with no participation.
- **Build:** an offboarding trigger (HR/IT initiates) and the consent/authorization record for that mode.
- **Already anticipated:** the admin OAuth path and immutable audit log (spec 00 §5, §12, §16), and
  `mailbox.status` already includes `'retiring'` alongside `'active'` (spec 04 §3). The ingest
  mechanics are identical; only the entry path and legal basis differ.

### 2. Privacy & sensitivity controls  *(turned up, not rebuilt)*
This is why offboarding is "the harder v2." The admin-side / no-consent configuration is the exact
pattern that draws the most regulatory scrutiny — it reads as employee monitoring, with hard limits
in the EU (`implementation-plan.md` §7).
- **Already exists in the engine:** sensitivity tagging (privileged/legal/HR/personal) with default
  `exclude` (spec 00 §11, params §14 `sensitivity_mode`), the audit log, retention/TTL, and
  data-subject deletion (spec 04 §10). These were specced *because* offboarding would need them.
- **What changes:** you turn existing dials up and add policy — not new machinery. The hard part is
  legal sign-off and a privacy review, which the docs already point at.

### 3. Freshness & feature emphasis  *(graceful shift)*
Offboarding data is historical, not current.
- **"Live state of project Z"** becomes **"state of project Z when they left."** Same objects,
  different framing in the UI copy.
- **Accomplishment summaries (L3 + the event layer, spec 01 §7) fit offboarding *better* than
  coverage** — summarizing what someone did across their tenure *is* the offboarding use case. The
  engine gains relevance here rather than losing it.

## The one decision to settle first

**Retention & deletion of third-party data** — the open decision in **spec 04 §10**.

For coverage it's low-stakes: the employee is present, the data is current and short-lived. For
offboarding you hold a *departed* person's correspondence indefinitely, which sharpens the genuinely
hard problem: *how do you erase one person's words from threads they share with others* (scrub vs.
tombstone vs. redact-in-place). Offboarding is the context that forces this answer. Settle the
policy — with legal — before building the offboarding trigger, because it shapes the data model's
deletion semantics.

## Transition checklist

| # | Item | Type | Reuses / depends on |
|---|---|---|---|
| 1 | Resolve third-party retention & deletion policy | **Policy + legal** | spec 04 §10 (open decision) |
| 2 | Privacy review for admin-side, no-consent processing (esp. EU) | **Policy + legal** | implementation-plan §7 |
| 3 | Offboarding trigger + authorization record | Eng (new surface) | spec 00 §5/§12; `mailbox.status='retiring'` (spec 04 §3) |
| 4 | Tighten sensitivity/exclusion config for offboarding mode | Eng (config) | spec 00 §11, params §14 |
| 5 | Retention/TTL + data-subject deletion enforcement | Eng | spec 04 §10/§12 |
| 6 | UI copy shift: "live state" → "state at departure" | Eng (surface) | spec 02 |
| 7 | Promote accomplishment summaries to a primary surface | Eng | spec 01 §7 (events) + L3 |
| 8 | Pipeline engine (L0–L3) | **No change** | `ekc_schemas`, specs 00/01/03 |

## Bottom line

Once coverage ships, offboarding reuses essentially the entire engine. The work is a consent/trigger
flow, a stricter configuration of privacy controls that already exist, and a feature-emphasis shift
toward accomplishment summaries. Resolve the retention/deletion policy and the privacy review first;
the code follows easily from there.
