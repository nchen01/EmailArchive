# S13 Relationship Map / Tree Plan

**Status:** ✅ Implemented (2026-06-24). Relationship Map ships as a new tab beside
Network Map (not a replacement). It is **API-derived live** from existing L1
tables — no `relationship_edge` table was added (Q5); see the persistence note in
`services/relationships/derive.py` for when to materialize. Backend:
`services/relationships/` (contracts, params, derive) + `GET /api/relationship-map/
{mailbox_id}`. Frontend: `/app/relationships` with owner/project/org/graph modes,
filters, force-graph render, and an evidence drawer. 13 derivation tests; live-
validated on the smoke mailbox. See the "Manual demo script (as shipped)" at the
end of this doc.

**Purpose:** Rework the Network Map from a mostly owner-centric flat graph into a more navigable relationship map that can show how people, projects, threads, and organizations relate to each other across the mailbox.

## Product Intent

The current Network Map is useful for answering:

> Who did the mailbox owner communicate with?

The future relationship map should answer richer questions:

- Who works together across projects?
- Which people appear together repeatedly in threads?
- Which contacts bridge multiple projects?
- Which organizations are connected through the mailbox owner's work?
- Which relationships are direct owner relationships versus inferred co-participation relationships?
- Which threads or projects explain an edge?

The goal is not only a prettier graph. The goal is a more truthful relationship model for email, where relationships are often formed by:

- Direct message exchange.
- Shared thread participation.
- Repeated co-presence across project threads.
- Project membership.
- Organization / domain affiliation.
- Handoff or responsibility patterns.

## Important Design Input

Do not make this a pure tree if the data is naturally a graph.

Email relationships are cyclic and many-to-many. A strict tree can be useful for navigation, but it can also lie by forcing every person into one parent/child path. The better product direction is:

> Build a graph-backed relationship model with tree-like views.

That means the data remains a graph, but the UI can offer layouts that feel easier than a flat force graph.

Recommended layouts:

1. **Owner Ego Tree**
   - Owner at root.
   - First layer: direct contacts.
   - Second layer: people frequently co-present with those contacts in threads/projects.
   - Best for quick handoff orientation.

2. **Project Relationship Tree**
   - Project at root.
   - First layer: core project members.
   - Second layer: related contacts, vendors, stakeholders, threads.
   - Best for "who is involved in this project?"

3. **Organization / Domain Tree**
   - Organization/domain at root.
   - People grouped under org.
   - Projects/threads connecting orgs.
   - Best for vendor/customer/account understanding.

4. **Relationship Graph View**
   - Optional graph layout for power users.
   - Shows non-owner edges and cross-links that a tree view hides.
   - Must distinguish direct communication from co-participation.

The UI can present the default as tree-like while keeping graph truth underneath.

## Data Model Direction

The current `Edge` concept is owner-centric: owner-to-person communication edges. S13 likely needs a second relationship layer rather than overloading `Edge`.

Potential new materialized object:

```text
RelationshipEdge
- id
- mailbox_id
- source_person_id
- target_person_id
- relationship_type
- evidence_kind
- project_ids
- thread_ids
- source_message_ids
- weight
- confidence
- first_seen
- last_seen
```

Possible `relationship_type` values:

- `direct_exchange` - one person sent to/replied to another.
- `thread_copresence` - people appeared in the same thread.
- `project_copresence` - people are assigned to the same project.
- `org_affiliation` - people share an organization/domain.
- `bridge` - person connects multiple otherwise separate groups.

Possible `evidence_kind` values:

- `message_headers`
- `thread_ids`
- `project_ids`
- `domain`

Important: every relationship shown in the UI needs explainable evidence. It may not need a full L3 citation claim, but the user should be able to answer:

> Why are these two people connected?

## Edge Semantics

Do not let edge weight imply importance or accomplishment.

The map should distinguish:

- Communication volume.
- Recency.
- Project relevance.
- Role / stakeholder type.
- Evidence strength.
- Directionality, where meaningful.

Example labels:

