# S12 Product Shell and Landing Experience Plan

> **D14 positioning update:** This historical S12 plan created the first product
> shell and landing page. The current MVP direction is now employee-initiated
> audited handoff packages. Keep the professional shell, but future landing and
> overview copy should lead with creating, reviewing, and publishing a scoped
> handoff package rather than generic mailbox search.

**Status:** ✅ Implemented (2026-06-24). App shell + client routing, workspace
overview, status screen, marketing landing page, Cover-for-me onboarding
(suggested questions), and conservative project/network polish are all shipped.
Frontend-only; no backend/schema/AI/retrieval/clustering changes. See the
"Manual demo script" section below for the exact walkthrough; the per-task notes
in the Implementation Plan section remain as the original brief.

**Purpose:** Give the MVP a polished front door and easier navigation while preserving the existing working app surfaces. This sprint should make the product feel trustworthy, inspectable, and easy to demo. It should not add new AI capabilities, providers, retrieval logic, or clustering behavior unless a narrow change is required for UX polish.

## Product North Star

The product should feel like:

> I can safely understand an authorized mailbox, see what matters, and hand off work without guessing.

The landing page and workspace should reinforce the same message:

> This is not a chatbot guessing from an inbox. It is a cited continuity workspace built from structured email evidence.

Primary positioning:

> Email continuity for coverage, handoffs, and institutional memory.

Supporting copy:

> Turn an authorized mailbox into a cited map of people, projects, and work history so a teammate can get oriented without guessing.

Avoid positioning the product primarily as an "AI email assistant." The stronger product frame is continuity, evidence, coverage, and operational trust.

## Product Shape

Split the experience into two layers.

Public / entry layer:

- Landing page
- Demo entry point
- Setup or preflight screen
- Clear "Open workspace" action

Workspace layer:

- Overview
- Network Map
- Projects
- Cover for Me
- Evidence and citations
- Demo readiness / mailbox status

The landing page should not replace the app. It should explain the product, establish trust, and route into the actual workspace quickly.

## Landing Page Goals

The landing page should answer these questions fast:

- What is this?
- Who is it for?
- What can I do immediately?
- Why should I trust the answers?
- How do I start?

Primary CTA:

- Open demo workspace

Secondary CTA:

- Check setup

Visual direction:

- Use a real product screenshot or dashboard preview.
- Avoid abstract AI gradients as the main visual.
- Avoid decorative blobs, orbs, or one-note palettes.
- Keep it professional, calm, and operational.

## Landing Page Sections

1. Hero

- Product name or category as the headline.
- One clear sentence explaining the product.
- Primary CTA: Open demo workspace.
- Secondary CTA: Setup / Preflight.
- Product screenshot or realistic product preview.

2. Three Core Surfaces

- Network Map: who works with whom.
- Projects: what work is active.
- Cover for Me: ask cited questions across the mailbox.

3. Trust Model

- Every answer is cited.
- Sensitive content is excluded by default.
- No citation, no claim.
- Retrieval status is visible.
- OAuth tokens and provider secrets are not stored in the app DB or logs.

4. How It Works

- Ingest authorized mailbox data.
- Normalize and structure contacts, threads, relationships, projects, events, and embeddings.
- Retrieve relevant evidence.
- Synthesize cited answers.

5. Demo / Setup Panel

- Mailbox status.
- Embeddings status.
- Retrieval status.
- Synthesis status.
- Open workspace button.

## Workspace Navigation

Move from a tab-only feel toward a polished app shell.

Recommended navigation:

- Overview
- Network
- Projects
- Cover for Me
- Setup / Status

Header should show:

- Current mailbox.
- Health indicator.
- Last ingest or available mailbox stats where cheap.
- Retrieval / synthesis readiness indicator.

The app shell should feel like a workbench, not a marketing page. Prefer dense, useful, low-friction UI over oversized cards or decorative sections.

## Overview Screen

Add an Overview screen as the default workspace entry point instead of dropping directly into Network Map.

Overview should show:

