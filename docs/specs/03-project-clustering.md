# Spec 03 — Project Clustering (deep dive)

> Expands spec 01 §6 into a build-ready specification. Goal: an engineer **or an
> autonomous agent** can implement the project-clustering module end to end from this
> document alone, with deterministic outputs and a passing eval. Read spec 01 first for
> the object model; this doc supersedes §6 wherever they differ.

**Service:** `services/enrich/clustering` · **Layer:** L1 · **Depends on:** L0 (threads,
messages, attachments), embedding model (shared with L2) · **Feeds:** project view (spec 02), L3.

**Stack (pinned):** Python 3.11 · `numpy` · `scipy` · `scikit-learn` (TF-IDF) ·
`spacy` (`en_core_web_sm` for NER) · `sentence-transformers` · `hnswlib` (ANN) ·
`python-igraph` + `leidenalg` (community detection).

---

## 1. Problem & goals

A *project* is a coherent unit of work. In email it has **no identifier** and **no clean
boundary**: one project spans many threads (subject lines drift: "Q3 launch" → "Re: assets"
→ "FWD: final???"), and one thread can touch several projects ("catching up" covers three).
Thread boundaries therefore cannot be used as project boundaries.

We must **construct** projects by clustering threads across thread boundaries, allowing a
thread to belong to more than one project (soft / overlapping membership), and produce
labeled, confidence-scored `Project` objects that trace back to source threads.

Goals, in priority order:
1. **Precision over recall on splits** — better to under-merge two phases of one project than
   to merge two unrelated projects (a wrong merge corrupts the project view and "who to ask").
2. **Soft membership** — boundary threads attach to every project they genuinely serve.
3. **Determinism** — fixed seed ⇒ identical output (required for the eval and for stable IDs).
4. **Idempotent, incremental** — re-running or adding threads must not churn project IDs.

## 2. Definitions & invariants

- `Thread` is the atomic unit fed to clustering. Each carries derived `ThreadFeatures` (§5).
- A `Project` is a set of threads with soft weights plus derived members, span, label, confidence.
- `ThreadProjectAssignment` is the soft edge: `(thread_id, project_id, weight ∈ (0,1])`.
- **Invariants (enforced at write time):**
  - Every thread is assigned to ≥1 project (no thread is dropped; orphans get a singleton project).
  - Every `Project.thread_ids` is non-empty and every listed thread has an assignment back.
  - `Project.member_ids` ⊆ union of participants of its threads.
  - Output is identical across runs given the same input and `seed`.

## 3. Data contracts

> **Authoritative definitions live in `packages/ekc_schemas/models.py`.** The code below is
> illustrative context — import the real models, do not re-declare them.

```python
from pydantic import BaseModel
from datetime import datetime

class ThreadProjectAssignment(BaseModel):
    thread_id: str
    project_id: str
    weight: float            # affinity of thread to project, (0, 1]
    is_primary: bool         # the thread's argmax project

class ProjectMember(BaseModel):
    person_id: str
    involvement: float       # sum over member threads of (assignment.weight * msgs_in_thread)
    message_count: int

class Project(BaseModel):           # extends spec 01 Project
    id: str
    label: str
    label_source: str        # "ctfidf" | "entity" | "fallback" | "user"
    member_ids: list[str]
    members: list[ProjectMember]
    thread_ids: list[str]
    start: datetime
    end: datetime
    confidence: float        # [0, 1], see §13
    debug: dict | None = None  # cohesion, separation, size — see §21

class ClusteringResult(BaseModel):
    projects: list[Project]
    assignments: list[ThreadProjectAssignment]
    run_meta: dict           # seed, params hash, modularity, n_communities, orphan_ratio
```

**Input** (`ThreadFeatures`, produced in §5) and **output** (`ClusteringResult`) are the only
public contracts. Everything between is internal and may be refactored freely.

## 4. Pipeline overview

```
ThreadFeatures[]
   │  A  feature extraction (§5)                — per thread
   │  B  candidate-pair blocking (§6)           — avoid O(n²)
   │  C  pairwise similarity (§7)               — only on candidates
   │  D  similarity graph (§8)                  — threshold + kNN sparsify
   │  E  community detection: Leiden (§9)       — hard partition
   │  F  soft membership (§10)                  — overlap via centroid affinity
   │  G  materialize Projects (§11)
   │  H  labeling (§12)
   │  I  confidence (§13)
   │  J  ID stability + incremental (§14)
   ▼
ClusteringResult
```