- "Direct email exchange"
- "Appeared together in 4 project threads"
- "Both assigned to Nexus Auth"
- "Same organization: acme.corp"
- "Bridge contact across 3 projects"

Avoid labels like:

- "Important"
- "High impact"
- "Key person"

Those require stronger evidence than email topology alone.

## Privacy / Sensitivity Rules

Relationship extraction must preserve the existing privacy posture.

Rules:

- Exclude sensitive messages and threads by default.
- If any thread contains sensitive content, do not use that thread for relationship inference unless a future permission model explicitly allows it.
- Do not expose raw message bodies in the relationship map.
- Evidence drilldowns should use safe metadata: subject, date, participants, project, snippet only if already allowed.
- Do not create relationships based only on HR/legal/personal threads.

Open question: should a sensitive relationship be hidden entirely, or should the UI show a generic "relationship hidden by policy" count? Recommended default: hide entirely for MVP.

## UI Direction

The current flat graph can feel visually interesting but hard to navigate. S13 should make the relationship map more structured.

Recommended UI:

- Left panel: mode selector.
  - Owner tree
  - Project tree
  - Organization tree
  - Graph view
- Center: interactive map/tree.
- Right drawer: selected node or edge detail.
- Search: person, project, org/domain.
- Filters:
  - relationship type
  - role
  - project
  - recency
  - minimum evidence count

Node types:

- Owner
- Person
- Project
- Organization
- Thread group

Visual encoding:

- Shape or icon for node type.
- Color for role or type, not arbitrary decoration.
- Edge style for relationship type.
  - solid: direct exchange
  - dashed: co-participation
  - dotted: org/domain affiliation
- Edge thickness for evidence count, with a tooltip that clarifies it is volume/evidence count, not importance.

## Tree-Like View Without Lying

Because relationship data is a graph, a tree layout needs rules for duplicate nodes.

Options:

1. **Duplicate nodes visually**
   - Same person can appear under multiple projects.
   - UI marks duplicates with "also appears in..."
   - Easy to understand in tree mode.

2. **Single node with cross-links**
   - More faithful graph representation.
   - Harder to read in a tree.

3. **Primary placement + related links**
   - Choose one primary parent by strongest evidence.
   - Show related projects/people in the drawer.
   - Recommended first version.

Recommended MVP approach:

Use primary placement + related links. It keeps the tree readable while still exposing graph reality in the details drawer.

## Backend Implementation Plan

### S13.1 Relationship Edge Spec

Define the data contract for person-person and person-project relationship edges.

Decisions needed:

- Is this a new DB table or an API-only derived object?
- Do relationships persist like `Edge`, or compute live from threads/projects?
- Which relationship types ship first?

Recommendation:

Start API-derived for one sprint if performance allows. Persist later only if query cost becomes painful.

### S13.2 Relationship Extraction

Build relationship derivation from existing L1/L2 objects:

- Person identities.
- Thread participants.
- Message sender/recipient metadata.
- ThreadProjectAssignment.
- ProjectMember.
- Existing Edge table.

Compute:

- Owner direct edges.
- Non-owner co-participation edges.
- Project co-membership edges.
- Org/domain grouping.
- Bridge scores.

Important: use deterministic ordering and stable IDs.

### S13.3 API Contract

Add endpoint such as:

```text
GET /api/relationship-map/{mailbox_id}
```

Possible query params:

- `mode=owner|project|org|graph`
- `root_id=<person/project/org id>`
- `min_weight`
- `relationship_types`
- `project_id`
- `recency_days`

Response shape:

```text
{
  "root": Node,
  "nodes": RelationshipNode[],
  "edges": RelationshipEdge[],
  "groups": RelationshipGroup[],
  "layout_hint": "tree" | "graph",
  "generated_from": {
    "threads": int,
    "projects": int,
    "messages": int
  }
}
```

### S13.4 Frontend Relationship Map

Either extend the current Network Map or add a new "Relationship Map" tab.

Recommendation:

Add a new tab first, keep Network Map intact. Once the new map proves better, consider replacing Network Map.

Potential libraries:

- Continue with `react-force-graph-2d` for graph mode.
- Use a tree layout library only if it handles collapse/expand well.
- Consider D3 hierarchy/tree if custom layout is needed.

### S13.5 Evidence Drawer for Relationships

Clicking an edge should show:

- Relationship type.
- Why connected.
- Evidence count.
- Shared projects.
- Shared threads.
- Recent message metadata.
- Caveat: volume is not importance.

No raw sensitive content.

### S13.6 Evaluation / QA

Create fixtures for:

- Owner direct contact.
- Two non-owner contacts on same project thread.
- Vendor + internal stakeholder.
- One bridge contact across multiple projects.
- Sensitive thread excluded.
- Same-domain org grouping.

Acceptance:

- Non-owner relationships appear when evidenced.
- Sensitive relationships are excluded.
- Tree mode remains readable.
- Graph mode shows cross-links.
- Edge drawer explains why an edge exists.
- No edge implies accomplishment or importance.

## Product Lead Decisions Logged

These decisions were recorded after product review. Engineers should follow these defaults unless the product lead explicitly revises them before S13 implementation starts.

### Q1. Replace Network Map or Add a New View?

Should the relationship map replace the current Network Map, or live beside it as a new tab first?

Recommendation: new tab first. Do not remove the current Network Map until the new view proves itself.

Decision: add Relationship Map as a new view/tab. Do not replace the current Network Map. The product should support both the current user-centric network map and the new relationship-centric, project-centric, org-centric tree/graph views.

### Q2. Default Root

Options:

- Mailbox owner.
- Most active project.
- User-selected project.
- Overview mode with multiple groups.

Recommendation: owner by default, with quick switches to project/org roots.

Decision: owner by default. This is a fast-handoff product, so the first view should orient around the covered mailbox owner. The UI should still make it easy to cycle to project, organization/domain, and relationship-centric roots.

### Q3. Strict Tree or Graph-Backed Tree?

Do you want a strict tree, or a graph-backed tree-like view?

Recommendation: graph-backed tree-like view. Email is not truly hierarchical.

Decision: graph-backed tree-like layout. The UI may feel like a tree, but the underlying model must preserve cross-links and many-to-many relationships.

### Q4. Relationship Scope

Recommendation: start with project-relevant / non-noise / non-sensitive threads. All co-thread participants can get noisy fast.

Decision: use the recommended scope first: project-relevant, non-noise, non-sensitive threads. Add a minimum evidence threshold if early results are noisy.

### Q5. Persist Relationship Edges?

Recommendation: derive live first from existing tables. Persist only if performance or repeatability becomes a problem.

Decision: derive live for now. Keep a note that this may need to become a persisted `relationship_edge` table later if performance, repeatability, or auditing needs demand it.

### Q6. Weak / Inferred Relationship Display

Recommendation: show them muted, with evidence count and relationship type. Avoid hiding them entirely unless below a minimum evidence threshold.

Decision: show weak relationships muted with evidence count and relationship type. Hide only relationships below a minimum evidence threshold.

### Q7. Organization / Domain Nodes

Recommendation: yes, for organization tree mode. Domain grouping is one of the clearest ways to make mailbox relationships readable.

Decision: domains/orgs are first-class nodes for organization tree mode.

### Q8. Bridge Contact Definition

Options:

- Person appears in multiple projects.
- Person connects two orgs.
- Person connects otherwise separate project clusters.

Recommendation: start with "appears in multiple projects" because it is explainable and easy to compute.

Decision: start with "appears in multiple projects." This is explainable, deterministic, and easy to show in the edge/node drawer.

### Q9. Historical / Stale Relationships

Recommendation: yes, but filter by recency and visually mute stale edges.

Decision: include stale relationships, but visually mute stale edges and provide a recency filter.

### Q10. Demo Story

Possible story:

> "I am covering this mailbox. Show me the people around Nexus Auth, then show which external vendors and internal stakeholders connect to it."

The map should support that flow.