- Mailbox loaded.
- People found.
- Projects found.
- Messages embedded.
- Retrieval status.
- Synthesis status.
- Recent or important projects.
- Suggested questions.

Suggested questions can include:

- What is the state of Nexus Auth?
- Who should I ask about the latency incident?
- What changed on the DataPipe SOW?
- What work appears unresolved?

The point is to help users who do not yet know what to ask.

## Cover For Me UX

Cover for Me should become the centerpiece of the workspace.

Improve it with:

- Suggested query chips.
- Recent successful queries, if cheap and local-only.
- Retrieval status shown inline.
- Evidence drawer or popover.
- Grouped citations.
- Clear no-evidence state.
- Suggested alternatives when the query misses known projects or contacts.

Important behavior:

- Preserve no citation, no claim.
- Do not expose sensitive messages.
- Do not claim an answer is unavailable because of HR/legal filtering unless the backend can support that distinction safely.

Example safe fallback:

> No email evidence found for this query. Try asking about a specific project or contact name from the mailbox.

Potential future fallback, only if backend support exists:

> No email evidence found for this query. Sensitive HR content may be excluded by policy.

## Evidence UX

Evidence inspection is the most important trust improvement.

When a citation chip is clicked, show an evidence drawer or popover with:

- Subject.
- Date.
- Message-ID header.
- Snippet.
- Retrieval source/status if available.
- Clear statement that the answer is grounded in cited messages.

Constraints:

- Use existing `supporting_evidence`.
- Do not invent evidence.
- Do not expose provider tokens, raw MIME, OAuth data, or secret values.
- If evidence metadata is missing, degrade gracefully.

Repeated citation labels should be grouped or collapsed so correct answers do not feel noisy.

## Project UX

Projects should feel like handoff packets.

Improve Project pages with:

- Cleaned display labels.
- Low-confidence / uncategorized grouping where appropriate.
- Summary or state at top.
- Timeline of cited activity.
- Members / people involved.
- Related messages.
- "Ask about this project" action.

Important: display-label cleanup should not mutate clustering semantics unless separately scoped. The first pass should be conservative and display-only, or use an existing label override path if available.

Examples of labels that need polish:

- Email Govdelivery
- Account Https
- unknown - Sep 2021

These may be technically valid but feel rough in a demo.

## Network Map UX

Make the graph easier to interpret.

Potential improvements:

- Search contacts.
- Filter by role.
- Click person to open side panel.
- Show why an edge exists.
- Show top shared threads/projects.
- Make clear that edge weight is communication volume, not impact.

Do not let volume imply accomplishment.

## Visual Direction

The look should be:

- Quiet.
- Professional.
- Operational.
- Evidence-forward.
- Readable.

Good references:

- Linear-like clarity.
- Notion-style calm information density.
- Retool-style practical admin surfaces.
- Security/compliance dashboard discipline.

Avoid:

- Oversized SaaS hero cards inside the app.
- Decorative gradient blobs.
- Consumer chatbot styling.
- One-note purple/blue gradient themes.
- UI copy that over-explains obvious controls.

## Implementation Plan

### S12.1 App Routing and Shell

Add lightweight routing if not already present.

Suggested routes:

- `/` - landing
- `/app` - workspace overview
- `/app/network` - network map
- `/app/projects` - project list/detail
- `/app/cover` - cover for me
- `/app/status` - setup/preflight

Keep existing tab behavior working until routes are stable.

### S12.2 Landing Page

Build a professional landing page with:

- Hero.
- Three product surfaces.
- Trust / citation / privacy section.
- How it works.
- Demo CTA.

Use a real product screenshot or realistic product preview. Primary CTA opens `/app`. Secondary CTA opens `/app/status`.

### S12.3 Workspace Overview

Add overview dashboard:

- Mailbox loaded.
- Contacts count.
- Projects count.
- Embeddings count.
- Preflight status.
- Suggested questions.
- Recent projects.

### S12.4 Navigation Polish

Replace or supplement tabs with proper app navigation.

Requirements:

