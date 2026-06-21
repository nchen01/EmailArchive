"""S8.5 real-mailbox smoke eval fixtures for puluo1938@gmail.com.

Eight curated cases against mailbox_id e21c187a-956a-47ee-92aa-b21badd16f4d.
All message_id_header values are RFC 5322 IDs stored by the ingest pipeline
(angle brackets stripped by norm_mid, lower-cased).

Mailbox composition (as of S8.1 backfill 2026-06-20):
  460 total messages; 67 embedded (voyage-4); 379 noise; 16 sensitive.
  Embedded set = 26 real messages (SSA, Zillow, VTA, etc.)
                + 41 synthetic smoke messages (acme.corp project threads
                  injected via generate_smoke_dataset.py).

Case inventory:
  S1  project / threaded    Nexus API JWT authentication design
  S2  project / threaded    Production P1 API latency incident
  S3  project               Q3 engineering headcount and budget
  S4  operational           External security audit remediation
  S5  contact/institutional Social Security benefit amount (real mail)
  S6  sensitive gate (hr)   Performance review career compensation
  S7  sensitive gate (priv) Vertex Partners MSA attorney-client privileged
  S8  insufficient evidence xyzzy sentinel — must return InsufficientEvidence
"""
from __future__ import annotations

from services.retrieval.eval.fixtures import RetrievalCase, VOYAGE_EVAL_PARAMS  # noqa: F401

# ── Smoke eval params ─────────────────────────────────────────────────────────
# Re-export so callers can do: from smoke_fixtures import SMOKE_EVAL_PARAMS
SMOKE_EVAL_PARAMS = VOYAGE_EVAL_PARAMS

# ── Smoke eval cases ──────────────────────────────────────────────────────────

SMOKE_EVAL_CASES: list[RetrievalCase] = [

    # S1: Nexus API JWT auth design (project thread, synthetic)
    # Thread: smoke-nexus-design — 5 messages on JWT token auth layer design.
    # All embedded; query matches key terms from the initial design and owner
    # response (circuit breaker, Redis, session cap, short-lived tokens).
    RetrievalCase(
        query="Nexus API JWT token authentication Redis circuit breaker design",
        expected_headers=[
            "smoke-nexus-design-0@smoke.generated",
            "smoke-nexus-design-1@smoke.generated",
        ],
        forbidden_headers=[
            "smoke-legal-msa-0@smoke.generated",      # privileged — must never appear
            "smoke-hr-perf-0@smoke.generated",         # hr — must never appear
            "smoke-oneone-manager-0@smoke.generated",  # hr — must never appear
        ],
        expected_route="hybrid",
    ),

    # S2: P1 production API latency incident (project thread, synthetic)
    # Thread: smoke-incident-api — P1 triage, root-cause (missing DB index),
    # resolution, and post-incident actions.
    RetrievalCase(
        query="production P1 API latency spike database connection pool index",
        expected_headers=[
            "smoke-incident-api-0@smoke.generated",   # initial triage message
            "smoke-incident-api-4@smoke.generated",   # resolution and RCA
        ],
        forbidden_headers=[
            "smoke-legal-msa-0@smoke.generated",
            "smoke-oneone-manager-0@smoke.generated",
        ],
        expected_route="hybrid",
    ),

    # S3: Q3 engineering headcount and ML engineer budget (project, synthetic)
    # Thread: smoke-budget-q3 — finance request, Sarah's response with +3 FTE,
    # owner's ML engineer justification (budget-q3-2), CTO approval (budget-q3-3).
    # voyage-4 ranks the ML engineer justification and CTO approval highest for
    # this query (0.81 / 0.74); the generic finance kickoff message scores lower.
    RetrievalCase(
        query="Q3 engineering headcount budget ML engineer hire vector retrieval",
        expected_headers=[
            "smoke-budget-q3-2@smoke.generated",   # owner's ML engineer justification
            "smoke-budget-q3-3@smoke.generated",   # CTO approval
        ],
        forbidden_headers=[
            "smoke-hiring-backend-0@smoke.generated",  # hr — candidate shortlist
        ],
        expected_route="hybrid",
    ),

    # S4: External security audit JWT key rotation rate limiting (operational, synthetic)
    # Thread: smoke-security-audit — Arcadia audit findings, JWT rotation plan,
    # rate limiting on auth/token, CVE remediation table.
    RetrievalCase(
        query="external security audit JWT key rotation rate limiting CVE remediation",
        expected_headers=[
            "smoke-security-audit-0@smoke.generated",   # audit findings
            "smoke-security-audit-1@smoke.generated",   # owner takes ownership
        ],
        forbidden_headers=[
            "smoke-legal-msa-0@smoke.generated",
            "smoke-oneone-manager-0@smoke.generated",
        ],
        expected_route="hybrid",
    ),

    # S5: Social Security benefit amount (institutional contact, real mail)
    # Three real SSA newsletter messages with nearly identical subject lines
    # "View Your New Benefit Amount Using Your my Social Security Account".
    # These are non-noise, sensitivity=['none'], and were embedded in S8.1.
    RetrievalCase(
        query="Social Security benefit amount cost of living adjustment",
        expected_headers=[
            "17145925.1347281@subscriptions.ssa.gov",
            "16826254.804604@subscriptions.ssa.gov",
        ],
        forbidden_headers=[],
        expected_route="l2_fallback",
    ),

    # S6: Sensitive gate — HR messages must not appear (hr sensitivity)
    # smoke-oneone-manager (hr): manager 1:1 notes with compensation/career content.
    # smoke-hr-perf (hr): H1 performance review cycle emails.
    # smoke-hiring-backend (hr): candidate shortlist with salary expectations.
    # These are excluded from embedding and filtered in SQL; gate validates both.
    RetrievalCase(
        query="H1 performance review Lattice compensation salary career staff engineer",
        expected_headers=[],
        forbidden_headers=[
            "smoke-oneone-manager-0@smoke.generated",
            "smoke-hr-perf-0@smoke.generated",
            "smoke-hiring-backend-0@smoke.generated",
        ],
        expected_route="l2_fallback",
    ),

    # S7: Sensitive gate — privileged legal messages must not appear
    # smoke-legal-msa (privileged): Vertex Partners MSA attorney-client review.
    # Not embedded; SQL sensitivity filter blocks them. Gate validates both.
    RetrievalCase(
        query="Vertex Partners MSA attorney client privileged indemnification GDPR liability",
        expected_headers=[],
        forbidden_headers=[
            "smoke-legal-msa-0@smoke.generated",
            "smoke-legal-msa-1@smoke.generated",
            "smoke-legal-msa-2@smoke.generated",
        ],
        expected_route="l2_fallback",
    ),

    # S8: Unanswerable query — must return InsufficientEvidence
    # "xyzzy" is the designated sentinel token checked by gate 6 in _check_case.
    # No message in the puluo mailbox contains these tokens; voyage-4 cosine
    # similarity will be below min_vector_score=0.60 for all documents.
    RetrievalCase(
        query="xyzzy frobnicator spaghetti",
        expected_headers=[],
        forbidden_headers=[],
        expected_route="l2_fallback",
    ),
]
