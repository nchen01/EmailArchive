# Spec 02 — Surface: Project View

> The second UI surface. Where a stand-in sees one project end to end: who's on it,
> who to ask, what state it's in, and what's actually been done — every claim cited.

**Owners:** full-stack. **Depends on:** L1 `Project`, `Person`, `Edge`, `Event`; L3 for summaries.

---

## 1. Purpose

The network map answers "who does this person talk to". The project view answers
"I'm covering project X — orient me". It is also the surface that most stress-tests
project-clustering quality (spec 01 §6): if the members and threads look wrong here,
clustering needs work.

## 2. Data sources

| Component | Source object(s) |
|---|---|
| Header (label, state, span, confidence) | `Project` |
| Members by role | `Project.member_ids` → `Person` (+ `Edge` for volume) |
| Who to ask | top `Person` by `Edge.weight` within the project, grouped by `Role` |
| Recent threads | `Project.thread_ids` → `Thread` (sorted by recency) |
| Evidenced activity | `Event` where `project_id == this`, grouped by `EventType` |
| State | derived from latest `Event` types + thread recency (see §4) |

## 3. Components

1. **Header** — project label (editable; renames are sticky), status pill, time span,
   and a small confidence indicator (clustering confidence).
2. **Metric row** — members, threads, last activity.
3. **Who to ask** — the headline component. 2–4 contacts ranked by in-project edge weight,
   role-colored, each a one-click "ask about this contact" (→ L3 cited summary).
4. **Members** — grouped by role with per-member in-project email counts.
5. **Recent threads** — last N threads with subject, participants, timestamp; click → opens
   the source thread (provenance).
6. **What's been done** — `Event`s rendered by epistemic grade:
   `outcome` (confirmed) > `did` (actioned) > `proposed` (pending). Each line shows its
   citation chip(s). Never present `proposed` as `outcome`.

## 4. Derived state (rules)

```python
def project_state(p: Project, events: list[Event], threads: list[Thread]) -> str:
    if days_since(max(t.last for t in threads)) > 30:      return "stale"
    if any(e.type == EventType.OUTCOME for e in recent(events)): return "shipping"
    return "active"
```

## 5. API contract

```http
GET /api/projects/{project_id}
```
```jsonc
{
  "id": "prj_8f3a",
  "label": "Atlas Migration",
  "state": "active",
  "confidence": 0.82,
  "start": "2026-02-03T00:00:00Z",
  "end": "2026-05-28T00:00:00Z",
  "metrics": { "members": 6, "threads": 41, "last_activity": "2026-05-28T14:10:00Z" },
  "who_to_ask": [
    { "person_id": "p_jenna", "name": "Jenna Park", "role": "internal",
      "in_project_count": 88, "weight": 0.91 }
  ],
  "members": [ /* Person + in_project_count, grouped client-side by role */ ],
  "recent_threads": [
    { "thread_id": "t_22", "subject": "Atlas: cutover plan",
      "participants": ["p_jenna","p_raj"], "last": "2026-05-28T14:10:00Z" }
  ],
  "activity": [
    { "type": "outcome", "summary": "Staging cutover completed",
      "actor": "p_raj", "source_message_ids": ["<CA+abc@mail>"], "confidence": 0.88 }
  ]
}
```
Hard invariant: every `activity` item ships with ≥1 `source_message_ids`; the API rejects
any event without one.

## 6. Acceptance

- `who_to_ask` returns the correct top contacts for a seed project, role-labeled.
- Clicking a contact issues an L3 query scoped to (person, project) and renders a cited answer.
- Every recent thread links back to its real source thread.
- No activity line renders without a citation chip.
- A `proposed` event is never shown under the "confirmed / outcome" heading.

## 7. Sprint tasks  ·  `@sprint S3`

- [ ] `GET /api/projects/{id}` assembling §2 sources.
- [ ] Project-state derivation (§4) + unit tests on fixtures.
- [ ] Frontend: header + metric row + members-by-role.
- [ ] Frontend: "who to ask" with per-contact L3 hook.
- [ ] Frontend: recent threads with source-thread deep link.
- [ ] (`@sprint S4`) "What's been done" once `Event` extraction lands.