- Mailbox ID visible but not visually dominant.
- Clear active nav state.
- Status indicator for backend, retrieval, and synthesis health.
- Navigation remains usable on desktop and mobile widths.

### S12.5 Cover For Me Onboarding

Add:

- Suggested questions.
- Better empty-state guidance.
- Human-readable retrieval/synthesis status.
- Evidence drawer or popover.
- Grouped citations.

### S12.6 Project and Network Polish

Improve:

- Project display labels.
- Project empty states.
- Network search/filter affordances where cheap.
- Contact panel clarity.

Do not change clustering semantics unless separately scoped.

### S12.7 Verification

Run:

- Full backend test suite.
- Frontend build.
- Manual browser walkthrough.

Manual demo script:

1. Open landing page.
2. Open workspace.
3. Confirm Overview loads.
4. Confirm Network Map loads.
5. Confirm Projects load.
6. Ask a cited Cover for Me query.
7. Open a citation/evidence drawer.
8. Ask a no-evidence query.
9. Confirm no-evidence state is clear.
10. Confirm sensitive HR query does not expose sensitive content.

## Acceptance Criteria

- Landing page exists and routes cleanly into the workspace.
- Workspace has a clear overview screen.
- Existing Network, Projects, and Cover for Me surfaces remain functional.
- Cover for Me has inspectable evidence.
- Repeated citations are grouped or otherwise visually de-noised.
- Empty/error states distinguish backend unavailable, no embeddings, L2 disabled, no retrieval hits, and synthesis unavailable where data supports the distinction.
- Project labels are less machine-like in display, without changing clustering semantics.
- No sensitive content is exposed.
- No claim without citation.
- Frontend build passes.
- Full backend test suite passes.
- Docs updated with S12 status when complete.

## Recommended Priority

Do not start with the landing page alone. Start with the app shell and Overview, then build the landing page around the real product experience.

Recommended order:

1. App routes/navigation.
2. Overview screen.
3. Cover For Me suggested questions/status/evidence polish.
4. Landing page.
5. Project/network visual polish.

The strongest first product improvement is evidence UX. When a user gets an answer, they need to immediately feel: "I can see exactly where this came from."

## Manual demo script (as shipped)

No frontend test runner is configured in this repo, so S12 is verified by build +
this manual walkthrough. Start the stack on Windows with
`scripts/run_backend.ps1` and `scripts/run_frontend.ps1` (deterministic port
5173), then:

1. Open `http://localhost:5173/` — the landing page renders (hero, three
   surfaces, trust model, how-it-works, demo CTA, product preview mock).
2. Click **Open demo workspace** — routes to `/app`, the workspace **Overview**
   (default entry), showing People/Projects counts, embeddings/retrieval/
   synthesis readiness, suggested questions, and top projects.
3. Click **Network** in the nav (`/app/network`) — the graph loads; the role
   legend sits in a sub-bar; clicking a node opens the contact panel.
4. Click **Projects** (`/app/projects`) — list + detail; cleaned labels;
   low-confidence/uncategorized treatment; the filter box appears when there are
   more than five projects.
5. Click **Cover for Me** (`/app/cover`) — suggested-question chips show in the
   empty state.
6. Ask a cited question (e.g. a top project) — claims render with grouped
   citation chips; the retrieval-status note appears.
7. Click a citation chip — the evidence drawer opens with subject, date,
   message_id_header, snippet, and retrieval source.
8. Ask a no-evidence query — the polite "No email evidence found…" state shows
   (not an error), without overstating sensitivity filtering.
9. Ask a sensitive (HR/performance-review) query — no sensitive content appears;
   the response stays L1-only with no claims/evidence.
10. Change the mailbox ID and Load — the previous answer, chips, and any open
    evidence drawer clear (S11 trust-boundary behavior preserved).
11. Click **Status** (`/app/status`) or the header health dot — the full
    preflight check list renders with a Refresh button.

Note: steps 6–9 issue billed Voyage calls and require `VOYAGE_API_KEY` plus the
owner's authorization per CLAUDE.md; the shell, overview, network, projects, and
status screens do not.
