"""High-signal Gmail smoke dataset — injects curated project threads for EKC testing.

Generates 59 messages across 18 threads (14 project + 4 noise) and imports
them directly into a throwaway Gmail mailbox without sending any real email.
A ground-truth JSON file is written to .local/smoke-dataset/ so the label
pass after ingest can be automated by fill_smoke_labels.py.

The dataset is designed so the EKC pipeline should produce near-perfect
eval scores: all project threads are non-noise, sensitivity keywords are
embedded correctly, and all four noise messages are classifiable by headers.

MAILBOX REQUIREMENT
-------------------
Use a FRESH, EMPTY throwaway Gmail account. The Gmail API returns messages
newest-first; if the account already has messages (e.g. 2000+), the smoke
messages (which will have the most-recent timestamps available) may still be
buried under other recent messages and missed by --max-messages. A fresh
account guarantees all 59 injected messages are the only ones to ingest.

BEFORE RUNNING
--------------
1. Create a fresh throwaway Gmail account (or verify the target is empty).

2. Obtain a gmail.insert token for that account:
     python scripts/gmail_oauth_write_token.py

3. Export the token — bash/zsh:
     export GMAIL_WRITE_TOKEN='{"token":...}'

   PowerShell:
     $env:GMAIL_WRITE_TOKEN = @'
     {"token":...}
     '@

USAGE
-----
  # Dry-run — print what will be injected, no API calls:
  python scripts/generate_smoke_dataset.py \\
      --owner-email you@gmail.com --company-domain acme.corp --dry-run --confirm

  # Full run:
  python scripts/generate_smoke_dataset.py \\
      --owner-email you@gmail.com --company-domain acme.corp --confirm

AFTER RUNNING
-------------
  # 1. Ingest the 59 smoke messages:
  python scripts/gmail_smoke_ingest.py \\
      --owner-email you@gmail.com \\
      --internal-domains acme.corp \\
      --max-messages 100 --confirm
  # Note: use --max-messages 100 (not 500) to ingest only the smoke messages
  # and avoid fetching unrelated mail if the account is not perfectly empty.

  # 2. Generate quality reports:
  python scripts/live_quality_report.py \\
      --mailbox-id <uuid> \\
      --out .local/reports/smoke-quality \\
      --sample-size 100 \\
      --include-sensitive

  # 3. Auto-fill gold labels from ground truth:
  python scripts/fill_smoke_labels.py \\
      --mailbox-id <uuid> \\
      --report-dir .local/reports/smoke-quality \\
      --ground-truth .local/smoke-dataset/ground_truth.json \\
      --out .local/labels/smoke-labels.csv

  # 4. Run eval (should pass all hard checks, soft targets near 1.0):
  python scripts/eval_live_quality.py \\
      --report-dir .local/reports/smoke-quality \\
      --labels .local/labels/smoke-labels.csv
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime as fmt_dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = ROOT / ".local"
SMOKE_DIR = LOCAL_DIR / "smoke-dataset"

MSG_ID_DOMAIN = "smoke.generated"


def _default_base_date() -> datetime:
    """Anchor for message timestamps: 90 days before now at 09:00 UTC.

    Using a recent anchor means the 59 injected messages appear near the top
    of Gmail's newest-first listing, so --max-messages 100 reliably fetches
    all of them even if the account is not perfectly empty.
    """
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=90)).replace(hour=9, minute=0, second=0, microsecond=0)

# ── persona model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Persona:
    name: str
    email: str


def _personas(owner_email: str, owner_name: str, d: str) -> dict[str, Persona]:
    return {
        "owner":     Persona(owner_name,          owner_email),
        "manager":   Persona("Sarah Chen",         f"sarah.chen@{d}"),
        "cto":       Persona("David Park",         f"david.park@{d}"),
        "eng1":      Persona("Alex Rivera",        f"alex.rivera@{d}"),
        "pm1":       Persona("Jordan Kim",         f"jordan.kim@{d}"),
        "eng2":      Persona("Taylor Brooks",      f"taylor.brooks@{d}"),
        "hr":        Persona("Morgan Lee",         f"morgan.lee@{d}"),
        "legal":     Persona("Casey Williams",     f"casey.williams@{d}"),
        "finance":   Persona("Riley Johnson",      f"riley.johnson@{d}"),
        "vendor_ae": Persona("Sam Torres",         "sam.torres@cloudbase.io"),
        "vendor2":   Persona("Drew Martinez",      "drew.martinez@vertex-partners.com"),
        "recruiter": Persona("Jamie Anderson",     f"jamie.anderson@{d}"),
        # External/noise senders — not real personas, used for noise messages only.
        "github":    Persona("GitHub",             "noreply@github.com"),
        "jira":      Persona("Atlassian Jira",     "noreply@atlassian.net"),
        "techcrunch":Persona("TechCrunch",         "hello@techcrunch.com"),
        "linkedin":  Persona("LinkedIn",           "notifications@linkedin.com"),
    }


# ── thread recipes ────────────────────────────────────────────────────────────
# Each thread is a list of message dicts. Fields:
#   from_, to, cc, subject, body, day, hour, minute
#   sensitivity: none/hr/legal/privileged/personal (gold-label value)
#   noise: bool
#   project_relevant: bool
#   extra_headers: optional dict of additional MIME headers

_LEGAL_FOOTER = (
    "\n\n--\nATTORNEY-CLIENT PRIVILEGED AND CONFIDENTIAL. This message is "
    "intended only for the named recipient(s) and may contain information "
    "that is attorney-client privileged and confidential. If you have "
    "received it in error, please notify the sender immediately and delete "
    "this message."
)

THREADS = [

    # ── 1. Nexus API design review ────────────────────────────────────────────
    {"key": "nexus-design", "messages": [
        {"from_": "eng1", "to": ["owner", "eng2", "pm1"], "cc": [],
         "subject": "Design review: Nexus API auth layer",
         "body": """Hi team,

I've wrapped up the first draft of the Nexus auth layer design. Looking for sign-off before we start the implementation sprint.

Key decisions:
1. Short-lived JWTs (15-min expiry) + refresh tokens via Redis-backed blocklist
2. Token rotation on every refresh (PKCE-style)
3. Failclosed policy when Redis is unreachable: return 503 to clients

Open questions:
- Jordan: what is the p99 latency budget for the token validation endpoint at 10k RPS? We have an enterprise SLA commitment to reference.
- Taylor: any infra concerns about the Redis dependency, given we already run Redis in the API gateway?

Full design doc is in Notion under Nexus/Architecture/Auth Layer v1.

Alex""",
         "day": 0, "hour": 9, "minute": 15,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["eng1", "eng2", "pm1"], "cc": [],
         "subject": "Re: Design review: Nexus API auth layer",
         "body": """Alex,

Strong design overall. Two concerns:

1. Sliding refresh window: if we keep resetting the expiry on every active token use, a stolen token never expires. Add an absolute session cap of 8 hours regardless of activity — add it as a session_max_exp claim to the JWT payload.

2. Failclosed + circuit breaker: agreed on 503, but we need a short timeout (< 2s) and a customer-facing "please try again" message. Support needs to know what to say when this fires.

Jordan: latency budget for validation endpoint needs to be < 5ms p99 at our current scale.
Taylor: +1 on reusing the existing Redis instance — just confirm the memory headroom.

