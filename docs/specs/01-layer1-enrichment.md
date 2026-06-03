# Spec 01 — Layer 1: Enrichment / Structuring

> The layer that turns ingested email into first-class objects. This is **not** RAG.
> It runs offline/batch and produces the structure that L2 queries and L3 cites.

**Owners:** backend / data. **Depends on:** L0 ingest output. **Feeds:** L2, L3, UI.

---

## 1. Scope

Input: normalized, deduped, sensitivity-tagged messages and reconstructed threads from L0.
Output: a persisted graph of `Person`, `Org`, `Project`, `Edge`, and `Event` objects, all
traceable back to `message_id`s.

Five sub-modules, run in this order (each idempotent):

1. Identity resolution  → `Person`, `Identity`
2. Relationship graph   → `Edge`
3. Role inference       → `Person.role`
4. Project clustering   → `Project`
5. Event extraction     → `Event`

## 2. Data models (`packages/schemas`)

> **Authoritative definitions live in `packages/schemas/models.py`.** The code below is
> illustrative context — import the real models, do not re-declare them. (Note: the `Project`
> model shown here is superseded by the richer one in spec 03 / the schemas package.)

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class Role(str, Enum):
    AE = "account_exec"
    LEAD = "lead"
    INTERNAL = "internal"
    MANAGER = "manager"
    VENDOR = "vendor"
    UNKNOWN = "unknown"

class Identity(BaseModel):
    email: str                       # canonical-cased address
    display_names: list[str] = []
    person_id: str | None = None     # resolved Person.id

class Person(BaseModel):
    id: str
    canonical_email: str
    names: list[str] = []
    org_id: str | None = None
    role: Role = Role.UNKNOWN
    role_confidence: float = 0.0
    identities: list[str] = []       # Identity.email values

class Org(BaseModel):
    id: str
    name: str
    domains: list[str] = []
    internal: bool = False

class Edge(BaseModel):
    person_id: str                   # the contact (owner is implicit)
    message_count: int
    sent_to_count: int               # owner -> contact
    received_count: int              # contact -> owner
    first_contact: datetime
    last_contact: datetime
    weight: float                    # computed, see §4

class Project(BaseModel):
    id: str
    label: str                       # inferred; see §6.4
    member_ids: list[str] = []
    thread_ids: list[str] = []
    start: datetime
    end: datetime
    confidence: float = 0.0

class EventType(str, Enum):
    PROPOSED = "proposed"            # intent / future tense
    DID = "did"                      # action taken
    OUTCOME = "outcome"              # confirmed result

class Event(BaseModel):
    id: str
    actor_person_id: str
    type: EventType
    summary: str                     # <= 1 sentence, no embellishment
    project_id: str | None = None
    source_message_ids: list[str]    # REQUIRED — grounding contract
    confidence: float = 0.0
```

## 3. Identity resolution  ·  `@sprint S1`

A person uses many addresses, aliases, and display-name spellings. Collapse them to one
`Person`.

Signals, in priority order:
1. Exact email match (after normalizing case + plus-addressing, e.g. `a+x@co.com → a@co.com`).
2. Directory / org-chart lookup if available (authoritative — see §8 open Qs).
3. Display-name + domain match (fuzzy name within the same org domain).
4. Reply-chain co-reference (same signature block across addresses).

```python
def resolve_identities(messages: list[Message]) -> tuple[list[Person], list[Identity]]:
    by_email = group_by(normalize_email, addresses_in(messages))
    clusters = union_find(by_email)
    for a, b in candidate_pairs(by_email):          # blocked by org domain
        if name_similarity(a, b) > 0.92 and same_domain(a, b):
            clusters.union(a, b)
    return materialize_people(clusters)
```

`@acceptance`
- Two addresses of one human (e.g. `j.park@acme.com`, `jenna@acme.com`) resolve to one `Person`.
- No false merge across distinct humans sharing a first name within an org (precision > recall here).
- Every `Message` sender/recipient maps to exactly one `Person`.

## 4. Relationship graph  ·  `@sprint S1`

One `Edge` per contact, owner implicit. Weight blends volume, recency, and reciprocity so
a high-volume-but-stale contact ranks below a current two-way collaborator.

```python
def edge_weight(e: Edge, now: datetime) -> float:
    volume   = log1p(e.message_count)
    recency  = exp(-days_since(e.last_contact, now) / HALF_LIFE_DAYS)   # default 45
    recip    = 1 - abs(e.sent_to_count - e.received_count) / max(e.message_count, 1)
    return round(0.5*volume + 0.3*recency + 0.2*recip, 4)