## 5. Stage A — feature extraction  `features.py`

```python
import math, numpy as np
from dataclasses import dataclass
from datetime import datetime
from collections import Counter

@dataclass(frozen=True)
class ThreadFeatures:
    thread_id: str
    participants: frozenset[str]      # person_ids; OWNER EXCLUDED (always present, no signal)
    keywords: frozenset[str]          # lemmatized entities + salient TF-IDF terms
    embedding: np.ndarray             # float32, L2-normalized, dim = D (model-dependent)
    t_start: datetime
    t_end: datetime
    attachment_hashes: frozenset[str] # sha256 of attachment bytes (from L0)
    link_domains: frozenset[str]      # registered domains of URLs in bodies
    msg_count_by_person: dict         # person_id -> messages they sent in this thread

def build_thread_features(threads, messages_by_thread, embed_fn, nlp, tfidf,
                          owner_person_id) -> list[ThreadFeatures]:
    feats = []
    for th in threads:
        msgs = messages_by_thread[th.id]
        parts = Counter()
        for m in msgs:
            for pid in m.participant_person_ids:
                if pid != owner_person_id:
                    parts[pid] += (1 if m.sender_person_id == pid else 0)
        # embedding = token-length-weighted mean of message embeddings, normalized
        embs = np.vstack([embed_fn(m.clean_text) for m in msgs])
        w = np.array([max(len(m.clean_text.split()), 1) for m in msgs], dtype="float32")
        emb = (embs * w[:, None]).sum(0) / w.sum()
        emb = (emb / (np.linalg.norm(emb) + 1e-9)).astype("float32")
        # keywords = NER (ORG/PRODUCT/WORK_OF_ART/PERSON) + top TF-IDF terms
        text = " ".join(m.clean_text for m in msgs)[:20000]
        ents = {e.lemma_.lower() for e in nlp(text).ents
                if e.label_ in {"ORG", "PRODUCT", "WORK_OF_ART", "PERSON", "EVENT"}}
        kw = ents | tfidf.top_terms(th.id, k=12)
        feats.append(ThreadFeatures(
            thread_id=th.id, participants=frozenset(parts),
            keywords=frozenset(kw), embedding=emb,
            t_start=min(m.ts for m in msgs), t_end=max(m.ts for m in msgs),
            attachment_hashes=frozenset(h for m in msgs for h in m.attachment_hashes),
            link_domains=frozenset(d for m in msgs for d in m.link_domains),
            msg_count_by_person=dict(parts)))
    return feats
```

Notes:
- `tfidf` is a `TfidfVectorizer` fit over **all thread documents** in the mailbox (one doc per
  thread, cleaned text). `top_terms` returns the highest-weight n-grams (1–2) per thread.
- `embed_fn` MUST be the same model used by L2 retrieval — align in `ekc_schemas` config.
- Owner is excluded from `participants`: they are on every thread and carry zero discriminative
  signal. The same applies to any auto-distribution list flagged by L0.

## 6. Stage B — candidate-pair blocking  `blocking.py`

All-pairs is O(n²) (8k threads → 32M pairs). Block to ~O(n·k) candidates from three sources,
then union. Drop ubiquitous participants from blocking keys via document frequency.

