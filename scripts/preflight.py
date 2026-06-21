"""CLI preflight check for the Email Knowledge Continuity stack (S8.3).

Usage:
    python scripts/preflight.py
    python scripts/preflight.py --mailbox-id <uuid>

Exit codes:
    0 — all required checks passed (warns and infos are non-fatal)
    1 — at least one required check failed
"""
from __future__ import annotations

import argparse
import sys


_STATUS_ICON = {
    "pass": "[pass]",
    "fail": "[FAIL]",
    "warn": "[warn]",
    "info": "[info]",
}


def main() -> None:
    from pathlib import Path
    _root = str(Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from scripts._env import load_local_env
    load_local_env()

    from services.preflight import run_checks

    parser = argparse.ArgumentParser(description="EKC operational preflight check")
    parser.add_argument("--mailbox-id", metavar="UUID", help="verify embeddings for this mailbox")
    args = parser.parse_args()

    checks = run_checks(mailbox_id=args.mailbox_id)

    any_fail = False
    for c in checks:
        icon = _STATUS_ICON.get(c.status, c.status.upper())
        print(f"{icon}  {c.name}: {c.message}")
        if c.failed:
            any_fail = True

    print()
    if any_fail:
        print("Preflight FAILED — fix the issues above before running the stack.")
        sys.exit(1)
    else:
        print("Preflight OK.")
        sys.exit(0)


if __name__ == "__main__":
    main()
