# Spec 05 — Surface: Network Map

> The first UI surface. The mailbox owner at center; contacts as nodes colored by role; edge
> weight = contact frequency and recency; filterable by role. Click a node for the relationship
> detail and the threads behind it.

**Owners:** full-stack. **Depends on:** L1 `Person`, `Edge`, `Role` (role inference, spec 01 §5);
DB migrations (spec 04) + read API. **Feeds into:** project view (spec 02) via contact click.

---

## 1. Purpose

Answers the first question a stand-in asks: *"Who does this person talk to, and in what capacity?"*
It is also the surface that stress-tests identity resolution quality (spec 01 §3): if two nodes
appear for the same person, or a wrong role is shown, the L1 logic needs work.

The network map renders the relationship graph materialized by L1. It is a **read surface** —
everything on screen is computed offline; no L3 synthesis is triggered until the user clicks into
a contact and requests an L3 summary (deferred to S4).

---

## 2. Data sources

| Component | Source object(s) |
|---|---|
| Owner node | `mailbox.owner_email` → `Person` |
| Contact nodes | All `Person` rows (excl. owner) with an `Edge` |
| Node color | `Person.role` → role color map |
| Node size | `Edge.weight` (normalized to `[min_radius, max_radius]` client-side) |
| Edge thickness | `Edge.weight` (normalized client-side) |
| Contact detail panel | `Edge` stats + `Thread`s where contact appears |

---

## 3. Components

1. **Graph canvas** — force-directed or static layout. Owner node is pinned at center.
   Contact nodes orbit by role cluster (internal nodes inner ring, external nodes outer ring is a
   reasonable default layout hint; the user can drag). Edge thickness scales with `Edge.weight`.
   Nodes are role-colored (see §4). Hovering a node shows the contact's name + role.

2. **Role legend** — color key mapping `Role` enum values to colors. Counts contacts per role
   in parentheses. Clicking a legend entry toggles visibility of that role's nodes.

3. **Toolbar** — role filter (multi-select, defaults to all visible); project filter (S3+,
   deferred); time-range slider (deferred). Keep the toolbar minimal for S2.

4. **Contact detail panel** (right drawer, opens on node click):
   - **Header** — name, canonical email, role + confidence badge, org domain.
   - **Relationship stats** — `message_count`, `sent_to_count`, `received_count`,
     `first_contact`, `last_contact`, `weight`.
   - **Recent shared threads** — last N threads where both owner and contact appear
     (sorted by `t_end` desc). Each row: subject, other participants, timestamp; click → opens
     source thread (provenance deep link).
   - **Ask about this contact** — one-click L3 query scoped to (mailbox, person); deferred to
     S4. Render as a disabled button in S2 with a tooltip "Coming in a future release."