```python
import math, hnswlib, numpy as np
from collections import Counter, defaultdict

def participant_idf(feats, drop_frac=0.5):
    N = len(feats); df = Counter()
    for f in feats:
        for p in f.participants: df[p] += 1
    idf = {p: math.log(N / d) for p, d in df.items()}
    ubiquitous = {p for p, d in df.items() if d > drop_frac * N}
    return idf, ubiquitous, df

def candidate_pairs(feats, ubiquitous, df, ann_k=25, max_block=300):
    pairs = set()
    # (1) shared rare participant (inverted index; cap to avoid hub blow-up)
    inv = defaultdict(list)
    for i, f in enumerate(feats):
        for p in f.participants:
            if p not in ubiquitous and df[p] <= max_block:
                inv[p].append(i)
    for ids in inv.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pairs.add((min(ids[a], ids[b]), max(ids[a], ids[b])))
    # (2) embedding ANN neighbours (catches subject-drift with no shared rare participant)
    X = np.vstack([f.embedding for f in feats]).astype("float32")
    idx = hnswlib.Index(space="cosine", dim=X.shape[1])
    idx.init_index(max_elements=len(X), ef_construction=200, M=16)
    idx.add_items(X); idx.set_ef(max(ann_k * 2, 50))
    lab, _ = idx.knn_query(X, k=min(ann_k, len(X)))
    for i, row in enumerate(lab):
        for j in row:
            j = int(j)
            if i != j: pairs.add((min(i, j), max(i, j)))
    return pairs
```

Rationale: source (1) catches projects bound by a shared external counterparty; source (2)
catches the same project across renamed threads with no shared rare participant. The `max_block`
cap stops a medium-frequency participant (e.g. a manager on 250 threads) from generating a
quadratic blob; those threads still get compared via ANN.

## 7. Stage C — pairwise similarity  `similarity.py`

```python
import numpy as np

W = {"part": 0.35, "emb": 0.30, "kw": 0.20, "temp": 0.10, "attach": 0.05}  # sums to 1.0

def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b: return 0.0
    return len(a & b) / len(a | b)

def weighted_jaccard(a, b, w):                  # participants weighted by IDF
    inter = sum(w.get(x, 1.0) for x in (a & b))
    union = sum(w.get(x, 1.0) for x in (a | b))
    return inter / union if union else 0.0

def temporal_affinity(as_, ae, bs, be, tau_days=14.0) -> float:
    inter = (min(ae, be) - max(as_, bs)).total_seconds()
    span  = (max(ae, be) - min(as_, bs)).total_seconds()
    if inter > 0:                               # overlapping intervals -> IoU
        return inter / span if span > 0 else 1.0
    gap = (max(as_, bs) - min(ae, be)).total_seconds() / 86400.0   # disjoint, in days
    return 0.3 * float(np.exp(-(gap / tau_days) ** 2))             # small decaying credit

def thread_similarity(a, b, pidf) -> float:
    s_part = weighted_jaccard(a.participants, b.participants, pidf)
    s_emb  = max(0.0, float(np.dot(a.embedding, b.embedding)))     # cosine; clamp negatives
    s_kw   = jaccard(a.keywords, b.keywords)
    s_temp = temporal_affinity(a.t_start, a.t_end, b.t_start, b.t_end)
    s_att  = jaccard(a.attachment_hashes | a.link_domains,
                     b.attachment_hashes | b.link_domains)
    return (W["part"]*s_part + W["emb"]*s_emb + W["kw"]*s_kw
            + W["temp"]*s_temp + W["attach"]*s_att)
```

The weights `W` are the primary tuning surface; treat them as config, not constants (§16).

## 8. Stage D — similarity graph  `graph.py`

```python
def build_edges(feats, pairs, pidf, tau=0.45, knn_cap=15):
    scored = []
    for i, j in pairs:
        s = thread_similarity(feats[i], feats[j], pidf)
        if s >= tau:
            scored.append((i, j, s))
    # kNN sparsify: keep each node's top-`knn_cap` edges (keeps Leiden well-behaved & fast)
    per_node = defaultdict(list)
    for i, j, s in scored:
        per_node[i].append((s, j)); per_node[j].append((s, i))
    keep = set()
    for node, lst in per_node.items():
        for s, other in sorted(lst, reverse=True)[:knn_cap]:
            keep.add((min(node, other), max(node, other), round(s, 6)))
    return list(keep)
```

## 9. Stage E — community detection (Leiden)  `communities.py`

Use **Leiden**, not Louvain: Leiden guarantees well-connected communities and is deterministic
under a fixed seed. Modularity objective with a tunable resolution `gamma` (higher → more,
smaller projects).