Clarification needed: this is not the landing page copy by itself. The demo story is the exact guided workflow used to prove the feature in a live demo, QA pass, or product walkthrough. It determines which mailbox/project is used, which tree mode is opened first, which node/edge the presenter clicks, and what relationship evidence is expected to appear.

Current product direction: because the product is for fast handoff, the default route should open the owner tree. The demo can then switch into a project tree, such as "Nexus Auth," to show vendors, internal stakeholders, bridge contacts, and evidence-backed relationship details.

Decision still needed before implementation:

- Which mailbox/project should be the canonical demo path?
- Which exact user action sequence should the engineer validate manually?
- Should the landing page mention this workflow, or should it remain an in-app demo/readiness script only?

## Product Lead Recommendation

Do this after S12, not before.

S12 makes the product navigable and credible. S13 makes the relationship model richer. If S13 happens before S12, the team may build powerful map logic into an app shell that still feels hard to use.

Recommended sprint order:

1. S12 Product Shell + Landing Experience.
2. S13 Relationship Map / Tree.
3. Later: richer relationship scoring and persisted relationship edges if needed.

## Definition of Done

- Relationship map view exists without breaking current Network Map.
- Non-owner relationships can be shown when evidenced by safe threads/projects.
- Tree-like layout is readable and does not pretend the data is strictly hierarchical.
- Edge details explain why two nodes are connected.
- Sensitive content remains excluded.
- Volume is never presented as accomplishment or importance.
- Backend tests cover relationship derivation.
- Frontend build passes.
- Manual demo script documented.

## Manual demo script (as shipped)

No frontend test runner exists in this repo, so the UI is verified by build +
this walkthrough. Backend derivation is covered by `tests/test_s13_relationships.py`
(13 tests). The relationship map uses no Voyage/Anthropic calls, so this
walkthrough is unbilled.

1. Start the stack: `scripts/run_backend.ps1` and `scripts/run_frontend.ps1`.
2. Open `http://localhost:5173/app` and load mailbox
   `e21c187a-956a-47ee-92aa-b21badd16f4d`.
3. Click the **Relationship Map** tab. Confirm it opens in **Owner tree** mode
   with the mailbox owner at the root and direct contacts around it (solid edges).
4. Note the legend: solid = direct exchange, dashed = co-participation, dotted =
   org affiliation, and "line thickness = evidence volume, not importance", plus
   the eligible/excluded thread counts.
5. Switch to **Project tree** and pick a project root from the dropdown (e.g.
   Nexus Auth / Connection Pool if present). Confirm members and org/domain nodes
   appear where evidenced.
6. Switch to **Organization tree** — confirm domain/org nodes group their people;
   switch to **Graph view** — confirm cross-links (bridge/co-presence) appear.
7. Click an edge — the right drawer explains the relationship ("Both assigned to
   …", "Appeared together in N project threads", "Same organization: …"), shows
   evidence count with the "volume, not importance" caveat, shared projects/
   threads, and message-id citations when present.
8. Use the filters: toggle relationship types, set a recency window, raise
   minimum evidence — confirm the map updates and weak/stale edges render muted.
9. Confirm no HR/legal/personal relationship is shown (sensitive threads are
   excluded whole; on the smoke mailbox ~383 of ~419 threads are excluded).
10. Click the **Network** tab — confirm the original Network Map still works
    unchanged.

**S14 evidence note:** in the edge drawer, `direct_exchange` (`message_headers`)
edges now show clickable source-message IDs that open the S14 source-detail
view; structural `project_copresence` / `thread_copresence` / `org_affiliation`
edges intentionally show a provenance note ("backed by shared project membership
/ thread participation / organization affiliation") instead of fabricated
Message-IDs.

Resolved (canonical demo, was Q10): the project-tree default uses the
recommended coverage ordering. For the `puluo` mailbox (`e21c187a`) the expected
default root is **Ml Engineer**. Users can sort project roots by **Recommended**,
**Recent**, **Relationship-rich**, or **A–Z**. "Account Https" is intentionally
demoted in Recommended / Recent / Relationship-rich because it is low-value /
automated-looking.