5. **Empty state** — shown when the graph has no edges yet (pipeline hasn't run). Link to
   trigger ingest.

---

## 4. Role → color map (canonical, shared with frontend)

| `Role` value | Display label | Suggested color |
|---|---|---|
| `internal` | Internal | `#4F81BD` (blue) |
| `manager` | Manager | `#9B59B6` (purple) |
| `account_exec` | Account Exec | `#E67E22` (orange) |
| `lead` | Lead / Prospect | `#27AE60` (green) |
| `vendor` | Vendor / Partner | `#E74C3C` (red) |
| `unknown` | Unknown | `#95A5A6` (grey) |

These are suggestions — the frontend can override. The **role value strings** are canonical and
must match `ekc_schemas.Role`. Never hard-code display labels or colors in the API response.

---

## 5. API contract

### 5.1 Full graph

```http
GET /api/network-map/{mailbox_id}
```

Query params (all optional):
- `roles` — comma-separated `Role` values to include (default: all)
- `min_weight` — exclude edges below this threshold (default: `0.0`)

Response:
```jsonc
{
  "owner": {
    "person_id": "uuid",
    "name": "Alex Rivera",
    "canonical_email": "alex@acme.com"
  },
  "nodes": [
    {
      "person_id": "uuid",
      "name": "Jenna Park",
      "canonical_email": "jenna@acme.com",
      "role": "internal",
      "role_confidence": 0.95,
      "org_domain": "acme.com",
      "weight": 0.82,
      "message_count": 4,
      "last_contact": "2026-04-04T16:00:00Z"
    }
    // ... one node per contact with an Edge
  ],
  "edges": [
    {
      "person_id": "uuid",          // matches a node's person_id
      "weight": 0.82,
      "message_count": 4,
      "sent_to_count": 1,
      "received_count": 3,
      "first_contact": "2026-04-01T09:00:00Z",
      "last_contact": "2026-04-04T16:00:00Z"
    }
  ]
}
```

Hard invariants:
- Owner does **not** appear in `nodes` or `edges`.
- Every `edge.person_id` has a corresponding entry in `nodes`.
- `nodes` and `edges` are ordered by `weight DESC` (most important contact first).
- Identity-merged persons appear as **one node** (e.g. `jenna@acme.com` and `j.park@acme.com`
  → one node, canonical email is `Person.canonical_email`).

### 5.2 Contact detail

```http
GET /api/network-map/{mailbox_id}/contact/{person_id}
```

Response:
```jsonc
{
  "person": {
    "person_id": "uuid",
    "name": "Jenna Park",
    "canonical_email": "jenna@acme.com",
    "all_emails": ["jenna@acme.com", "j.park@acme.com"],
    "role": "internal",
    "role_confidence": 0.95,
    "org_domain": "acme.com"
  },
  "edge": {
    "weight": 0.82,
    "message_count": 4,
    "sent_to_count": 1,
    "received_count": 3,
    "first_contact": "2026-04-01T09:00:00Z",
    "last_contact": "2026-04-04T16:00:00Z"
  },
  "recent_threads": [
    {
      "thread_id": "uuid",
      "subject": "Weekly sync notes",
      "other_participants": ["aiko@acme.com", "grace@acme.com"],
      "last": "2026-04-04T16:00:00Z",
      "message_count": 1
    }
    // last 10, sorted by t_end desc
  ]
}
```

`recent_threads` includes threads where **both** the owner and this contact appear in
`Thread.participants`. `other_participants` excludes both owner and this contact.

---

## 6. Role inference (gate for S2)

Role inference (spec 01 §5) must land before the network map is meaningful — otherwise every
node renders as `Role.UNKNOWN`. Implement role inference in-memory first (same pattern as
identity and graph: pure function, fixture-validated), then wire it into `run_enrichment`.

**S2 role inference minimal implementation** (rules-based, confidence-scored):

| Signal | Rule | Role |
|---|---|---|
| Sender domain ∈ internal domains | — | `internal` (confidence 1.0) |
| Sender domain ∉ internal domains AND display name/domain matches known vendor | — | `vendor` |
| Contact is in CC more than To across their threads | — | lean `manager` |
| Contact initiates threads to owner (sender) more than recipient | combined with ext. domain | `account_exec` or `lead` |
| No strong signal | — | `unknown` (confidence 0.0) |

The gold labels in `fixtures/gold/roles.json` are the acceptance gate.

---

## 7. Acceptance

Gates run against the fixture's L1 output (after identity resolution + graph + role inference):

- [ ] 10 contact nodes returned (all persons minus owner `alex@acme.com`; owner has no Edge).
- [ ] `jenna@acme.com` and `j.park@acme.com` resolve to **one** node, not two.
- [ ] Node roles match `fixtures/gold/roles.json` for all 10 contacts (role inference gate).
- [ ] Owner (`alex@acme.com`) is absent from `nodes` and `edges`.
- [ ] Jenna's node: `received_count >= 3`, `weight > 0`.
- [ ] Noise message `pmsg_014` contributes `0` to any edge (already enforced in graph builder).
- [ ] Contact detail for Jenna returns ≥ 3 `recent_threads` (T1, T2, T7 all include her).
- [ ] Identity-merged addresses (`j.park@acme.com`) appear in `person.all_emails` on the detail
      endpoint, not as a separate node.
- [ ] `GET /api/network-map/{mailbox_id}?roles=internal` returns only internal contacts.

---

## 8. Sprint tasks  ·  `@sprint S2`

### Role inference (gates the node role colors)
- [ ] `services/enrich/roles.py` — `infer_roles(people, messages, edges, params) -> list[Person]`
      with updated `role` + `role_confidence`. Rules-based v1; fixture gold as eval.
- [ ] Wire `infer_roles` into `services/enrich/pipeline.run_enrichment`.
- [ ] `tests/test_l1_roles.py` — check roles match `gold/roles.json` for internal/manager/AE/
      lead/vendor distinctions present in the fixture.

### Persistence (spec 04 prerequisite — must land before surface)
- [ ] Alembic migrations 4.1–4.4: `mailbox`, `sync_state`, `audit_log`, `schema_meta`, `thread`,
      `message`, `message_attachment`, `org`, `person`, `identity`, `edge` tables + indexes.
- [ ] SQLAlchemy models + Pydantic↔row mappers (ticket 4.7).
- [ ] Round-trip test: persist L0+L1 output from fixture run, reload, compare to original objects.
- [ ] Idempotency test: re-run ingest, assert no new rows (dedup by `message_id_header`).

### API
- [ ] `GET /api/network-map/{mailbox_id}` assembling §5.1 shape from persisted data.
- [ ] `GET /api/network-map/{mailbox_id}/contact/{person_id}` assembling §5.2 shape.
- [ ] Role filter query param (`?roles=internal,manager`).
- [ ] FastAPI route, Pydantic response models (derive from `ekc_schemas`, do not fork).

### Frontend
- [ ] Graph canvas: force-directed layout, owner pinned at center, role-colored nodes, weighted
      edges. Uses `react-force-graph-2d` (D9 — resolved, do not re-open).
- [ ] Role legend with toggle.
- [ ] Contact detail drawer: header + stats + recent threads.
- [ ] "Ask about this contact" button (disabled, S4 tooltip).

---

## 9. Open decisions

- **Graph library choice:** resolved as `react-force-graph-2d` (D9 in `docs/decisions.md`). Not an open decision.
- **Layout algorithm:** force-directed (default, interactive) vs static hierarchical. Force-
  directed is the natural fit; note that with 10 nodes the fixture is small — test the layout at
  ~50 nodes before committing.
- **`min_weight` default:** `0.0` means every contact with any message appears. A non-zero default
  (e.g. 0.1) declutters. Decide when real mailbox data is available; leave at 0.0 for now.
- **Thread provenance link format:** what does "click to open source thread" actually open in
  the MVP? (Gmail link via `provider_id`? Internal thread view? TBD.)