```python
import igraph as ig, leidenalg as la

def detect_communities(n_nodes, edges, gamma=1.0, seed=42):
    g = ig.Graph(n=n_nodes, edges=[(i, j) for i, j, _ in edges])
    g.es["weight"] = [w for _, _, w in edges]
    part = la.find_partition(
        g, la.RBConfigurationVertexPartition,
        weights="weight", resolution_parameter=gamma, seed=seed, n_iterations=-1)
    return list(part.membership), part.modularity   # membership[node] = community_id
```

Isolated nodes (no surviving edges) each form their own singleton community — handled here,
materialized as low-confidence singleton projects in §11.

## 10. Stage F — soft membership  `communities.py`

Leiden returns a hard partition. Recover overlap by scoring each thread against every community
**profile** and attaching it to additional communities that are nearly as good as its primary.

```python
import numpy as np
from collections import Counter, defaultdict

def community_profiles(feats, membership):
    by_c = defaultdict(list)
    for i, c in enumerate(membership): by_c[c].append(i)
    prof = {}
    for c, idxs in by_c.items():
        emb = np.mean([feats[i].embedding for i in idxs], axis=0)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        prof[c] = {
            "emb": emb.astype("float32"),
            "participants": frozenset().union(*(feats[i].participants for i in idxs)),
            "keywords": frozenset().union(*(feats[i].keywords for i in idxs)),
            "t_start": min(feats[i].t_start for i in idxs),
            "t_end":   max(feats[i].t_end   for i in idxs)}
    return prof

def _affinity(f, pr, pidf):                       # same shape as thread_similarity vs a profile
    s_part = weighted_jaccard(f.participants, pr["participants"], pidf)
    s_emb  = max(0.0, float(np.dot(f.embedding, pr["emb"])))
    s_kw   = jaccard(f.keywords, pr["keywords"])
    s_temp = temporal_affinity(f.t_start, f.t_end, pr["t_start"], pr["t_end"])
    return W["part"]*s_part + W["emb"]*s_emb + W["kw"]*s_kw + W["temp"]*s_temp

def soft_assign(feats, membership, prof, pidf, ratio=0.65, min_aff=0.35):
    assigns = defaultdict(dict)                    # thread_idx -> {community_id: weight}
    for i, f in enumerate(feats):
        affs = {c: _affinity(f, pr, pidf) for c, pr in prof.items()}
        primary = max(affs, key=affs.get); top = affs[primary]
        assigns[i][primary] = round(max(top, 1e-3), 4)   # always keep primary
        for c, a in affs.items():
            if c != primary and a >= min_aff and a >= ratio * top:
                assigns[i][c] = round(a, 4)
    return assigns                                  # primary = argmax per thread
```

`ratio` controls how readily a thread joins a second project; `min_aff` is a floor so weak
attachments are not created. Both are config (§16).

## 11. Stage G — materialize projects  `materialize.py`

```python
import uuid
from collections import defaultdict

def materialize(feats, assigns):
    by_c = defaultdict(list)                         # community -> [(thread_idx, weight, primary?)]
    for i, cs in assigns.items():
        primary = max(cs, key=cs.get)
        for c, w in cs.items():
            by_c[c].append((i, w, c == primary))
    projects, assignments = [], []
    for c, rows in by_c.items():
        pid = f"prj_{uuid.uuid4().hex[:8]}"
        members = defaultdict(lambda: [0.0, 0])      # person -> [involvement, msg_count]
        t_lo, t_hi, thread_ids = None, None, []
        for i, w, is_primary in rows:
            f = feats[i]; thread_ids.append(f.thread_id)
            t_lo = f.t_start if t_lo is None else min(t_lo, f.t_start)
            t_hi = f.t_end   if t_hi is None else max(t_hi, f.t_end)
            for p, mc in f.msg_count_by_person.items():
                members[p][0] += w * mc; members[p][1] += mc
            assignments.append(dict(thread_id=f.thread_id, project_id=pid,
                                    weight=w, is_primary=is_primary))
        member_list = [dict(person_id=p, involvement=round(v[0], 2), message_count=v[1])
                       for p, v in sorted(members.items(), key=lambda kv: -kv[1][0])]
        projects.append(dict(id=pid, member_ids=[m["person_id"] for m in member_list],
                             members=member_list, thread_ids=thread_ids,
                             start=t_lo, end=t_hi))
    return projects, assignments
```

## 12. Stage H — labeling  `labeling.py`

