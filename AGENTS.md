# AGENTS.md — start here if you are an AI implementing this repo

This is a **spec-first** project. The architecture, data contracts, and acceptance criteria already
exist; your job is to realize them faithfully, not to redesign them. **Read before you write.**

---

## 1. Orientation — read in this order

1. **`README.md`** — system purpose, the L0→L1→L2→L3 pipeline, tech stack, repo layout, sprint table.
2. **`packages/ekc_schemas/models.py`** — read the top-of-file docstring end to end; it is both the
   contract *and* the rulebook (six numbered conventions). Then skim the models.
3. **`docs/implementation-plan.md`** — the rationale, the MVP cut (§8), and the open questions (§9).
4. **The spec for your assigned layer** in `docs/specs/`. Read the whole spec, not just your ticket.

| Spec | Covers | Status |
|---|---|---|
| `00-l0-ingest.md` | L0 ingest & normalization (deep dive) | ✓ implemented (S1) |
| `01-layer1-enrichment.md` | L1: identity (§3), graph (§4), roles (§5), clustering (§6), events (§7) | ✓ all sections done (S1–S4) |
| `02-project-view.md` | Project-view surface + its API contract | ✓ implemented (S3–S4) |
| `03-project-clustering.md` | Deep dive of L1 §6 (build-ready) | ✓ implemented (S3) |
| `04-storage-schema.md` | Postgres + pgvector schema & migrations | ✓ implemented (S2, migrations 0001–0005) |
| `05-network-map.md` | Network-map surface + API contract | ✓ implemented (S2) |

## 2. The mental model

- Pipeline: mailbox → **L0 ingest** (clean/normalize) → **L1 enrich** (materialize Person, Org,
  Project, Edge, Event) → **L2 retrieve** → **L3 synthesize** → UI surfaces.
- The differentiated value is **L1 structuring**, not retrieval. L0 makes the data clean; L1 makes
  it structured; L3 turns it into cited answers; L2 is plumbing.
- A "project" is *constructed*, not given — there is no `project_id` in email (the reason spec 03 exists).

## 3. Invariants you cannot break

1. **Import models from `ekc_schemas`; never re-declare or fork them.** Inline code in specs is
   illustrative; if it conflicts with the schema file, the file wins.
2. **Two id schemes, never mixed:** internal foreign keys use UUID `id`; citations/provenance use
   `Message.message_id_header` (RFC 5322 Message-ID), so they survive re-ingest and open the email.
3. **No citation, no claim.** Any asserting object (currently `Event`) carries ≥1 `source_message_ids`
   holding `message_id_header` values — not UUIDs. The schema enforces this at construction; do not
   engineer around it.
4. **Confidence ∈ [0,1];** the UI hides inferred facts below a per-tenant threshold (default 0.4).
5. **Strip the owner exactly once, in L1:** included in `Thread.participants`, excluded from `Edge`
   and clustering features. Do not re-strip elsewhere.
6. **Determinism & idempotency:** use the fixed `seed`; identical inputs ⇒ byte-identical output;
   persist via idempotent upsert keyed on `message_id_header`. No wall-clock or RNG in logic paths.
7. **Respect the layer boundary:** L0 emits normalized *addresses*, never `person_id`. Identity
   resolution (spec 01 §3) is the bridge — do not build the relationship graph before it runs.
8. **Volume is not accomplishment.** Never let email count imply impact; this is the core product
   failure mode the grounding discipline (proposed/did/outcome) exists to prevent.
9. **Operational:** the LLM appears only in L3; sensitivity tags gate data (default `exclude`);
   OAuth tokens never touch the app DB or logs.

## 4. How to execute a spec

The two deep-dive specs (00 and 03) share one shape — learn it once:

- **Scope & invariants** — what you build and the rules around it.
- **Data contracts** — a pointer to `ekc_schemas`; import, don't redefine.
- **Stages** — each section is headed with its target module file (e.g. `normalize/threads.py`,
  `communities.py`) and carries reference code. Implement file by file, in order.
- **Parameters & defaults** — go in `params.py`; nothing hardcoded in logic.
- **Edge cases / Observability / Acceptance (Definition of Done)** — your test and review checklist.
- **Sprint task breakdown** — dependency-ordered tickets, each with a done-condition (spec 00:
  0.1–0.10; spec 03: 3.1–3.11). Take a ticket; don't invent scope outside it.
