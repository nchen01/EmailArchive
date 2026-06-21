"""S7.10 retrieval eval runner.

Callable as a function (from tests or other scripts) and as a standalone CLI:
    python -m services.retrieval.eval.run_eval --mailbox-id <uuid>
    python -m services.retrieval.eval.run_eval --mailbox-id <uuid> --embed-client voyage

Hard gates (any failure → non-zero exit / raises EvalFailure):
  1. Every expected_header appears in the top-10 results.
  2. No forbidden_header appears at any rank.
  3. No sensitive message (sensitivity != ['none']) appears when
     include_sensitive=False (the default).
  4. No noise message appears.
  5. Every returned message_id_header exists in the fixture DB.
  6. InsufficientEvidence is returned for the unanswerable query (C7).

Soft targets (reported, not blocking):
  - Mean Reciprocal Rank (MRR) >= 0.6 across cases that have expected_headers.
  - Top-1 precision >= 0.5 across the same subset.

Embed client (--embed-client flag):
  fake   (default) FakeEmbedClient — deterministic, offline, no API key needed.
                   Use this for CI and all automated tests.
  voyage           VoyageEmbedClient — real voyage-4 queries against voyage-4
                   indexed document embeddings.  Requires VOYAGE_API_KEY.
                   Use this for live L2 validation after a real backfill.

AUTHORIZATION: --embed-client voyage calls the Voyage AI API and must not be
run without the repo owner's explicit instruction for that specific run
(see CLAUDE.md Voyage API key rules).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.retrieval.contracts import InsufficientEvidence, RetrievalHit
from services.retrieval.embed_client import EmbedClient, FakeEmbedClient
from services.retrieval.eval.fixtures import (
    EVAL_CASES,
    EVAL_PARAMS,
    VOYAGE_EVAL_PARAMS,
    RetrievalCase,
)
from services.retrieval.hybrid import hybrid_search
from services.retrieval.params import RetrievalParams

_FIXTURE_CHOICES = ("fixture", "smoke")


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case:          RetrievalCase
    hits:          list[RetrievalHit] | InsufficientEvidence
    passed:        bool
    failures:      list[str]   # human-readable failure descriptions
    reciprocal_rank: float     # 0.0 when no expected_header found


@dataclass
class EvalResult:
    case_results:       list[CaseResult]
    hard_gates_passed:  bool
    mrr:                float   # mean reciprocal rank over cases with expected_headers
    top1_precision:     float   # fraction of those cases where top-1 is expected


class EvalFailure(Exception):
    """Raised by run_eval() when at least one hard gate fails."""


# ── Core eval logic ───────────────────────────────────────────────────────────

def _check_case(
    case: RetrievalCase,
    result: list[RetrievalHit] | InsufficientEvidence,
    all_db_headers: set[str],
) -> CaseResult:
    """Evaluate one case against the hard gates; return a CaseResult."""
    failures: list[str] = []
    hits = result if isinstance(result, list) else []

    returned_headers = [h.message_id_header for h in hits]
    top10_headers    = set(returned_headers[:10])

    # Gate 1: every expected_header appears in top-10.
    for hdr in case.expected_headers:
        if hdr not in top10_headers:
            failures.append(
                f"expected {hdr!r} in top-10 but got: "
                f"{returned_headers[:10]}"
            )

    # Gate 2: no forbidden_header appears at any rank.
    for hdr in case.forbidden_headers:
        if hdr in returned_headers:
            failures.append(f"forbidden {hdr!r} appeared in results")

    # Gate 3: no sensitive hit when include_sensitive=False (the eval default).
    if not case.allow_sensitive_in_result:
        for hit in hits:
            if list(hit.sensitivity) != ["none"]:
                failures.append(
                    f"sensitive message {hit.message_id_header!r} "
                    f"(sensitivity={list(hit.sensitivity)}) leaked into results"
                )

    # Gate 4: no noise hit.
    for hit in hits:
        if hit.noise:
            failures.append(f"noise message {hit.message_id_header!r} appeared in results")

    # Gate 5: every returned header exists in the DB.
    for hdr in returned_headers:
        if hdr not in all_db_headers:
            failures.append(f"returned {hdr!r} not found in DB — citation invalid")

    # Gate 6: unanswerable query must return InsufficientEvidence.
    # Identified by: no expected_headers AND no forbidden_headers AND query
    # contains 'xyzzy' (the designated unanswerable sentinel token).
    if "xyzzy" in case.query and not isinstance(result, InsufficientEvidence):
        failures.append(
            f"unanswerable query returned {len(hits)} hits; "
            f"expected InsufficientEvidence"
        )

    # Reciprocal rank for soft targets.
    rr = 0.0
    if case.expected_headers:
        for rank, hdr in enumerate(returned_headers, start=1):
            if hdr in case.expected_headers:
                rr = 1.0 / rank
                break

    return CaseResult(
        case=case,
        hits=hits,
        passed=len(failures) == 0,
        failures=failures,
        reciprocal_rank=rr,
    )


def run_eval(
    session,
    mailbox_id: str,
    embed_client: EmbedClient | None = None,
    *,
    params: RetrievalParams | None = None,
    cases: list[RetrievalCase] | None = None,
    verbose: bool = False,
) -> EvalResult:
    """Run eval cases against hybrid_search for ``mailbox_id``.

    ``embed_client`` defaults to FakeEmbedClient(dim=1024, model='fake-embed').
    ``params`` defaults to EVAL_PARAMS (fake-embed calibrated); pass
    VOYAGE_EVAL_PARAMS when using VoyageEmbedClient so embed_model and
    min_vector_score match the indexed documents.
    ``cases`` defaults to EVAL_CASES (fixture mailbox); pass SMOKE_EVAL_CASES
    for the S8.5 real-mailbox eval.

    All eval queries are embedded in a single batch call (embed_queries_batch)
    to avoid per-query API rate-limit hits when using VoyageEmbedClient.
    """
    from sqlalchemy import text

    _client = embed_client or FakeEmbedClient(dim=1024, model="fake-embed")
    _params = params or EVAL_PARAMS
    _cases  = cases if cases is not None else EVAL_CASES

    # Load all message_id_headers present in the DB for gate 5.
    db_headers_rows = session.execute(
        text("SELECT message_id_header FROM message WHERE mailbox_id = :mid"),
        {"mid": mailbox_id},
    ).all()
    all_db_headers = {r[0] for r in db_headers_rows}

    # Pre-embed all queries in one batch call — single API request regardless
    # of how many eval cases exist, avoiding per-query rate-limit hits.
    queries = [case.query for case in _cases]
    query_vecs = _client.embed_queries_batch(queries)

    case_results: list[CaseResult] = []
    for case, query_vec in zip(_cases, query_vecs):
        result = hybrid_search(
            session,
            mailbox_id,
            query_vec,
            case.query,
            _params,
        )
        cr = _check_case(case, result, all_db_headers)
        case_results.append(cr)

        if verbose:
            status = "PASS" if cr.passed else "FAIL"
            print(f"  [{status}] {case.query!r}")
            for f in cr.failures:
                print(f"         x {f}")
            if not isinstance(result, InsufficientEvidence):
                for h in result[:3]:
                    print(f"         -> {h.message_id_header} (score={h.rerank_score:.3f})")

    # Soft metrics over cases that have expected_headers.
    ranked_cases = [cr for cr in case_results if cr.case.expected_headers]
    mrr = (
        sum(cr.reciprocal_rank for cr in ranked_cases) / len(ranked_cases)
        if ranked_cases else 0.0
    )
    top1_precision = (
        sum(1 for cr in ranked_cases if cr.reciprocal_rank == 1.0) / len(ranked_cases)
        if ranked_cases else 0.0
    )

    hard_gates_passed = all(cr.passed for cr in case_results)

    result_obj = EvalResult(
        case_results=case_results,
        hard_gates_passed=hard_gates_passed,
        mrr=mrr,
        top1_precision=top1_precision,
    )

    if not hard_gates_passed:
        failures_summary = "\n".join(
            f"  Case {i} ({cr.case.query!r}): {'; '.join(cr.failures)}"
            for i, cr in enumerate(case_results)
            if not cr.passed
        )
        raise EvalFailure(
            f"Retrieval eval FAILED — {sum(1 for cr in case_results if not cr.passed)} "
            f"case(s) failed hard gates:\n{failures_summary}"
        )

    return result_obj


# ── Standalone CLI ────────────────────────────────────────────────────────────

def _build_embed_client(name: str) -> EmbedClient:
    """Construct the requested embed client, failing fast on misconfiguration."""
    if name == "fake":
        return FakeEmbedClient(dim=1024, model="fake-embed")
    if name == "voyage":
        import os
        key = os.environ.get("VOYAGE_API_KEY")
        if not key:
            print(
                "ERROR: --embed-client voyage requires VOYAGE_API_KEY to be set.",
                file=sys.stderr,
            )
            sys.exit(1)
        from services.retrieval.embed_client import VoyageEmbedClient
        return VoyageEmbedClient(api_key=key)
    raise ValueError(f"Unknown embed client: {name!r}. Choose 'fake' or 'voyage'.")


def main(argv: list[str] | None = None) -> None:
    import argparse
    from services.db.engine import SessionLocal

    parser = argparse.ArgumentParser(
        description="Run the S7.10 retrieval eval against a seeded mailbox."
    )
    parser.add_argument("--mailbox-id", required=True, metavar="UUID")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--embed-client",
        choices=["fake", "voyage"],
        default="fake",
        help=(
            "'fake' (default): FakeEmbedClient, offline, no key needed. "
            "'voyage': VoyageEmbedClient, requires VOYAGE_API_KEY — use only "
            "after a real voyage-4 backfill to validate end-to-end retrieval."
        ),
    )
    parser.add_argument(
        "--fixture",
        choices=list(_FIXTURE_CHOICES),
        default="fixture",
        help=(
            "'fixture' (default): run EVAL_CASES against the standard 18-message "
            "fixture mailbox (FakeEmbedClient or voyage). "
            "'smoke': run SMOKE_EVAL_CASES (S8.5) against the real puluo mailbox "
            "(mailbox_id e21c187a-956a-47ee-92aa-b21badd16f4d) — requires "
            "--embed-client voyage and a completed voyage-4 backfill."
        ),
    )
    args = parser.parse_args(argv)

    from scripts._env import load_local_env
    load_local_env()

    embed_client = _build_embed_client(args.embed_client)
    eval_params  = VOYAGE_EVAL_PARAMS if args.embed_client == "voyage" else EVAL_PARAMS

    if args.fixture == "smoke":
        from services.retrieval.eval.smoke_fixtures import SMOKE_EVAL_CASES
        selected_cases = SMOKE_EVAL_CASES
    else:
        selected_cases = EVAL_CASES

    print(
        f"embed client : {args.embed_client} "
        f"(model={embed_client.model}, dim={embed_client.dim})"
    )
    print(
        f"eval params  : embed_model={eval_params.embed_model} "
        f"min_vector_score={eval_params.min_vector_score}"
    )
    print(f"fixture      : {args.fixture} ({len(selected_cases)} cases)")

    session = SessionLocal()
    try:
        print(f"mailbox      : {args.mailbox_id}")
        print("Running retrieval eval (queries batched into one embed call) ...")
        result = run_eval(
            session, args.mailbox_id,
            embed_client=embed_client,
            params=eval_params,
            cases=selected_cases,
            verbose=args.verbose,
        )
        print(
            f"\nAll {len(result.case_results)} hard gates PASSED  "
            f"MRR={result.mrr:.3f}  top-1 precision={result.top1_precision:.3f}"
        )
        mrr_target  = 0.6
        top1_target = 0.5
        if result.mrr < mrr_target:
            print(f"  ⚠  MRR {result.mrr:.3f} below soft target {mrr_target}")
        if result.top1_precision < top1_target:
            print(f"  ⚠  top-1 precision {result.top1_precision:.3f} below soft target {top1_target}")
    except EvalFailure as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