No canonical name exists. Use **class-based TF-IDF (c-TF-IDF)** to find terms distinctive to a
cluster versus the corpus, prefer a capitalized entity, fall back to top-contact + month.

```python
import math
from collections import Counter, defaultdict

def label_projects(feats, assigns, projects):
    docs = defaultdict(list)                          # primary-cluster keyword bags
    pid_for = {}
    for p in projects:
        for tid in p["thread_ids"]: pid_for.setdefault(tid, p["id"])
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}
    for p in projects:
        for tid in p["thread_ids"]:
            docs[p["id"]].extend(feats[idx_by_tid[tid]].keywords)
    cdf = Counter()                                   # in how many clusters does term appear
    for terms in docs.values():
        for w in set(terms): cdf[w] += 1
    C = max(len(docs), 1)
    for p in projects:
        tf = Counter(docs[p["id"]])
        score = {w: tf[w] * math.log((1 + C) / (1 + cdf[w])) for w in tf}
        ranked = [w for w, _ in sorted(score.items(), key=lambda x: -x[1])]
        entity = next((w for w in ranked if w.istitle() or " " in w), None)
        if entity:
            p["label"], p["label_source"] = entity.title(), "entity" if entity in tf else "ctfidf"
        elif ranked:
            p["label"], p["label_source"] = " · ".join(ranked[:2]).title(), "ctfidf"
        else:
            top = p["members"][0]["person_id"] if p["members"] else "unknown"
            p["label"] = f"{top} · {p['start'].strftime('%b %Y')}"; p["label_source"] = "fallback"
    return projects
```

- **Optional LLM polish** (behind a flag): pass the top c-TF-IDF terms + 3 sample subjects and
  ask for a 2–4 word title with the constraint *"only use words supported by the input"* to keep
  it grounded. Off by default for determinism.
- **Sticky user renames:** store overrides in a `project_label_overrides` table keyed by a stable
  cluster signature (sorted hash of high-weight thread_ids). On re-cluster, if the signature still
  matches ≥0.5 Jaccard, reapply the override and set `label_source="user"`.

## 13. Stage I — confidence  `confidence.py`

```python
import numpy as np, random

def project_confidence(feats, project_thread_idxs, prof, this_c, min_solid=4, sample=200):
    idxs = project_thread_idxs
    # cohesion: mean pairwise embedding cosine within the cluster (sampled if large)
    pairs = [(a, b) for k, a in enumerate(idxs) for b in idxs[k+1:]]
    if len(pairs) > sample: pairs = random.Random(42).sample(pairs, sample)
    cohesion = float(np.mean([np.dot(feats[a].embedding, feats[b].embedding)
                              for a, b in pairs])) if pairs else 0.5
    # separation: 1 - max cosine of this centroid to any other centroid
    me = prof[this_c]["emb"]
    others = [float(np.dot(me, prof[c]["emb"])) for c in prof if c != this_c]
    separation = 1.0 - (max(others) if others else 0.0)
    size_factor = min(1.0, len(idxs) / min_solid)
    conf = 0.5 * cohesion + 0.3 * separation + 0.2 * size_factor
    return round(float(np.clip(conf, 0.0, 1.0)), 3), dict(
        cohesion=round(cohesion, 3), separation=round(separation, 3), n_threads=len(idxs))
```

Singletons get low confidence (small `size_factor`, undefined cohesion → 0.5 default) and the UI
hides projects below a display threshold (default 0.4, configurable per spec 02).

## 14. Stage J — ID stability & incremental updates  `incremental.py`

Re-clustering must not reshuffle project IDs (the project view, renames, and any links depend on
them). Two mechanisms:

**ID carry-over (full re-cluster).** Match new clusters to previous ones by Jaccard of thread sets;
inherit the old `id` (and label override) on a match.

```python
def carry_over_ids(new_projects, old_projects, j_thresh=0.5):
    old = {p["id"]: set(p["thread_ids"]) for p in old_projects}
    used = set()
    for np_ in new_projects:
        ns = set(np_["thread_ids"]); best, bj = None, 0.0
        for oid, os in old.items():
            if oid in used: continue
            j = len(ns & os) / len(ns | os) if (ns | os) else 0.0
            if j > bj: best, bj = oid, j
        if best and bj >= j_thresh:
            np_["id"] = best; used.add(best)
    return new_projects
```

