# Email Knowledge Continuity — Implementation Plan

> Structuring a departing or covered employee's mailbox into a navigable map of
> people, projects, and evidenced work, so a successor can pick up the role fast.

---

## 1. The problem

Onboarding is well tooled; offboarding and coverage are not. When someone leaves,
goes on leave, or hands off a role, the institutional memory in their inbox — who
they worked with, what they owned, the live state of each project — evaporates.
This product turns that unstructured mailbox into structured, queryable knowledge.

## 2. Two products, one engine

The same pipeline powers two go-to-market motions with very different risk profiles.
Build the engine once; ship the safe version first.

| | Coverage (0-to-1) | Offboarding (v2) |
|---|---|---|
| Trigger | Vacation, leave, role handoff | Departure / termination |
| Employee present? | Yes — participates | No |
| Consent model | Employee opt-in (clean) | Admin-side, after the fact |
| Data freshness | Current | Historical |
| Stakes / scrutiny | Low | High (reads as monitoring) |
| Buyer | Manager | HR / IT |

The coverage motion is the wedge: cleaner consent, fresher data, an acute and
recurring pain. Offboarding is the higher-value but harder follow-on.

## 3. Core user job

> *"I'm covering for X. Who do I ask about Y, and what's the state of project Z?"*

If a stand-in can type one sentence and get back the right two or three people plus
the last thread that actually matters — each answer traceable to a real message —
the product has done its job.

---

## 4. Architecture — three layers

Retrieval (RAG) is the middle layer. The two hard problems live above and around it.

```
                ┌─────────────────────────────────────────┐
   mailbox ───► │  L0  Ingestion & normalization            │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L1  Enrichment / structuring  (NOT RAG)  │
                │      people · relationships · projects ·  │
                │      roles · events                       │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L2  Retrieval (RAG)                      │
                │      vector + structured filters          │
                └─────────────────────────────────────────┘
                                  │
                ┌─────────────────────────────────────────┐
                │  L3  Constrained synthesis                │
                │      grounded, citation-bound answers     │
                └─────────────────────────────────────────┘
                                  │
                          Surfaces (UI)
```

### Layer 0 — Ingestion & normalization

The foundation. Get the data in cleanly and safely before anything touches a model.

- **Access.** Admin OAuth into a single mailbox (Google Workspace / Microsoft 365).
  Scope it to the one mailbox, time-box the grant, and write an immutable audit log
  of every access. Least-privilege is both a security posture and a sales asset.
- **Thread reconstruction.** Rebuild conversation lineage from `Message-ID`,
  `In-Reply-To`, and `References` headers plus the provider thread ID. Store the
  lineage; do not trust subject lines as project boundaries.
- **Deduplication.** Collapse quoted-reply duplication so the same paragraph is not
  embedded fifty times.
- **Noise filtering.** Drop newsletters, automated notifications, calendar spam, and
  mass `cc-all` blasts *before* the model sees them. Garbage in is the number-one
  quality killer downstream.
- **Sensitivity tagging.** A first-pass classifier flags privileged / legal / HR /
  personal content so later layers can redact or exclude it. (See §7.)

### Layer 1 — Enrichment / structuring

This is the part that is **not** RAG, and where the differentiated value lives. It
runs offline and materializes first-class objects that Layer 2 later queries.

- **Identity resolution.** Map the many addresses, aliases, and display-name variants
  a person uses to one canonical `Person`. Same for `Org` (domain-based + fuzzy).
- **Relationship graph.** Build weighted edges between the mailbox owner and every
  contact. Weight by frequency, recency, directionality (who initiates), and
  thread depth. This graph is what the network map renders.
- **Role inference.** Classify each contact — account executive, lead/prospect,
  internal teammate, manager, vendor, customer — from signals: email domain
  (internal vs external), directory data if available, salutation and signature
  patterns, thread role, and language. Treat as a confidence-scored label, not truth.
- **Project clustering.** The hard one. A project is a fuzzy set of threads, people,
  and time spans — there is no `project_id` in email. Cluster *across* thread
  boundaries using participant overlap, entity/keyword co-occurrence, temporal
  bursts, shared attachments and links, calendar tie-ins, and embedding similarity.
  Output materialized `Project` objects with members, time span, and source threads.
- **Event extraction.** Pull verbs and outcomes at the right epistemic grain:
  *proposed* vs *did* vs *confirmed-outcome*. This is the raw material for honest
  accomplishment summaries and prevents the "volume = impact" trap.

### Layer 2 — Retrieval (RAG)

Standard, well-understood plumbing — but it queries the structure L1 produced.

- **Embeddings** over message/thread chunks for semantic search.
- **Hybrid retrieval.** Vector similarity *plus* structured filters (by project,
  by contact, by role, by time window) from the L1 objects.
- **Provenance.** Every retrieved chunk carries its `message_id` so any downstream
  claim can be traced back to a specific, openable message.

### Layer 3 — Constrained synthesis

Where summaries get generated — and where the grounding discipline lives.

