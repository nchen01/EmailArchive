# Product Roadmap: Quality-First (post-S41)

Status: roadmap direction note (docs-only). Captures the agreed post-S41 direction
so future sprint planning does not drift back toward "more graph signals" or broad
integrations too early. This is a planning guardrail, not an implementation plan.

## 1. Core product boundary (unchanged)

Everything below is bounded by the product invariants that already hold in code and
must keep holding:

- **Creator** can reason over their **own authenticated mailbox**. The covered
  employee is present, scopes the package, and reviews it before publishing.
- **Recipient** receives only a **frozen, reviewed, package-local artifact**. The
  recipient never gets live access to the creator's mailbox or any live graph; they
  read a snapshot with cited, in-package evidence.
- **Admin / governance stays metadata-only.** Admin and audit surfaces expose safe
  metadata (lifecycle, provider-connection metadata, jobs, audit trails, aggregate
  exclusion counts, readiness) and two audited actions. No admin route is a content
  backdoor.
- **No mailbox backdoor, and no surveillance / productivity scoring.** Evidence
  volume is communication volume, never importance or performance. We do not rank,
  score, or monitor employees, and managers do not browse employee mailboxes or live
  graphs.

## 2. Roadmap shift

The next phase prioritizes **proving that handoff packages are accurate, safe,
usable, governable, and pilot-ready before adding more intelligence or broad
integrations.** Depth of trust before breadth of signal. We do not add more graph
signals, more synthesis, or more connectors until the current artifact is
demonstrably correct and safe in a pilot.

## 3. Recommended sequence

1. **Docs / status cleanup first** (done: the status docs now reflect S34-S41 and
   the current Alembic head).
2. **Handoff quality evaluation harness** - a way to measure whether generated
   packages are accurate, well-cited, and free of leaked/excluded content.
3. **Privacy and safety review gates** - explicit checks that sensitivity/noise
   exclusion, no-citation-no-claim, and snapshot-only recipient access hold before a
   package can be published.
4. **Creator workflow / guided handoff wizard** - make scoping, reviewing, and
   pruning a package easy and hard to get wrong.
5. **Coverage contract per project** - a per-project statement of what a handoff
   covers (and what it deliberately does not), so recipients and managers can trust
   the boundary.
6. **Pilot metrics and demo QA** - the signals that tell us a real pilot is working,
   plus the deferred final demo QA.
7. **Calendar integration first** - the first likely external connector (see below).
8. **Then one structured work integration** - most likely Jira or Linear for
   engineering / product teams, or the best-fit system chosen from pilot evidence.
   One connector, chosen deliberately.
9. **Slack / Teams only after pilot evidence proves chat-only items are a major
   gap** - not before, and not by default.

## 4. Why calendar first

Calendar is the first likely integration **after** package quality, safety, and
pilot readiness, because it helps identify **meetings, deadlines, handoff windows,
and time-bound commitments** without immediately opening the privacy burden of chat
ingestion. It is high-signal for a handoff (what is due, when coverage starts/ends,
which commitments are time-bound) and comparatively low-risk on the privacy axis.

## 5. Integration principles

Any connector we add follows these rules:

- **One connector at a time.** No "connect every tool" push.
- **Read-only, least-privilege scopes.** Never write access; never more scope than
  the artifact needs.
- **Retrieve only artifacts relevant to author-selected projects / date windows.**
  No broad corpus harvesting; the creator's scope bounds what is fetched.
- **Creator reviews before publish.** Nothing from a connector reaches a recipient
  without the covered employee reviewing it.
- **Recipient still sees only package-local snapshots.** A connector expands what the
  creator can pull into a package, never what the recipient can browse live.
- **Source badges / links are allowed only as approved snapshot metadata**, never as
  a way for the recipient to live-browse the source system.

## 6. Not to build yet

Explicitly out of scope for this phase:

- Broad Slack / Teams ingestion.
- DMs / private channels by default.
- A generic "connect every tool" data harvester.
- Manager browsing of employee mailboxes or live graphs.
- Automatic publishing without author approval.
- Productivity scoring / employee monitoring.
- Broad SharePoint / Drive corpus embedding.

## 7. How to use this note

When planning a sprint, check it against Sections 2, 5, and 6 before adding a new
signal or connector. If a proposed sprint adds breadth (a new source, more graph
signal, broader ingestion) before the quality/safety/pilot items in Section 3 are
in place, treat that as roadmap drift and re-justify it against pilot evidence.