**Incremental assignment (new threads, no full re-cluster).** Score each new thread against
existing community profiles; attach if `top_affinity ≥ assign_thresh` (default 0.45), else mark
as `orphan`. When `orphan_ratio` (orphans / total since last full run) exceeds `recluster_at`
(default 0.15) **or** on the nightly schedule, trigger a full re-cluster + `carry_over_ids`.

## 15. Orchestrator  `pipeline.py`

```python
def cluster_mailbox(feats, *, prev_result=None, params=PARAMS) -> ClusteringResult:
    pidf, ubiquitous, df = participant_idf(feats, params.drop_frac)
    pairs   = candidate_pairs(feats, ubiquitous, df, params.ann_k, params.max_block)
    edges   = build_edges(feats, pairs, pidf, params.tau, params.knn_cap)
    member, modularity = detect_communities(len(feats), edges, params.gamma, params.seed)
    prof    = community_profiles(feats, member)
    assigns = soft_assign(feats, member, prof, pidf, params.ratio, params.min_aff)
    projects, assignments = materialize(feats, assigns)
    projects = label_projects(feats, assigns, projects)
    idx_by_tid = {f.thread_id: i for i, f in enumerate(feats)}
    by_pid = defaultdict(list)
    for a in assignments: by_pid[a["project_id"]].append(idx_by_tid[a["thread_id"]])
    cmap = {p["id"]: c for c, p in zip(member_ids_per_project(projects, assigns), projects)}  # see note
    for p in projects:
        conf, dbg = project_confidence(feats, by_pid[p["id"]], prof, cmap[p["id"]],
                                       params.min_solid)
        p["confidence"], p["debug"] = conf, dbg
    if prev_result: projects = carry_over_ids(projects, prev_result.projects, params.j_thresh)
    return ClusteringResult(projects=projects, assignments=assignments,
        run_meta=dict(seed=params.seed, params_hash=params.hash(), modularity=round(modularity,4),
                      n_communities=len(set(member)), orphan_ratio=0.0))
```

> Implementation note: keep the community-id ↔ project-id map alongside `materialize` rather than
> reconstructing it; the line marked above is illustrative. Thread the community id through
> `materialize` so confidence can find the right profile.

## 16. Parameters & defaults  `params.py`

| Param | Default | Range | Effect |
|---|---|---|---|
| `seed` | 42 | int | Determinism. Never randomize in prod runs. |
| `W` | see §7 | sums to 1 | Similarity component weights — primary tuning surface. |
| `drop_frac` | 0.5 | 0.3–0.7 | Participant counts as ubiquitous if on > frac·N threads. |
| `max_block` | 300 | 100–1000 | Cap on inverted-index list length (hub control). |
| `ann_k` | 25 | 10–50 | ANN neighbours per thread for candidate gen. |
| `tau` | 0.45 | 0.35–0.6 | Min similarity to create a graph edge. ↑ = purer, more splits. |
| `knn_cap` | 15 | 8–30 | Max edges kept per node after sparsify. |
| `gamma` | 1.0 | 0.5–2.0 | Leiden resolution. ↑ = more, smaller projects. |
| `ratio` | 0.65 | 0.5–0.9 | Secondary-membership relative threshold (overlap aggressiveness). |
| `min_aff` | 0.35 | 0.25–0.5 | Absolute floor for any membership. |
| `min_solid` | 4 | 2–8 | Threads for full size-confidence. |
| `assign_thresh` | 0.45 | — | Incremental: attach new thread to existing project. |
| `recluster_at` | 0.15 | — | Orphan ratio that forces a full re-cluster. |
| `j_thresh` | 0.5 | — | Jaccard for ID carry-over. |

`PARAMS.hash()` = stable hash of the param dict, stored in `run_meta` so any result is reproducible.

## 17. Edge cases & failure modes

