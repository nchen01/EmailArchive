r"""S43 - Handoff quality evaluation runner.

Runs the synthetic handoff-eval corpus against the real (deterministic, LLM-free)
generator and prints a quality report. Offline: requires a local DATABASE_URL
(Postgres); no Anthropic / Voyage / Gmail / network calls. Creates and destroys its
own throwaway mailboxes and never touches real data.

Usage (PowerShell):
    $env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_test'
    .\.venv\Scripts\python.exe scripts\eval_handoff_quality.py
    .\.venv\Scripts\python.exe scripts\eval_handoff_quality.py --json

Exit code is 0 only if every scenario passes all hard gates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.handoff.eval.corpus import default_corpus_dir, load_corpus  # noqa: E402
from services.handoff.eval.harness import run_scenario  # noqa: E402
from services.handoff.eval.report import format_console, to_json  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Handoff quality evaluation harness (S43).")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a console report")
    ap.add_argument("--corpus", default=None, help="corpus dir (default fixtures/handoff_eval)")
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is required (offline local Postgres). "
              "No external API is used. Example:\n"
              "  $env:DATABASE_URL='postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_test'",
              file=sys.stderr)
        sys.exit(2)

    from services.db.engine import SessionLocal

    corpus = load_corpus(args.corpus or default_corpus_dir())
    if not corpus:
        print("ERROR: no scenarios found in corpus.", file=sys.stderr)
        sys.exit(2)

    session = SessionLocal()
    results = []
    try:
        for sc in corpus:
            results.append(run_scenario(session, sc.data))
    finally:
        session.close()

    if args.json:
        print(json.dumps([to_json(r) for r in results], indent=2))
    else:
        print(format_console(results))

    sys.exit(0 if all(r.hard_pass for r in results) else 1)


if __name__ == "__main__":
    main()