```

`@acceptance`
- Edges expose the raw counts AND the computed weight (UI scales node/edge size by weight).
- Re-running over the same mailbox produces identical weights (deterministic).

## 5. Role inference  ·  `@sprint S2`

Classify each `Person` into a `Role` with a confidence. Treat as a label, not truth.

Feature signals:
- `internal`: sender domain == owner's org domain (strongest single signal).
- Directory title if available (`VP`, `Manager`, `SDR`, etc.).
- Thread behavior: who opens deals, who is cc'd vs to'd, who approves.
- Linguistic cues from signatures/salutations (`Account Executive`, `Sales`, `Procurement`).
- Edge shape: managers show broad internal fan-out; AEs show external + recurring cadence.

Start rules-based (transparent, debuggable) with a confidence score; upgrade to a small
classifier once labeled data exists. Output `Role.UNKNOWN` below threshold rather than guessing.

`@acceptance`
- Internal vs external split is ~100% (domain-driven).
- Each non-internal contact gets a role + confidence; low-confidence renders as "unconfirmed".

## 6. Project clustering  ·  `@sprint S3`

The hard one. A project is a fuzzy set of threads/people/time — **there is no `project_id`
in email**, and thread boundaries do not equal project boundaries (one project spans many
threads; one thread can touch several projects). Cluster *across* threads.

### 6.1 Feature vector per thread
`participants` (set), `entities/keywords` (extracted), `embedding` (mean of message
embeddings), `time_window`, `attachment_hashes`, `linked_urls`, `calendar_refs`.

### 6.2 Similarity
```python
def thread_similarity(a, b) -> float:
    return ( 0.35*jaccard(a.participants, b.participants)
           + 0.30*cosine(a.embedding, b.embedding)
           + 0.20*jaccard(a.keywords, b.keywords)
           + 0.10*temporal_overlap(a.window, b.window)
           + 0.05*shared(a.attachment_hashes, b.attachment_hashes) )
```

### 6.3 Clustering
Build a thread-similarity graph (edges above a threshold), run community detection
(Louvain/Leiden). Communities = candidate projects. Allow a thread to belong to >1 project
via soft assignment (overlapping communities) since threads genuinely span projects.

### 6.4 Labeling
No canonical name exists. Derive a label from the cluster's top TF-IDF keyphrases +
most-frequent capitalized noun phrase; fall back to `"<top contact> · <month>"`. Store
`confidence`; let the user rename in the UI (renames are sticky).

`@acceptance`
- A known multi-thread project (seed fixture) clusters into one `Project` with ≥80% of its
  threads.
- A "catching up" thread that touches 3 projects is soft-assigned to all 3, not forced into one.
- Every `Project` carries `thread_ids`, `member_ids`, time span, and confidence.

## 7. Event extraction  ·  `@sprint S4`

Raw material for honest accomplishment summaries. Email logs *activity and intent*, rarely
*outcomes* — so extract at the right epistemic grain and never upgrade intent to outcome.

```python
EVENT_PROMPT = """Extract events from this thread as JSON list.
Each: {actor, type, summary, source_message_ids}.
type is one of: proposed (future/intent), did (action taken),
outcome (confirmed result with evidence in the text).
Do NOT infer outcome from volume or tone. If no outcome is stated, do not emit one.
summary: one factual clause, no adjectives."""
```

Run per thread with the schema enforced; attach `project_id` from §6; every event REQUIRES
≥1 `source_message_id`.

`@acceptance`
- "I'll send the contract" → `proposed`, never `outcome`.
- A thread with lots of discussion but no stated result yields `proposed`/`did` events only.
- 0 events without a citation (hard invariant; reject at write time).

## 8. Orchestration & interfaces

```python
def run_enrichment(mailbox_id: str) -> EnrichmentResult:
    msgs, threads = load_l0(mailbox_id)
    people, ids   = resolve_identities(msgs)        # S1
    edges         = build_relationship_graph(msgs, people)   # S1
    people        = infer_roles(people, msgs, edges)         # S2
    projects      = cluster_projects(threads)                # S3
    events        = extract_events(threads, projects)        # S4
    return persist(people, ids, edges, projects, events)     # idempotent upsert by content hash
```

## 9. Open questions

- Is org-directory access available alongside the mailbox? It sharpens §3 and §5 dramatically.
- Embedding model + vector store choice (shared with L2 — align here).
- Confidence thresholds for hiding inferred facts (per-field, configurable).
- Where does the **existing RAG query pipeline** slot in? Map its current outputs to
  §6.2 (thread embeddings) and L2; mark `// TODO` once confirmed.