| Case | Symptom | Handling |
|---|---|---|
| **Thread hijack** (reply to old thread, new topic) | Mixed keywords in one thread | Soft membership lets the thread attach to both; if it's one message, ANN + embedding dominates and it lands with the new topic. Accept; do not split threads in v1. |
| **Standing meeting / weekly sync** | Huge thread spanning everything | High participant overlap with many clusters. c-TF-IDF gives weak distinctive terms ⇒ low confidence; surfaces as its own low-confidence "recurring" project, not merged into a real one. |
| **Shared vendor across projects** | Vendor links two unrelated clusters | Participant IDF down-weights the vendor; embedding + keywords keep them separate. Tune `W["part"]` down if over-merging. |
| **Singleton thread** | One-off, no neighbours | Forms a singleton project, low confidence, hidden below display threshold. Never dropped (invariant §2). |
| **Newsletter slipped past L0** | Dense hub of unrelated threads | Should be filtered at L0; defensively, treat senders with df > drop_frac and zero replies as non-participants. Log for L0 feedback. |
| **Two phases of one project** | Over-split (planning vs launch) | Lower `gamma`, or raise `temporal_affinity` weight. Confidence + adjacency are exposed so the UI can suggest a merge. |
| **Sparse mailbox** (< 50 threads) | Leiden unstable | Below `MIN_THREADS=30`, fall back to agglomerative clustering on the dense similarity matrix (no blocking needed at that scale). |

## 18. Evaluation  `eval/`

Clustering is **overlapping**, so single-assignment metrics (ARI, NMI) are invalid. Use:

- **Extended BCubed precision/recall/F1** (Amigó et al., 2009) — the primary metric; correctly
  handles multi-label items via multiplicity.
- **Omega index** — secondary, agreement on co-membership counts.
- **Pairwise F1** — sanity baseline only.

```python
def ext_bcubed(gold: dict, pred: dict):           # item -> set(labels)
    items = list(gold)
    def mult(c, x, y): return len(c[x] & c[y])
    P = R = 0.0
    for x in items:
        pt = [min(mult(gold,x,y), mult(pred,x,y)) / mult(pred,x,y)
              for y in items if mult(pred,x,y) > 0]
        rt = [min(mult(gold,x,y), mult(pred,x,y)) / mult(gold,x,y)
              for y in items if mult(gold,x,y) > 0]
        P += sum(pt)/len(pt) if pt else 0.0
        R += sum(rt)/len(rt) if rt else 0.0
    P /= len(items); R /= len(items)
    F = 2*P*R/(P+R) if (P+R) else 0.0
    return round(P,3), round(R,3), round(F,3)
```

**Gold fixture format** (`eval/fixtures/*.json`): synthetic mailbox with hand-labeled projects;
threads may list multiple gold projects.

```json
{
  "owner_person_id": "p_owner",
  "threads": [
    {"thread_id": "t1", "participants": ["p_jenna","p_raj"], "keywords": ["atlas","cutover"],
     "subject": "Atlas: cutover plan", "t_start": "2026-04-01", "t_end": "2026-04-03",
     "gold_projects": ["atlas"]},
    {"thread_id": "t9", "participants": ["p_jenna","p_grace"], "keywords": ["atlas","borealis"],
     "subject": "weekly sync", "t_start": "2026-04-02", "t_end": "2026-04-02",
     "gold_projects": ["atlas","borealis"]}
  ]
}
```

**Targets (Definition of Done gates):** on the seed fixture set, extended-BCubed **F1 ≥ 0.75**,
**precision ≥ 0.80** (we favor precision per §1). Multi-gold threads receive ≥2 predicted
projects in ≥80% of cases. Determinism test: two runs byte-identical.

## 19. Module layout & CLI

```
services/enrich/clustering/
  __init__.py
  params.py          # PARAMS dataclass + hash()
  features.py        # §5
  blocking.py        # §6
  similarity.py      # §7
  graph.py           # §8
  communities.py     # §9 + §10
  materialize.py     # §11
  labeling.py        # §12
  confidence.py      # §13
  incremental.py     # §14
  pipeline.py        # §15  (cluster_mailbox)
  eval/
    metrics.py       # §18
    run_eval.py      # loads fixtures, runs pipeline, prints BCubed/Omega
    fixtures/*.json
```

CLI: `python -m enrich.clustering.pipeline --mailbox <id> --out result.json [--prev prev.json]`
Eval: `python -m enrich.clustering.eval.run_eval --fixtures eval/fixtures`