- **Citation-bound generation.** Every assertion must link to one or more
  `message_id`s. No citation, no claim.
- **Epistemic honesty.** Distinguish what is evidenced from what is inferred. Prefer
  *"Coordinated the migration across 12 threads; final outcome not visible in email"*
  over a confident fabrication.
- **Anti-pattern guardrails.** Volume of email is not accomplishment. The synthesis
  layer must resist rendering "sent 200 emails" as "drove to completion."
- **Partial-record framing.** Email is one channel; real work also lives in Slack,
  docs, and meetings. Surface what is evidenced and flag what cannot be seen.

---

## 5. Data model (sketch)

| Object | Key fields |
|---|---|
| `Person` | canonical id, names, org, role label + confidence |
| `Identity` | email address, display name → resolves to `Person` |
| `Org` | name, domain, internal/external flag |
| `Thread` | provider id, root `Message-ID`, participants, time span |
| `Message` | id, thread id, sender, recipients, ts, body ref, sensitivity tags |
| `Project` | id, label, members, time span, source thread ids, confidence |
| `Edge` | person_a, person_b, weight, frequency, recency, direction |
| `Event` | actor, type (proposed/did/outcome), project, source message ids |

## 6. Surfaces (MVP)

1. **Network map** — the mailbox owner at center; contacts colored by role; edge
   weight = contact frequency; filterable by project. Click a node for the
   relationship detail and the threads behind it.
2. **Project view** — members, timeline, current state, and the threads that define it.
3. **Cover-for-me query** — the natural-language entry point that answers the §3 job,
   every answer cited.

## 7. Privacy & compliance (design constraints, not afterthoughts)

- The mailbox contains **third-party personal data** (every external contact). Those
  individuals retain rights under GDPR/CCPA regardless of which internal employee
  reads the data.
- Privileged, HR-sensitive, and personal content must be detectable and excludable
  (the L0 sensitivity pass).
- The offboarding config (admin-side, no employee consent) is the pattern that draws
  the most scrutiny and reads as employee monitoring — hard limits in the EU.
- Keep an audit log of access; support retention limits and data-subject deletion.
- *Not legal advice — run a real privacy review before selling into a regulated buyer.*

## 8. MVP cut & sequencing

**Implemented (S1–S2):**
- Single-mailbox ingest via `FixtureProvider` (fixture-driven) and `GmailProvider` (real OAuth).
  Thread reconstruction, address normalization, deduplication, noise filtering, sensitivity tagging.
- Identity resolution (domain-blocked fuzzy name merge), relationship graph with weighted edges.
- Role inference — rules-based v1 (internal/external split + keyword signals).
- PostgreSQL 16 + pgvector persistence via Alembic migrations + SQLAlchemy mappers.
- Network-map surface: FastAPI `GET /api/network-map/{mailbox_id}` + contact detail endpoint.
  React 18 + `react-force-graph-2d` frontend with role-colored nodes, legend toggles, contact drawer.

**Next (S3):**
- Project clustering (spec 03, tickets 3.1–3.11): thread-similarity graph, community detection,
  overlapping soft assignment, c-TF-IDF labeling.
- Project view surface (spec 02): members, timeline, derived state, "what's been done" panel.

**Deferred:**
- Accomplishment summaries / cover-for-me query (needs event extraction, S4).
- Multi-mailbox, the offboarding motion, cross-channel (Slack/docs) ingestion.
- L2 retrieval layer (externally owned query router; `message_embedding` table deferred until
  embedding model dimension is chosen — see spec 04 ticket 4.5).
- M365 provider (stub now; drops in when needed — D2).
- Object store for raw MIME (`raw_uri = None` until production deployment — D6).

**Plug-in point:** the existing RAG query pipeline maps onto **Layer 2** and parts of
the project-clustering retrieval in **Layer 1**. `// TODO: confirm which signals the
current pipeline already extracts and where it slots in.`

## 9. Open questions

- **Directory access (org chart)** alongside the mailbox? It would sharpen role inference
  dramatically beyond the current keyword-based v1. The rules-based classifier correctly handles
  the fixture but will struggle with ambiguous cases at real-mailbox scale. Resolved if yes:
  add directory lookup as signal priority 2 in spec 01 §3/§5.
- **Projects displayed when email gives no canonical label?** Addressed in spec 03 §12 (c-TF-IDF
  keyphrases + top contact + month fallback). Still to validate against real-mailbox output.
- **Confidence thresholds** for hiding inferred facts: `role_confidence_threshold = 0.4` (default
  in `EnrichParams`, spec 04 `mailbox.display_threshold`). UI respects this for role labels.
  Fine-tuning per-tenant is deferred.
- **Retention** after a coverage period ends: `retention_days` param in `IngestParams` (wired in
  params, not yet enforced in a scheduled job).
- **Embedding model + dimension:** deferred; gates spec 04 ticket 4.5 (`message_embedding` HNSW
  index) and L2 retrieval. Decide before S4.
- **Data-subject deletion semantics** for third-party content inside shared threads — needs a
  product + legal decision (spec 04 §11, §13).