Everything else looks good. Let's close by Friday.""",
         "day": 0, "hour": 11, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng2", "to": ["owner", "eng1", "pm1"], "cc": [],
         "subject": "Re: Design review: Nexus API auth layer",
         "body": """Taylor here.

Infra sign-off: we already run Redis for session management in the gateway, so no new dependency. The blocklist pattern works — just make sure the TTL is set to JWT expiry + 1 minute buffer, otherwise expired tokens pile up.

On the 8-hour cap: +1. I'll add session_max_exp to the JWT signing service.

Circuit breaker: using Resilience4j on the gateway already. I'll wire token validation into the existing config — 1.5s timeout, open at 50% failure rate over 10s window.

Redis memory check: we're at 58% of the 8GB limit. Headroom is fine for the blocklist volume we're projecting.

Ready to proceed once Jordan confirms the latency budget.""",
         "day": 1, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "pm1", "to": ["owner", "eng1", "eng2"], "cc": [],
         "subject": "Re: Design review: Nexus API auth layer",
         "body": """Jordan checking in.

Latency budget: < 5ms p99 at 10k RPS — that's what we committed in the enterprise SLA (contract ref: Nexus-ENT-004). At current Redis cluster load, p99 is 1–2ms, so we have margin.

On the 503 UX: I'll add "Session expired, please sign in again" to the copy deck. Consistent with existing expired-JWT messaging.

One addition: Compliance flagged that we need a written data retention policy for the blocklist entries before SOC 2 compliance covers this surface. Not a sprint blocker, but let's not go GA without it.

Design approved from product.

Jordan""",
         "day": 2, "hour": 14, "minute": 20,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng1", "to": ["owner", "eng2", "pm1"], "cc": [],
         "subject": "Re: Design review: Nexus API auth layer",
         "body": """Thanks everyone. Design is accepted. Summary of decisions:

- 8-hour absolute cap: added (session_max_exp claim)
- Redis TTL: token expiry + 60s buffer, documented in the runbook
- Circuit breaker: Resilience4j config, 1.5s timeout, Taylor to merge
- Latency target: < 5ms p99 tracked in the Nexus perf dashboard
- SOC 2 blocklist retention policy: Casey Williams is drafting — not blocking sprint start

Implementation sprint starts Monday. I'll create the Nexus sprint tickets today.

Alex""",
         "day": 5, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 2. Nexus launch readiness ─────────────────────────────────────────────
    {"key": "nexus-launch", "messages": [
        {"from_": "pm1", "to": ["owner", "manager", "eng1"], "cc": [],
         "subject": "Nexus launch readiness — go/no-go checklist",
         "body": """Hi,

T-10 days to Nexus GA. Kicking off launch readiness.

Current status:
- Auth layer: code complete, in QA
- Circuit breaker: merged, load-testing in progress
- SOC 2 blocklist retention policy: Casey has a draft, pending legal review
- Documentation: 70% done; SDK migration guide missing
- Support runbook: not started

Blockers I need resolved by EOW:
1. Load test results (auth + circuit breaker under 12k RPS)
2. SDK migration guide ETA
3. Go/no-go from Sarah on launch weekend support staffing

Jordan""",
         "day": 15, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["pm1", "manager", "eng1"], "cc": [],
         "subject": "Re: Nexus launch readiness — go/no-go checklist",
         "body": """Jordan,

Load test results: p99 token validation at 12k RPS came in at 3.8ms — under the 5ms SLA target. Circuit breaker tripped correctly at 14k RPS; recovery time 4 seconds.

SDK migration guide: I have a first draft. Will review with Alex today and target Thursday delivery.

One issue to flag: the 503 error case when Redis is down shows as a generic 500 in the current SDK client. This is a regression we need to fix before GA — it's a one-line change in the error mapping.

Provisional go from me pending the 503 fix and support runbook.""",
         "day": 15, "hour": 14, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["pm1", "owner", "eng1"], "cc": [],
         "subject": "Re: Nexus launch readiness — go/no-go checklist",
         "body": """On launch weekend staffing: confirmed 2 engineers on-call. PagerDuty schedule updated.

503 fix: agreed, this must be in before GA. Alex, can you own it? Should be fast.

SOC 2 retention policy: I spoke with Casey this morning. Draft back to us by Wednesday. No surprises expected.

My go/no-go is provisional yes pending: (1) 503 fix merged and tested, (2) Casey's retention policy signed off internally.

Sarah""",
         "day": 16, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng1", "to": ["pm1", "owner", "manager"], "cc": [],
         "subject": "Re: Nexus launch readiness — go/no-go checklist",
         "body": """503 fix merged and deployed to staging. The SDK now surfaces the Redis-down scenario as AuthServiceUnavailableError with a retry-after hint. Backwards compatible — old SDK versions still see a generic error, but those are past EOL.

Support runbook v1 is in Confluence under Nexus/Ops. Covers: Redis down, token validation latency alert, and account lockout scenarios.

SDK migration guide published to the developer portal. Link in the Nexus launch doc.

From my side: ready to go. Alex""",
         "day": 18, "hour": 9, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 3. Production API incident ────────────────────────────────────────────
    {"key": "incident-api", "messages": [
        {"from_": "eng2", "to": ["owner", "manager", "cto"], "cc": [],
         "subject": "INCIDENT P1: prod-api p99 latency spike — triaging",
         "body": """Started: 14:32 UTC

Symptom: prod-api p99 latency jumped from 12ms to 340ms. Error rate is 0.3%, up from baseline 0.01%. Nexus auth surface is unaffected. Downstream services seeing timeouts on /api/v2/data.

Initial hypothesis: database connection pool exhaustion. We are at 98/100 connections. Checking query logs now.

Severity: P1. TechCorp and Meridian are on this surface.

I am incident commander. Heads up to Sarah and David.

Taylor""",
         "day": 22, "hour": 14, "minute": 35,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["eng2", "manager", "cto"], "cc": [],
         "subject": "Re: INCIDENT P1: prod-api p99 latency spike — triaging",
         "body": """Found a candidate. A slow analytics query on the events table started at 14:28. It is doing a full scan — missing index on (tenant_id, created_at, event_type). Matches the connection pool timing.

Query came from the new dashboard feature Alex shipped this morning. The migration added a composite index with CREATE INDEX CONCURRENTLY, which may not have finished building before prod traffic hit.

Checking: SELECT idx_scan FROM pg_stat_user_indexes WHERE indexrelname = 'idx_events_tenant_ts_type';

Will report back in 5 minutes.""",
         "day": 22, "hour": 14, "minute": 50,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["eng2", "owner", "cto"], "cc": [],
         "subject": "Re: INCIDENT P1: prod-api p99 latency spike — triaging",
         "body": """Customer comms: I have flagged to CS that TechCorp and Meridian may see elevated latency. Holding on a public status page update for 10 minutes until we confirm root cause.

David: no action needed yet. Will loop you in on RCA.

What is the ETA on the index being usable?

Sarah""",
         "day": 22, "hour": 15, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "cto", "to": ["eng2", "owner", "manager"], "cc": [],
         "subject": "Re: INCIDENT P1: prod-api p99 latency spike — triaging",
         "body": """Standing by. If the index is not ready soon, can we kill the slow query and disable the dashboard endpoint temporarily to unblock the connection pool?

What is the rollback plan if the index build never completes?

David""",
         "day": 22, "hour": 15, "minute": 5,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng2", "to": ["owner", "manager", "cto"], "cc": [],
         "subject": "Re: INCIDENT P1: prod-api p99 latency spike — triaging",
         "body": """RESOLVED — 15:17 UTC. Duration: 45 minutes.

Root cause: CREATE INDEX CONCURRENTLY was still running when the dashboard went live. Index was unavailable, causing full scans that saturated the connection pool.

Fix: killed the in-flight index build, disabled the dashboard endpoint temporarily, rebuilt the index with fill_factor=80 (completes faster). Dashboard re-enabled at 15:12. P99 back to 11ms.

Post-incident actions:
1. Add a migration deployment gate that blocks release if CONCURRENT index build has not confirmed completion.
2. Load-test all new endpoints before prod release.

Status page: all green. TechCorp and Meridian notified — no SLA breach.

Taylor""",
         "day": 23, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 4. CloudBase vendor proposal ──────────────────────────────────────────
    {"key": "vendor-cloudbase", "messages": [
        {"from_": "vendor_ae", "to": ["owner"], "cc": [],
         "subject": "CloudBase Enterprise — managed pgvector for Acme Corp",
         "body": """Hi,

I am Sam Torres, Account Executive at CloudBase. I came across Acme Corp through the PostgreSQL community — you are evaluating embedding storage options, and managed pgvector at scale is exactly where we help engineering teams.

CloudBase Enterprise offers: managed pgvector clusters with automatic sharding, HNSW index optimization, and a 99.99% SLA. Typical customers at your scale see 40-60% reduction in query latency versus self-managed.

I would like to set up a 30-minute call to walk through a tailored proposal. Would Thursday or Friday this week work?

Best,
Sam Torres
Account Executive, CloudBase""",
         "day": 8, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["vendor_ae"], "cc": [],
         "subject": "Re: CloudBase Enterprise — managed pgvector for Acme Corp",
         "body": """Sam,

Thanks for reaching out. We are evaluating embedding storage as part of our L2/retrieval build, so the timing is relevant.

Before we set up a call:
1. Pricing model — per-cluster, per-query, or per-GB stored?
2. Cross-region replication — included in base tier or add-on?
3. GCP integration — native or bring-your-own-VPC deployment?

Friday 2pm PT works if you can answer these ahead of time.""",
         "day": 9, "hour": 11, "minute": 15,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "vendor_ae", "to": ["owner"], "cc": [],
         "subject": "Re: CloudBase Enterprise — managed pgvector for Acme Corp",
         "body": """Answers:

1. Per-cluster per month, based on vCPUs and storage. No per-query charges. For 50M vectors at 768-dim, roughly $2,800/month for a 3-node cluster with 2TB NVMe SSD.
2. Cross-region replication included in Enterprise tier. Active-passive by default, active-active is an add-on.
3. GCP: native integration via GCP Marketplace. Deploy into your own VPC using Private Service Connect. No egress surprises.

Friday 2pm PT confirmed. Calendar invite coming.

Sam""",
         "day": 10, "hour": 9, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["vendor_ae", "manager"], "cc": [],
         "subject": "Re: CloudBase Enterprise — managed pgvector for Acme Corp",
         "body": """Sam,

Good call yesterday. Copying in Sarah Chen, Engineering Manager, who should be part of any procurement conversation.

Summary for Sarah: CloudBase is proposing a managed pgvector cluster at ~$2.8k/month. Compelling on latency and ops burden; migration path from self-managed needs evaluation. I would like a 30-day POC with our actual 768-dim HNSW cosine workload before committing.

Sam — can you provision a POC environment? We would run our own benchmark suite. If results match the claims, we can move to procurement conversation.

CC Sarah on next steps.""",
         "day": 12, "hour": 14, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 5. Legal MSA with Vertex Partners [sensitivity: privileged] ──────────
    # Gold label is "privileged" because the keyword-based classifier detects
    # "attorney-client" → PRIVILEGED. The LEGAL tag requires cfg.legal_domains
    # to be configured with the sender's domain — not configured by default.
    # "termination" wording is intentionally avoided in §11 references to prevent
    # the "termination" HR keyword from firing on contract exit clauses.
    {"key": "legal-msa", "messages": [
        {"from_": "legal", "to": ["owner", "manager"], "cc": [],
         "subject": "Vertex Partners MSA — legal review [ATTORNEY-CLIENT PRIVILEGED]",
         "body": """Sarah, team,

Vertex Partners has sent over a draft Master Services Agreement for the data pipeline integration. I have reviewed it and flagged concerns below. All of this is attorney-client privileged and confidential.

Key issues:

1. Indemnification (§8.2): Vertex requests indemnification against third-party IP claims with no cap on our side. Our standard position is mutual indemnification, each side capped at fees paid in the preceding 12 months.

2. Data Processing Addendum (Exhibit C): missing GDPR-specific provisions. We handle EU customer data — this is a blocker for execution.

3. Liability cap (§9.1): they propose a 3-month fee cap. We require 12 months.

Recommendation: accept with redlines on §8.2 and §9.1, and require a revised Exhibit C before signature. I will prepare the redline document.

Casey""" + _LEGAL_FOOTER,
         "day": 30, "hour": 9, "minute": 0,
         "sensitivity": "privileged", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["legal", "manager"], "cc": [],
         "subject": "Re: Vertex Partners MSA — legal review [ATTORNEY-CLIENT PRIVILEGED]",
         "body": """Casey,

Thanks for the thorough review. My positions:

§8.2 indemnification: agreed, we cannot accept uncapped IP indemnification. Mutual + capped at 12 months of fees is our standard. Push back hard on this.

Exhibit C / GDPR: hard blocker from my side too. We have contractual obligations to EU customers requiring a compliant DPA with every sub-processor. Do not execute without it.

§9.1 liability cap: 12 months is our norm. 3 months is too low given the integration scope and data volumes involved.

One more item not in your list: §11 (exit and notice). The notice period for contract exit is 90 days. Our standard is 30 days. Add to the redline.

Happy to join a call with Vertex if negotiation stalls.""" + _LEGAL_FOOTER,
         "day": 31, "hour": 10, "minute": 30,
         "sensitivity": "privileged", "noise": False, "project_relevant": True},

        {"from_": "legal", "to": ["owner", "manager"], "cc": [],
         "subject": "Re: Vertex Partners MSA — legal review [ATTORNEY-CLIENT PRIVILEGED]",
         "body": """Redlines are drafted. Summary sent to Vertex:

- §8.2: mutual indemnification, capped at 12 months fees paid, with mutual IP carve-out for willful infringement
- §9.1: liability cap raised to 12 months both sides
- §11: contract exit notice reduced to 30 days
- Exhibit C: GDPR-compliant DPA using our standard template

All changes are marked attorney-client privileged. The counter-party legal team should respond within 5 business days.

If they push back on §8.2, our floor is: their obligation is uncapped for willful infringement only; our obligation is capped regardless.

Casey""" + _LEGAL_FOOTER,
         "day": 32, "hour": 14, "minute": 0,
         "sensitivity": "privileged", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["owner", "legal"], "cc": [],
         "subject": "Re: Vertex Partners MSA — legal review [ATTORNEY-CLIENT PRIVILEGED]",
         "body": """Casey — good work. Correct set of redlines.

Once Vertex responds, let's sync before any counter-offer. I do not want to move off the 12-month liability cap without explicit sign-off from me and David.

One addition before the redlines go out: we need a data deletion SLA. Vertex must purge our data within 30 days of contract expiry. Casey, please add as §12.4.

Sarah""" + _LEGAL_FOOTER,
         "day": 35, "hour": 9, "minute": 30,
         "sensitivity": "privileged", "noise": False, "project_relevant": True},
    ]},

    # ── 6. HR performance review cycle [sensitivity: hr] ─────────────────────
    {"key": "hr-perf", "messages": [
        {"from_": "hr", "to": ["owner"], "cc": [],
         "subject": "H1 performance review cycle — action required",
         "body": """Hi,

The H1 performance review cycle opens Monday. As a manager, you have 3 direct reports this cycle.

Actions required:
1. Complete your own self-assessment (due in 2 weeks)
2. Submit manager reviews for your direct reports (due in 3 weeks)
3. Participate in the calibration session (scheduled separately)

The review platform is Lattice. Review links will be in your inbox by Monday.

Note from leadership: this cycle we are paying particular attention to impact over output. Frame your assessments around measurable outcomes, not activity.

Questions? Let me know.

Morgan Lee, HR Business Partner""",
         "day": 40, "hour": 8, "minute": 30,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "owner", "to": ["hr"], "cc": [],
         "subject": "Re: H1 performance review cycle — action required",
         "body": """Morgan,

A few questions:

1. One direct report started 6 weeks ago. Do they participate in self-assessment this cycle, or are they exempt as a new hire?
2. Is the calibration session Sarah mentioned in the all-hands the same as the manager calibration, or is there a separate IC-level calibration?
3. The review template has a "potential" rating — is that new this cycle? I do not recall seeing it in H2.
4. Is there guidance on how to handle compensation discussions in the context of the performance review? I want to make sure I am framing expectations correctly before calibration.

Thanks.""",
         "day": 41, "hour": 10, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "hr", "to": ["owner", "manager"], "cc": [],
         "subject": "Re: H1 performance review cycle — action required",
         "body": """Answers:

1. New hires < 90 days are exempt from self-assessment but their manager still completes a brief check-in review (5 questions, not the full template). You will see a separate link in Lattice for that person.
2. The all-hands calibration Sarah mentioned is leadership-level. There is a separate manager-level calibration on the 14th at 2:30pm — invite coming from your calendar.
3. "Potential" rating is new this cycle, introduced in the Q4 talent framework update. Rubric attached.
4. On compensation discussions: performance reviews in Lattice are separate from compensation decisions. Compensation adjustments are communicated in a separate salary review cycle in Q3. Please do not reference specific compensation numbers in review notes — that creates documentation risk.

Copying Sarah so she is aware of the new-hire exception.

Morgan""",
         "day": 42, "hour": 14, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "manager", "to": ["owner", "hr"], "cc": [],
         "subject": "Re: H1 performance review cycle — action required",
         "body": """Thanks Morgan. Confirming the manager calibration on the 14th — I will send a prep agenda.

For your new hire's check-in review: focus on onboarding completeness, initial project contributions, and early trajectory signals. Keep it factual and development-oriented — this is not a formal performance rating.

A reminder for everyone: all review content in Lattice is confidential and must not be shared outside the calibration process without HR sign-off. No exports, screenshots, or verbal summaries to anyone outside the calibration session.

Sarah""",
         "day": 45, "hour": 9, "minute": 15,
         "sensitivity": "hr", "noise": False, "project_relevant": False},
    ]},

    # ── 7. Senior backend engineer hiring [sensitivity: hr] ───────────────────
    {"key": "hiring-backend", "messages": [
        {"from_": "recruiter", "to": ["owner", "manager"], "cc": [],
         "subject": "Senior Backend Engineer shortlist — Nexus team",
         "body": """Hi Sarah, team,

Completed the initial screen for the Senior Backend Engineer opening on the Nexus team. Shortlist of 3:

Candidate A: 8 years backend, 4 years distributed systems, Go + PostgreSQL. Currently at a Series B infrastructure company. Strong system design interview. Salary expectations: within our senior band.
Candidate B: 6 years, primarily Java with Go exposure. Strong reliability engineering background. Solid on the on-call scenario question. Salary expectations: mid-band.
Candidate C: 5 years, Go + Rust. Currently at FAANG. Strong technical signal but compensation expectations are at the top of our band — may require a band exception.

Recommend moving all 3 to technical screens. Let me know if you would like to adjust the slate.

Jamie""",
         "day": 50, "hour": 9, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "owner", "to": ["recruiter", "manager"], "cc": [],
         "subject": "Re: Senior Backend Engineer shortlist — Nexus team",
         "body": """Jamie,

Agreed on all 3 to technical screens.

Candidate A: this is the profile we need. Distributed systems + Go/PostgreSQL is a direct fit for Nexus. High priority.
Candidate B: Java background is not a concern — we have onboarded from Java to Go before. Reliability focus is a plus given the incident last week.
Candidate C: if they are exceptional we have some salary band flexibility, but we need to discuss that offband before setting any expectations.

I can run technical screens next week. Send the take-home prompt when set up.""",
         "day": 51, "hour": 11, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "recruiter", "to": ["owner", "manager"], "cc": [],
         "subject": "Re: Senior Backend Engineer shortlist — Nexus team",
         "body": """Technical screens scheduled:

- Candidate A: Tuesday 10am PT (system design + Go live coding)
- Candidate B: Wednesday 2pm PT (reliability case study + architecture)
- Candidate C: Thursday 11am PT (distributed consensus system design)

Take-home prompts sent to all 3. Due 48h before each screen.

Note on Candidate C: hold compensation details for the separate salary band conversation. Do not reference specific compensation numbers with the candidate until we have had that internal discussion.

Sarah — sending you the updated scoring rubric separately. It was revised last quarter to weight production-readiness and operability more heavily.

Panel composition: owner + one Nexus engineer. Proposed second panelist: Taylor Brooks (SRE perspective). Please confirm.

Jamie""",
         "day": 52, "hour": 9, "minute": 30,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "manager", "to": ["owner", "recruiter"], "cc": [],
         "subject": "Re: Senior Backend Engineer shortlist — Nexus team",
         "body": """Panel: confirmed, add Taylor Brooks as second panelist. SRE perspective is exactly right for this role.

For Candidate C's compensation: I will send you our internal salary band data separately — do not discuss specific numbers with the candidate until we have an internal conversation about whether to proceed with a band exception. That conversation needs me and David before any offer stage.

For all panelists: scoring sheets must be submitted to Jamie within 24h of each screen. No verbal feedback sharing between panelists before sheets are submitted — calibration issues happen when people compare notes first.

Sarah""",
         "day": 55, "hour": 8, "minute": 45,
         "sensitivity": "hr", "noise": False, "project_relevant": False},
    ]},

    # ── 8. Q3 engineering budget ──────────────────────────────────────────────
    {"key": "budget-q3", "messages": [
        {"from_": "finance", "to": ["owner", "manager", "cto"], "cc": [],
         "subject": "Q3 engineering budget review — headcount request",
         "body": """Hi,

Finance is kicking off the Q3 budget cycle. Headcount requests for engineering need to be finalized by end of this week.

Current approved headcount: 18 FTE. The Q3 template asks you to justify incremental hires with projected business impact (revenue, cost savings, or risk reduction).

Needed from you:
1. Final headcount request number
2. Prioritized list of open roles with urgency rating
3. Any planned contractor-to-FTE conversions

Template in the Finance shared drive: Q3 Eng HC Template v2.

Riley""",
         "day": 35, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["finance", "owner", "cto"], "cc": [],
         "subject": "Re: Q3 engineering budget review — headcount request",
         "body": """Riley,

Q3 headcount request: +3 FTE net new on top of 18 current.

Roles:
1. Senior Backend Engineer — Nexus team (urgent, already posted)
2. Staff Infrastructure Engineer — SRE/platform (high, active scaling constraint)
3. ML Engineer — retrieval and embedding (medium, Q4 delivery dependency)

Business justification:
- Backend eng: Nexus is our fastest-growing surface; team is at capacity.
- Infra engineer: the Q1 API incident exposed an SRE gap. This hire directly reduces P1 risk.
- ML engineer: the L2/vector retrieval build is blocked without ML expertise. This is a Q4 revenue dependency.

No contractor-to-FTE conversions planned this cycle.

Sarah""",
         "day": 36, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["manager", "finance"], "cc": [],
         "subject": "Re: Q3 engineering budget review — headcount request",
         "body": """Riley,

Adding context on the ML engineer request. Our current retrieval layer is rules-based (L1 only). L2 vector retrieval requires: embedding model selection, HNSW index tuning, and ongoing model quality evaluation — none of our current team has production ML experience at this depth.

The Q4 cover-for-me feature is directly gated on L2 retrieval quality. Without the ML hire, we will need to descope or delay, which affects the Q4 revenue commitment.

If the ML hire cannot be approved for Q3, L2 should be marked as at-risk for Q4 delivery. Happy to present this to David at the Q3 planning session.""",
         "day": 37, "hour": 14, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "cto", "to": ["manager", "owner", "finance"], "cc": [],
         "subject": "Re: Q3 engineering budget review — headcount request",
         "body": """Riley — approved on all 3 headcount requests. Please process.

Sarah — I want to move fast on the ML engineer. Can you have a job description drafted and the req open by Friday?

The L2 delivery risk is noted. I will flag it in the Q3 planning deck as a dependency to track. If the ML hire slips, we may need a vendor embedding service as a bridge — let's revisit after the first round of candidates.

David""",
         "day": 40, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 9. H2 roadmap planning ────────────────────────────────────────────────
    {"key": "roadmap-h2", "messages": [
        {"from_": "pm1", "to": ["owner", "manager", "cto", "eng1"], "cc": [],
         "subject": "H2 Roadmap v0.1 — draft for review",
         "body": """Hi team,

Attaching the H2 roadmap draft. Looking for feedback before we lock it for the board deck.

Themes:
1. L2/vector retrieval — unblock cover-for-me quality improvements
2. Project clustering — surface multi-project inboxes as a new use case
3. Platform reliability — reduce P1 risk; SRE hire dependency
4. Enterprise features — SSO, audit log API, data retention controls (SOC 2)

Key bets:
- Cover-for-me moves from L1-only to L2 hybrid in Q3. Flagship product improvement.
- Project clustering becomes demo-able in Q4.
- Enterprise features run Q3-Q4 in parallel.

Needed by Friday:
- David: priority call on cover-for-me vs. enterprise for Q3 bandwidth conflict
- Sarah: engineering capacity estimates per theme
- Team: risk flags on L2 technical assumptions
- Alex: project clustering complexity estimate

Jordan""",
         "day": 55, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "cto", "to": ["pm1", "owner", "manager", "eng1"], "cc": [],
         "subject": "Re: H2 Roadmap v0.1 — draft for review",
         "body": """Jordan,

Priority call: cover-for-me L2 is higher priority for Q3 than enterprise features. Enterprise customers are directly asking for it. SSO is table stakes above $100k ACV but we have more timing flexibility — I will accept a Q4 slip on SSO if it frees L2 bandwidth.

One item missing: M365 provider support. We have inbound from 3 enterprise prospects on M365 only. Currently a stub/NotImplementedError. We need to schedule it. Add it as a Q4 consideration.

David""",
         "day": 56, "hour": 11, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["pm1", "manager", "cto", "eng1"], "cc": [],
         "subject": "Re: H2 Roadmap v0.1 — draft for review",
         "body": """Risk flags on L2 technical assumptions:

1. Embedding model not chosen. The roadmap assumes a production embedding model is available at Q3 start. This decision has not been made. Without it, the L2 sprint cannot begin. This is the critical path item — it must be D11 at the next architecture review.

2. HNSW index dimension: we need to commit to an embedding dimension before the DB migration (migration 0006). Changing dimensions post-migration requires a full rebuild. This blocks all downstream features.

3. Cover-for-me L2 estimate: 4-6 weeks assuming the embedding model is available day 1. Add 2 weeks if we are still evaluating models in week 1.

Recommendation: lock the embedding model decision before the sprint starts. Do not begin without it.""",
         "day": 57, "hour": 14, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["pm1", "owner", "cto", "eng1"], "cc": [],
         "subject": "Re: H2 Roadmap v0.1 — draft for review",
         "body": """Engineering capacity (rough, based on current team + planned hires):

Q3:
- L2 retrieval: 2 engineers (team + ML hire when onboarded), 6-8 weeks
- Platform reliability: 1 engineer (infra hire), ongoing
- Enterprise features: 1 engineer (shared with Nexus), starting mid-Q3

Q4:
- Project clustering: 2 engineers, 6-8 weeks (L2 dependency)
- M365 provider: 1 engineer, 4-6 weeks (noted David — added to the plan)

Risk: if the ML hire does not close by end of Q3, L2 is at risk for Q4 delivery, not just Q3.

Sarah""",
         "day": 58, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng1", "to": ["pm1", "owner", "manager", "cto"], "cc": [],
         "subject": "Re: H2 Roadmap v0.1 — draft for review",
         "body": """Clustering complexity estimate:

What is already built: blocking/similarity/labeling pipeline (S3 work). What is not built: production embedding integration, incremental cluster updates as new mail arrives, and the project view UI surface.

Rough estimate:
- Embedding integration: 2 weeks (once model is chosen)
- Incremental updates: 3 weeks
- Project view UI: 2 weeks frontend

Total: 6-7 weeks full-time. Critical dependency: embedding model must be chosen and available from day 1.

On M365: 6+ weeks of work. Conflicts with clustering if the same engineers own both. Needs a 4th engineer or we choose one over the other.

Alex""",
         "day": 62, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 10. External security audit ───────────────────────────────────────────
    {"key": "security-audit", "messages": [
        {"from_": "legal", "to": ["owner", "manager", "cto"], "cc": [],
         "subject": "External security audit findings — action items [CONFIDENTIAL]",
         "body": """David, Sarah, team,

We received the external security audit report from Arcadia Security. Treat this as confidential until we decide on disclosure.

Critical findings (2):
1. JWT signing key rotation: no automated rotation in place. Current key has been in use for 11 months. Recommendation: automated rotation with 90-day cycle.
2. Rate limiting on /api/v2/auth/token: absent. An attacker can enumerate refresh tokens at high volume. Recommendation: per-IP rate limiting (100 req/min) with exponential backoff.

High findings (3):
- Missing CSP headers on dashboard endpoints
- Unencrypted Redis connection in staging (not prod)
- Dependency with known CVE (libxml2 v2.9.10) in the PDF export service

We need to respond to Arcadia within 30 days with remediation status. Casey""",
         "day": 65, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["legal", "cto"], "cc": [],
         "subject": "Re: External security audit findings — action items [CONFIDENTIAL]",
         "body": """Casey,

Taking ownership of the critical items:

1. JWT key rotation: implementing automated rotation using AWS Secrets Manager. 90-day cycle, 30-minute overlap window to handle in-flight tokens during rotation. ETA: 2 weeks.
2. Rate limiting on /api/v2/auth/token: adding at the API gateway layer. We already have rate limiting middleware on other endpoints. ETA: 3 days.

For the high severity items, I will draft a remediation plan with owners and ETAs by Friday.

On libxml2 CVE (CVE-2022-29824): is there a vulnerability disclosure obligation to enterprise customers? If their DPAs require notification of vulnerabilities that could affect their data, we need to check TechCorp and Meridian specifically.""",
         "day": 66, "hour": 10, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "cto", "to": ["owner", "legal"], "cc": [],
         "subject": "Re: External security audit findings — action items [CONFIDENTIAL]",
         "body": """Good moves. Additional notes:

On libxml2: Casey, please check TechCorp and Meridian DPAs. If notification is required, I would rather we proactively inform them before the 30-day Arcadia deadline. Sets a better tone.

JWT rotation and rate limiting are clearly P0. I want both done before the next customer-facing demo.

On staging Redis: fix this week even if not prod-affecting. Standards matter across all environments.

David""",
         "day": 67, "hour": 14, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["legal", "cto", "manager"], "cc": [],
         "subject": "Re: External security audit findings — action items [CONFIDENTIAL]",
         "body": """Remediation plan:

| Item | Owner | ETA |
|---|---|---|
| JWT key rotation | owner | +14 days |
| Auth endpoint rate limiting | owner | +3 days |
| CSP headers | Alex Rivera | +5 days |
| Redis TLS in staging | Taylor Brooks | +2 days |
| libxml2 CVE | Alex Rivera | +7 days |

On DPA notification: Casey, agreed — check TechCorp and Meridian. If notification is required, proactive disclosure before the Arcadia deadline is the right call.

Full remediation plan sent to Arcadia by Friday.""",
         "day": 70, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 11. Customer escalation — TechCorp ────────────────────────────────────
    {"key": "customer-esc", "messages": [
        {"from_": "vendor_ae", "to": ["owner", "manager"], "cc": [],
         "subject": "URGENT: TechCorp — data export returning partial results",
         "body": """Hi,

Urgent escalation. TechCorp (largest account) reports that /api/v2/export/contacts returns only 500 contacts even though they have 2,400. Their data team noticed during their weekly sync.

This is blocking their Monday reporting run. Their VP of Ops has already escalated to their account team.

Issue started after the Nexus launch last week. Could be related to the pagination changes in that release.

I am on a call with TechCorp in 2 hours. Need a status update to share with them.

Sam""",
         "day": 72, "hour": 13, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["vendor_ae", "manager"], "cc": [],
         "subject": "Re: URGENT: TechCorp — data export returning partial results",
         "body": """Found it. The Nexus launch changed the default page size for the contacts export endpoint from 500 to 100. TechCorp's integration hardcodes page=1 with no pagination loop, so they only get the first page.

This is a breaking change we made without a deprecation notice. That is on us.

Immediate fix: reverting default page size to 500 in a hotfix. ETA 20 minutes to deploy. This restores TechCorp's behavior without requiring changes on their side.

Longer term: this endpoint needs to be added to our API versioning plan so we do not break integrations silently.

Sam — update for your call: fix deploying in 20 minutes, full functionality restored within the hour.""",
         "day": 72, "hour": 13, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng2", "to": ["owner", "manager", "vendor_ae"], "cc": [],
         "subject": "Re: URGENT: TechCorp — data export returning partial results",
         "body": """Hotfix deployed at 14:12 UTC. TechCorp endpoint confirmed returning all 2,400 contacts.

Post-incident actions flagged:
1. The page size change was not in the changelog for the Nexus release. We need a process for documenting breaking API changes.
2. Add an integration test covering TechCorp's usage pattern: single-page fetch, large result set.
3. API v3 versioning: second silent integration break in 2 months. Should prioritize in Q3.

Adding these to the incident tracker.

Taylor""",
         "day": 73, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 12. On-call handoff ───────────────────────────────────────────────────
    {"key": "oncall-handoff", "messages": [
        {"from_": "eng1", "to": ["owner", "eng2", "manager"], "cc": [],
         "subject": "On-call handoff — week of Mar 29",
         "body": """Team,

Handing off on-call to Taylor this Friday. Notes from my week:

Notable alerts:
- Monday: prod-db-1 disk hit 82%. Cleaned up WAL segments, back to 61%. Monitoring.
- Wednesday: the API latency incident (see that thread). Resolved.
- Thursday: false-positive on Nexus auth memory. Alert fires at 70%; p99 usage is consistently 72%. Recommend raising threshold to 80%.

Open items for Taylor:
1. prod-db-1 disk: watch weekly trend. Page Sarah if we cross 85%.
2. Nexus auth memory alert: NEX-449 filed, not yet fixed.
3. Redis memory: 58% of 8GB limit, growing ~2%/week. Capacity concern in ~10 weeks.

New runbook: Redis blocklist TTL — see Nexus/Ops/Redis Blocklist in Confluence.

Alex""",
         "day": 78, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["eng1", "eng2"], "cc": [],
         "subject": "Re: On-call handoff — week of Mar 29",
         "body": """Alex,

Redis memory growth: is the 2%/week trend linear or accelerating? If TTLs are set correctly, growth should level off as old entries expire. If it is linear and not leveling off, we may have a TTL misconfiguration from the Nexus auth deployment. Can you check the TTL histogram before handing off?

Nexus auth alert: merging NEX-449 today. 70% was too tight.

On the disk cleanup: was that a one-time cleanup or did you find a root cause? If WAL segments are accumulating faster than expected, we should check the archival job.""",
         "day": 78, "hour": 11, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "eng2", "to": ["owner", "eng1"], "cc": [],
         "subject": "Re: On-call handoff — week of Mar 29",
         "body": """Taylor accepting handoff.

Redis TTL check: TTLs are set correctly (expiry + 60s buffer as designed). Growth is from new refresh tokens issued as Nexus user count grows — expected behavior, not a bug. 10-week runway is enough time to plan a capacity upgrade. Adding a Redis capacity ticket to the Q3 backlog.

WAL segments: one-time cleanup. The archival job is healthy. Accumulation was from a temp table not getting vacuumed. Added a manual vacuum to the weekly maintenance schedule.

Ready for the week. Taylor""",
         "day": 79, "hour": 9, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 13. 1:1 with manager [sensitivity: hr — compensation keyword] ─────────
    {"key": "oneone-manager", "messages": [
        {"from_": "manager", "to": ["owner"], "cc": [],
         "subject": "1:1 notes — career check-in",
         "body": """Hi,

Notes from our 1:1. Happy to discuss further anytime.

Work:
- Nexus auth design: strong work getting consensus quickly. Good judgment on the JWT decisions.
- CloudBase vendor conversation: good instinct to loop me in before going further on procurement.
- Q3 priorities: the ML engineer hire is an opportunity for you to manage a junior ML engineer. It is a stretch that would prepare you for a staff-level role.

Career:
- You mentioned targeting staff engineer in the next 12-18 months. Let's build toward that intentionally.
- Next concrete step: take the L2 retrieval technical lead role. It is cross-functional, involves architecture decisions, and is visible to David.
- Note on compensation: staff-level roles come with a meaningful compensation step-up. I want you to understand what the trajectory looks like. We can discuss the specifics of our salary bands in a follow-up once you are ready.
- Feedback: you tend to under-communicate when blocked. I have noticed this twice this month. Flag early — it is always better.

Sarah""",
         "day": 10, "hour": 17, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},

        {"from_": "owner", "to": ["manager"], "cc": [],
         "subject": "Re: 1:1 notes — career check-in",
         "body": """Sarah,

Thanks for writing this up.

On managing the ML engineer: yes, I would like to take that on. Can we spend 15 minutes in the next 1:1 on what hiring manager responsibilities actually look like? I have only been an interviewer, not a hiring manager.

On the under-communication feedback: fair. The two times you are thinking of, I was confident I had it handled quickly. I will send an "I am on it, ETA X" message even when I expect to resolve it fast.

On the staff engineer timeline and compensation discussion: yes, let's schedule that. I would like to understand the salary band structure and what the performance expectations are at staff before the H2 review cycle.

On L2 tech lead: makes sense as a target. Let's define what success looks like at the start of the sprint so we are aligned.""",
         "day": 11, "hour": 9, "minute": 0,
         "sensitivity": "hr", "noise": False, "project_relevant": False},
    ]},

    # ── 14. Board deck Q2 ─────────────────────────────────────────────────────
    {"key": "board-prep", "messages": [
        {"from_": "cto", "to": ["owner", "manager", "finance"], "cc": [],
         "subject": "Board deck Q2 — engineering metrics input needed by Thursday",
         "body": """Team,

Board deck due Friday. I need the engineering metrics section by Thursday EOD.

What I need:
1. Reliability: p99 latency trend Q1 vs Q2 (API gateway + Nexus auth). Incident count and MTTR. Uptime vs SLA target.
2. Velocity: sprint velocity trend, major features shipped Q2.
3. Headcount: current vs approved. Q3 hiring pipeline status.
4. Risk register: top 3 items for the board to be aware of.

Format: bullets with numbers where possible. One slide worth of content.

Board will ask about the API incident. Have a one-liner ready.

David""",
         "day": 80, "hour": 9, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "owner", "to": ["cto", "manager"], "cc": [],
         "subject": "Re: Board deck Q2 — engineering metrics input needed by Thursday",
         "body": """David,

Reliability:
- API gateway p99: Q1 avg 18ms → Q2 avg 14ms (22% improvement; connection pool tuning + CDN cache hit rate).
- Nexus auth p99: N/A in Q1 (launched Q2). Q2 avg 3.8ms, under 5ms SLA.
- Incidents Q2: 2 P1s. MTTR: 45 min (API latency) and 20 min (TechCorp export). Both resolved same day. No SLA breach.
- Uptime: 99.94% Q2 vs 99.9% target. Outperformed.

Board one-liner for the API incident: "A missing database index during a feature deployment caused a 45-minute latency event affecting 2 customers. No data loss, no SLA breach. Root cause fixed; deployment gate added to prevent recurrence."

Ready to review when you have a draft.""",
         "day": 81, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "finance", "to": ["cto", "owner", "manager"], "cc": [],
         "subject": "Re: Board deck Q2 — engineering metrics input needed by Thursday",
         "body": """David,

Headcount and cost:
- Q2 actual: 17 FTE vs 18 approved (1 role open — Senior Backend Eng, in interviews). Burn: $2.1M vs $2.3M budget (9% under due to open role).
- Q3 plan: 3 additional FTE approved (+17% headcount growth). ML Engineer is the highest-cost hire; total Q3 eng budget will be $2.8–3.0M depending on comp outcomes.
- CloudBase vendor evaluation: if we proceed, ~$2.8k/month ($33.6k annualized). Within discretionary budget without a new approval.

Full cost slide ready Thursday morning. Riley""",
         "day": 82, "hour": 9, "minute": 30,
         "sensitivity": "none", "noise": False, "project_relevant": True},

        {"from_": "manager", "to": ["cto", "owner", "finance"], "cc": [],
         "subject": "Re: Board deck Q2 — engineering metrics input needed by Thursday",
         "body": """David,

Velocity + risk:

Q2 shipped: Nexus GA (on schedule), cover-for-me v1 (1 week delayed, soft-launched Q2), project clustering beta (internal only).
Sprint velocity: stable at ~28 story points per sprint. No significant variance.

Risk register top 3:
1. L2/vector retrieval gated on ML hire. If hire slips to Q4, cover-for-me improvements are at risk for H2.
2. M365 provider not built. 3 enterprise prospects are M365-only. Competitive gap if not scheduled.
3. JWT key rotation not yet automated (critical security audit finding). ETA 2 weeks; tracking closely.

Technical risk slide added to the deck. Sarah""",
         "day": 83, "hour": 10, "minute": 0,
         "sensitivity": "none", "noise": False, "project_relevant": True},
    ]},

    # ── 15. Noise: GitHub PR notification ─────────────────────────────────────
    {"key": "noise-github", "messages": [
        {"from_": "github", "to": ["owner"], "cc": [],
         "subject": "[acme-corp/nexus-api] PR #142: Add HNSW index migration",
         "body": """Alex Rivera opened pull request #142 against main.

Changes: +342 -12 lines | Files changed: 3

  migrations/0005_add_hnsw_index.sql
  services/db/store.py
  tests/test_db_migrations.py

Review the pull request to give feedback.

---
You are receiving this email because you are subscribed to pull request reviews on acme-corp/nexus-api.
Manage notification settings · Unsubscribe""",
         "day": 3, "hour": 11, "minute": 0,
         "extra_headers": {},
         "sensitivity": "none", "noise": True, "project_relevant": False},
    ]},

    # ── 16. Noise: Jira ticket update ─────────────────────────────────────────
    {"key": "noise-jira", "messages": [
        {"from_": "jira", "to": ["owner"], "cc": [],
         "subject": "[JIRA] NEX-449 - JWT alert threshold — status updated",
         "body": """Taylor Brooks updated NEX-449.

  NEX-449: Raise Nexus auth memory alert threshold to 80%
  Project: Nexus API
  Assignee: Alex Rivera
  Priority: Low
  Status: In Progress → In Review
  Sprint: Sprint 14

This is an automated email from Atlassian Jira.
Do not reply to this email. Visit the issue in Jira.

---
You are receiving this notification because you are watching NEX-449.
Manage notifications · Unsubscribe""",
         "day": 7, "hour": 14, "minute": 0,
         "extra_headers": {},
         "sensitivity": "none", "noise": True, "project_relevant": False},
    ]},

    # ── 17. Noise: newsletter ─────────────────────────────────────────────────
    {"key": "noise-newsletter", "messages": [
        {"from_": "techcrunch", "to": ["owner"], "cc": [],
         "subject": "TechCrunch Daily: Top stories for you",
         "body": """TechCrunch Daily Digest

Today's top stories:

• OpenAI raises $6.6B at $157B valuation
• Apple unveils new MacBook Pro lineup at WWDC
• Stripe acquires Lemon Squeezy in fintech consolidation
• Amazon AWS launches new EU data sovereignty offering
• Y Combinator W26 batch: 10 startups to watch

Read the full stories at techcrunch.com

---
You received this email because you subscribed to TechCrunch Daily.
Unsubscribe | Privacy Policy | TechCrunch, 610 Crescent Executive Ct, Lake Mary, FL 32746""",
         "day": 12, "hour": 7, "minute": 0,
         "extra_headers": {"List-Unsubscribe": "<mailto:unsubscribe@techcrunch.com>",
                           "Precedence": "bulk"},
         "sensitivity": "none", "noise": True, "project_relevant": False},
    ]},

    # ── 18. Noise: LinkedIn notification ─────────────────────────────────────
    {"key": "noise-linkedin", "messages": [
        {"from_": "linkedin", "to": ["owner"], "cc": [],
         "subject": "Sam Torres and 2 others viewed your profile",
         "body": """Your profile was recently visited by:

  Sam Torres, Account Executive at CloudBase
  2 other professionals in your extended network

See who viewed your profile and connect with them.
View profile analytics →

---
You are receiving this email because you have a LinkedIn account.
Unsubscribe from these emails | LinkedIn, 1000 W Maude Avenue, Sunnyvale, CA 94085""",
         "day": 20, "hour": 8, "minute": 0,
         "extra_headers": {"List-Unsubscribe": "<mailto:unsubscribe@linkedin.com>"},
         "sensitivity": "none", "noise": True, "project_relevant": False},
    ]},
]


# ── message building ──────────────────────────────────────────────────────────

def _msg_id(thread_key: str, idx: int) -> str:
    return f"smoke-{thread_key}-{idx}@{MSG_ID_DOMAIN}"


def _addr(persona: Persona) -> str:
    return f"{persona.name} <{persona.email}>"


def _date(base: datetime, day: int, hour: int, minute: int) -> datetime:
    return base + timedelta(days=day, hours=hour, minutes=minute)


def build_messages(personas: dict[str, Persona], base: datetime) -> list[dict]:
    """Resolve thread recipes into fully-specified message dicts with threading headers."""
    all_msgs: list[dict] = []

    for thread in THREADS:
        key = thread["key"]
        refs: list[str] = []

        for i, spec in enumerate(thread["messages"]):
            msg_id_val = _msg_id(key, i)
            in_reply_to = refs[-1] if refs else None
            references = list(refs)

            all_msgs.append({
                "thread_key":     key,
                "msg_index":      i,
                "message_id":     msg_id_val,
                "in_reply_to":    in_reply_to,
                "references":     references,
                "from_key":       spec["from_"],
                "to_keys":        spec["to"],
                "cc_keys":        spec.get("cc", []),
                "from_persona":   personas[spec["from_"]],
                "to_personas":    [personas[k] for k in spec["to"]],
                "cc_personas":    [personas[k] for k in spec.get("cc", [])],
                "subject":        spec["subject"],
                "body":           spec["body"],
                "date":           _date(base, spec["day"], spec["hour"], spec["minute"]),
                "extra_headers":  spec.get("extra_headers", {}),
                "sensitivity":    spec["sensitivity"],
                "noise":          spec["noise"],
                "project_relevant": spec["project_relevant"],
            })
            refs.append(msg_id_val)

    # Sort chronologically for import order.
    all_msgs.sort(key=lambda m: m["date"])
    return all_msgs


def build_mime(msg_dict: dict) -> bytes:
    """Build an RFC 2822 email message as raw bytes."""
    em = EmailMessage()
    em["From"]    = _addr(msg_dict["from_persona"])
    em["To"]      = ", ".join(_addr(p) for p in msg_dict["to_personas"])
    if msg_dict["cc_personas"]:
        em["Cc"] = ", ".join(_addr(p) for p in msg_dict["cc_personas"])
    em["Subject"] = msg_dict["subject"]
    em["Date"]    = fmt_dt(msg_dict["date"])
    em["Message-ID"] = f"<{msg_dict['message_id']}>"
    if msg_dict["in_reply_to"]:
        em["In-Reply-To"] = f"<{msg_dict['in_reply_to']}>"
    if msg_dict["references"]:
        em["References"] = " ".join(f"<{r}>" for r in msg_dict["references"])
    for hdr, val in msg_dict["extra_headers"].items():
        em[hdr] = val
    em.set_content(msg_dict["body"])
    return em.as_bytes()


# ── Gmail API import ──────────────────────────────────────────────────────────

def _get_credentials(token_env: str = "GMAIL_WRITE_TOKEN"):
    raw = os.environ.get(token_env)
    if not raw:
        sys.exit(
            f"ERROR: Set {token_env} to the OAuth token JSON.\n"
            "Run: python scripts/gmail_oauth_write_token.py\n"
            "Then: export GMAIL_WRITE_TOKEN='<paste>'"
        )
    try:
        td = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: {token_env} is not valid JSON: {exc}")

    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        sys.exit("ERROR: google-auth is not installed. Run: pip install -e .[gmail]")

    required = {"token", "refresh_token", "token_uri", "client_id", "client_secret"}
    missing = required - td.keys()
    if missing:
        sys.exit(f"ERROR: token JSON missing fields: {missing}")

    _INSERT_SCOPE = "https://www.googleapis.com/auth/gmail.insert"
    _MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
    _FULL_SCOPE   = "https://mail.google.com/"
    scopes = td.get("scopes") or []
    # Always check — an absent or empty scopes field is treated as a missing
    # write scope, not as "unknown scopes, proceed anyway".  A token obtained
    # via gmail_oauth_token.py (gmail.readonly) will fail here rather than
    # producing a confusing 403 mid-import.
    has_write = any(s in scopes for s in (_INSERT_SCOPE, _MODIFY_SCOPE, _FULL_SCOPE))
    if not has_write:
        sys.exit(
            "ERROR: the token does not have a write scope.\n"
            f"  Token scopes : {scopes or '(none — field absent or empty)'}\n"
            f"  Required     : {_INSERT_SCOPE}\n"
            "Run: python scripts/gmail_oauth_write_token.py\n"
            "to obtain a token with gmail.insert scope.\n"
            "Do NOT use a token from gmail_oauth_token.py — that is gmail.readonly only."
        )

    return Credentials(
        token=td["token"],
        refresh_token=td["refresh_token"],
        token_uri=td["token_uri"],
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=scopes,
    )


def import_messages(messages: list[dict]) -> list[str]:
    """Import all messages into Gmail. Returns list of inserted Gmail message IDs."""
    creds = _get_credentials()
    try:
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("ERROR: google-api-python-client not installed. Run: pip install -e .[gmail]")

    service = build("gmail", "v1", credentials=creds)
    inserted_ids: list[str] = []

    for i, msg in enumerate(messages, start=1):
        mime_bytes = build_mime(msg)
        encoded    = base64.urlsafe_b64encode(mime_bytes).decode()
        result = service.users().messages().import_(
            userId="me",
            body={"raw": encoded},
            neverMarkSpam=True,
            internalDateSource="dateTimeHeader",
        ).execute()
        inserted_ids.append(result.get("id", ""))
        print(f"  [{i:2d}/{len(messages)}] imported {msg['thread_key']}-{msg['msg_index']} "
              f"({msg['date'].strftime('%Y-%m-%d')})")

    return inserted_ids


# ── ground truth ──────────────────────────────────────────────────────────────

def write_ground_truth(
    messages: list[dict],
    owner_email: str,
    company_domain: str,
    out_dir: Path,
) -> Path:
    """Write .local/smoke-dataset/ground_truth.json.

    Keyed by raw Message-ID value (without angle brackets) so fill_smoke_labels.py
    can look up by the message_id_header value stored in the DB.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    gt: dict[str, dict] = {}
    for msg in messages:
        gt[msg["message_id"]] = {
            "thread_key":       msg["thread_key"],
            "msg_index":        msg["msg_index"],
            "subject":          msg["subject"],
            "from_email":       msg["from_persona"].email,
            "date_day":         msg["date"].strftime("%Y-%m-%d"),
            "noise":            msg["noise"],
            "sensitivity":      msg["sensitivity"],
            "project_relevant": msg["project_relevant"],
        }

    payload = {
        "owner_email":    owner_email,
        "company_domain": company_domain,
        "msg_id_domain":  MSG_ID_DOMAIN,
        "total_messages": len(messages),
        "messages":       gt,
    }
    path = out_dir / "ground_truth.json"
    with path.open("w", newline="\n", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True, indent=2))
        f.write("\n")
    return path


def write_manifest(messages: list[dict], out_dir: Path) -> Path:
    """Write a quick-reference CSV manifest of all injected messages."""
    import csv
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "manifest.csv"
    fields = ["message_id", "thread_key", "msg_index", "date", "from_email",
              "subject_preview", "noise", "sensitivity", "project_relevant"]
    with path.open("w", newline="\n", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for msg in messages:
            w.writerow({
                "message_id":      msg["message_id"],
                "thread_key":      msg["thread_key"],
                "msg_index":       msg["msg_index"],
                "date":            msg["date"].strftime("%Y-%m-%d %H:%M"),
                "from_email":      msg["from_persona"].email,
                "subject_preview": msg["subject"][:60],
                "noise":           msg["noise"],
                "sensitivity":     msg["sensitivity"],
                "project_relevant": msg["project_relevant"],
            })
    return path


# ── main ──────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Inject curated project email threads into a throwaway Gmail mailbox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--owner-email", required=True,
                   help="Email address of the throwaway Gmail account.")
    p.add_argument("--owner-name", default=None,
                   help="Display name for the owner. Defaults to the local part of --owner-email.")
    p.add_argument("--company-domain", default="acme.corp",
                   help="Internal company email domain (default: acme.corp). "
                        "Pass the same value as --internal-domains to gmail_smoke_ingest.py.")
    p.add_argument("--out", default=str(SMOKE_DIR),
                   help=f"Output dir for ground_truth.json and manifest.csv "
                        f"(default: {SMOKE_DIR}).")
    p.add_argument("--base-date", default=None, metavar="YYYY-MM-DD",
                   help="Anchor date for message timestamps (default: 90 days before today). "
                        "Messages span from this date over the next ~90 days. "
                        "Using a recent anchor ensures injected messages appear near the "
                        "top of Gmail's newest-first listing and are captured by ingest.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print all messages and write ground truth, but do not call Gmail API.")
    p.add_argument("--confirm", action="store_true",
                   help="Required. Acknowledges you are injecting test content into a "
                        "throwaway account you own.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not args.confirm:
        sys.exit(
            "ERROR: --confirm is required.\n"
            "By passing --confirm you acknowledge that you are injecting synthetic test "
            "content into a throwaway Gmail account you own, and that no real personal "
            "or third-party data will be affected."
        )

    owner_name = args.owner_name or args.owner_email.split("@")[0]
    out_dir    = Path(args.out)

    if not str(out_dir.resolve()).startswith(str(LOCAL_DIR.resolve())):
        sys.exit(f"ERROR: --out must be under {LOCAL_DIR}. Got: {out_dir.resolve()}")

    if args.base_date:
        try:
            base = datetime.strptime(args.base_date, "%Y-%m-%d").replace(
                hour=9, minute=0, second=0, tzinfo=timezone.utc
            )
        except ValueError:
            sys.exit(f"ERROR: --base-date must be YYYY-MM-DD format. Got: {args.base_date!r}")
    else:
        base = _default_base_date()

    personas = _personas(args.owner_email, owner_name, args.company_domain)
    messages = build_messages(personas, base)

    # Summary
    noise_count  = sum(1 for m in messages if m["noise"])
    sens_counts: dict[str, int] = {}
    for m in messages:
        s = m["sensitivity"]
        sens_counts[s] = sens_counts.get(s, 0) + 1

    print(f"\nSmoke dataset — {len(messages)} messages across {len(THREADS)} threads")
    print(f"  Owner:          {args.owner_email}")
    print(f"  Company domain: {args.company_domain}")
    print(f"  Noise:          {noise_count}")
    print(f"  Sensitivity:    {sens_counts}")
    print(f"  Date range:     {messages[0]['date'].date()} — {messages[-1]['date'].date()}")

    if args.dry_run:
        print("\n[dry-run] Messages that would be injected:")
        for msg in messages:
            flag = "NOISE" if msg["noise"] else f"sens={msg['sensitivity']}"
            print(f"  {msg['date'].strftime('%Y-%m-%d')} "
                  f"{msg['thread_key']:<20} {flag:<16} {msg['subject'][:55]}")
    else:
        print("\nImporting into Gmail...")
        import_messages(messages)

    gt_path  = write_ground_truth(messages, args.owner_email, args.company_domain, out_dir)
    mf_path  = write_manifest(messages, out_dir)
    print(f"\nGround truth: {gt_path}")
    print(f"Manifest:     {mf_path}")

    if args.dry_run:
        print("\nDry-run complete. Remove --dry-run to inject into Gmail.")
    else:
        print(f"\nDone. {len(messages)} messages injected.")
        print("\nNext steps:")
        print(f"  1. python scripts/gmail_smoke_ingest.py \\")
        print(f"         --owner-email {args.owner_email} \\")
        print(f"         --internal-domains {args.company_domain} \\")
        print(f"         --max-messages 500 --confirm")
        print(f"  2. python scripts/live_quality_report.py \\")
        print(f"         --mailbox-id <uuid> \\")
        print(f"         --out .local/reports/smoke-quality \\")
        print(f"         --sample-size 100 --include-sensitive")
        print(f"  3. python scripts/fill_smoke_labels.py \\")
        print(f"         --mailbox-id <uuid> \\")
        print(f"         --report-dir .local/reports/smoke-quality \\")
        print(f"         --ground-truth {gt_path} \\")
        print(f"         --out .local/labels/smoke-labels.csv")
        print(f"  4. python scripts/eval_live_quality.py \\")
        print(f"         --report-dir .local/reports/smoke-quality \\")
        print(f"         --labels .local/labels/smoke-labels.csv")


if __name__ == "__main__":
    main()