- **Open decisions / questions** — confirm with a human before they block you; don't guess.

Implement to the Definition of Done, run the eval where one exists, and prove determinism.

## 5. Sprint & dependency order

- **S0 ✓** — schemas contract (`packages/ekc_schemas/models.py`), DB spec (spec 04), and seed fixture
  (`fixtures/`, 18 messages + `gold/`) are all in place.
- **S1 ✓** — L0 ingest (`services/ingest/`) + identity resolution + relationship graph
  (`services/enrich/identity.py`, `graph.py`). 38 passing tests.
- **S2 ✓** — role inference (`services/enrich/roles.py`, spec 01 §5) + Alembic migrations
  0001–0005 (`alembic/versions/`) + SQLAlchemy mappers (`services/db/`) + FastAPI network-map
  endpoints (`services/api/`, spec 05) + React frontend (`frontend/`). 46 passing tests.
  DB runs via Docker Compose (`docker-compose.yml`). Seed with `python scripts/dev_seed.py`.
- **S3 ✓** — project clustering (spec 03) + project view surface (spec 02). 124 tests.
- **S4 ✓** — event extraction (spec 01 §7) + L3 synthesis (project summary, contact summary).
  138 tests. `services/enrich/events/`, `services/synthesis/`.
- **S5 — next** — bounded L1-only cover-for-me query (D11). Routes over Person, Project,
  Event, Edge, Thread already in the DB. Does not bluff on queries beyond structured evidence.
  No L2/vector retrieval in S5; `message_embedding` remains deferred (see known gaps).

## 6. Known gaps — flag, don't fake

- **L2 retrieval** is intentionally *not* specced here; an existing query router owns it. Integrate
  against it; do not build a retrieval layer. The `message_embedding` table (spec 04 ticket 4.5,
  pgvector HNSW) is deferred until the embedding model is chosen.
- **L3 synthesis / grounding spec** is not written. The contract (cite-or-don't-claim; proposed/did/
  outcome grading) lives in `implementation-plan.md` §4 and spec 01 §7, but the L3 *mechanism* is
  unspecified. Do not build it until the spec exists.
- **Role inference** (spec 01 §5, `services/enrich/roles.py`) is a rules-based v1. It correctly
  classifies all fixture contacts but is not a learned classifier. Upgrade path: labeled data →
  small classifier, logged in the spec.
- **`message_embedding` table + HNSW index** — spec 04 ticket 4.5 deliberately deferred until the
  embedding model dimension is chosen. Add as migration 0006 when that decision is made.
- **M365 provider** (`services/ingest/providers/msgraph.py`) — raises `NotImplementedError`; Gmail
  is the only live provider. M365 drops in without pipeline changes (D2).
- **Object store for raw MIME** — `Message.raw_uri = None` in all current runs. Wire when deploying
  against a real mailbox (D6).

Terminology heads-up: the implementation-plan narrative sometimes writes `message_id` loosely; the
authoritative field name is `Message.message_id_header`.

## 7. Making changes to shared contracts

- To add or change a shared model: edit `packages/ekc_schemas/models.py` and **bump `SCHEMA_VERSION`**.
  Never fork a model into a service.
- Import as `from ekc_schemas import Message, Person, Project, ...`.
- Python 3.11 backend; Pydantic v2; per-service module layout is given at the bottom of each spec.

## 8. Definition of done (every task)

- Imports schemas; introduces no forked or re-declared models.
- Meets the spec's Acceptance / Definition-of-Done checkboxes.
- Passes its eval where one exists (clustering: extended-BCubed gates, spec 03 §22; L0 classifiers:
  labeled-sample precision/recall, spec 00 §19).
- Deterministic: identical output on identical input + seed.
- Honors privacy: sensitivity exclusion, least-privilege scope, no tokens in DB or logs.

## 9. When docs disagree

Precedence: **`packages/ekc_schemas/models.py` → your layer's spec → `implementation-plan.md` → README →
this file.** Where specs overlap, the newer (spec 03) overrides the older (spec 01 §6). When a spec's
**"Open decisions"** list contains an item that **`docs/decisions.md`** has resolved, the decision log
wins. **Surface the conflict** to a human; do not silently choose.