## 20. Performance & scale

- Target mailbox: ~8k threads / ~50k messages. Embeddings: 8k × 768 float32 ≈ 24 MB.
- Blocking keeps candidates ≈ `n · (ann_k + avg_rare_block)` ≈ low millions worst case; similarity
  is a dot product + a few set ops per pair → seconds to low minutes single-process.
- Leiden on a kNN-sparsified graph is near-linear in edges.
- Parallelize Stage C across pair shards if needed; everything else is cheap.
- Memory ceiling is the embedding matrix + candidate set; both fit in RAM at this scale. Beyond
  ~100k threads, move ANN to an on-disk FAISS index and shard similarity.

## 21. Observability & debug artifacts

Emit per run (gated by `--debug`):
- `similarity_graph.graphml` — nodes=threads, edges=sim, for visual inspection.
- `cluster_cards.json` — per project: top c-TF-IDF terms, top members, thread count, confidence,
  cohesion, separation. This is the fastest way to eyeball quality.
- `run_meta` logged: `seed`, `params_hash`, `modularity`, `n_communities`, `orphan_ratio`,
  wall-time per stage. Alert if `modularity < 0.3` (graph too dense → raise `tau`) or
  `n_communities` ≈ `n_threads` (too sparse → lower `tau`).

## 22. Acceptance / Definition of Done

- [ ] `cluster_mailbox` produces a valid `ClusteringResult` honoring all §2 invariants.
- [ ] Deterministic: identical bytes across two runs with the same `seed` and input.
- [ ] Eval gates met on the fixture set: BCubed F1 ≥ 0.75, precision ≥ 0.80; ≥80% of multi-gold
      threads multi-assigned.
- [ ] Soft membership demonstrably assigns the "weekly sync" fixture to ≥2 projects.
- [ ] ID carry-over keeps ≥90% of project IDs stable when 5% of threads are added and re-clustered.
- [ ] Sparse-mailbox fallback path covered by a test (< 30 threads).
- [ ] `--debug` emits `cluster_cards.json` and `similarity_graph.graphml`.
- [ ] All params read from `params.py`; none hardcoded in logic.

## 23. Sprint task breakdown (`@sprint S3`)

| # | Ticket | Depends on | Done when |
|---|---|---|---|
| 3.1 | `features.py` + TF-IDF fit + spaCy NER wiring | L0 output, embed_fn | `ThreadFeatures` built for a fixture mailbox |
| 3.2 | `blocking.py` (IDF, inverted index, hnswlib ANN) | 3.1 | candidate set size sane on 8k-thread fixture |
| 3.3 | `similarity.py` (all 5 components) + unit tests | 3.1 | component tests pass; weights configurable |
| 3.4 | `graph.py` (threshold + kNN sparsify) | 3.3 | edge list stable, deterministic |
| 3.5 | `communities.py` Leiden | 3.4 | hard partition + modularity returned |
| 3.6 | `communities.py` soft membership | 3.5 | multi-assignment on sync fixture |
| 3.7 | `materialize.py` + `confidence.py` | 3.6 | Projects with members, span, confidence |
| 3.8 | `labeling.py` (c-TF-IDF + fallback + sticky renames) | 3.7 | labels on all projects; override table works |
| 3.9 | `incremental.py` (carry-over + orphan trigger) | 3.7 | ID stability test passes |
| 3.10 | `eval/` (BCubed, Omega, fixtures, run_eval) | 3.7 | gates in §22 evaluated in CI |
| 3.11 | `pipeline.py` wiring + CLI + `--debug` artifacts | all above | end-to-end run on fixture green |

## 24. Open decisions (confirm before/while building)

- Embedding model + dimension — MUST match L2. Pin in shared config.
- Is org-directory data available? If so, add a directory-derived participant role feature to §7
  (boosts separation between internal-heavy and external-heavy projects).
- Where the **existing RAG query pipeline** already produces thread embeddings — reuse them in §5
  instead of re-embedding. `// TODO: confirm output shape and wire in.`
- LLM labeling on or off for the first release (determinism vs. nicer titles).
- Display confidence threshold for the project view (coordinate with spec 02; default 0.4).
