"""Local environment loader for CLI scripts (dev-only).

Call ``load_local_env()`` inside a script's ``main()`` function, before reading
any environment variables.  Do NOT call it at module import time and do NOT
import it from shared service modules (embed_client, db/engine, API routers, etc.).

Why only CLI entrypoints:
  Loading .env in shared library code creates hidden global behaviour — a module
  import could silently change auth or database configuration depending on
  directory context.  We removed a similar import-time side-effect from
  embed_backfill.py for this exact reason.  Keep the boundary here.

Existing process environment variables always win (override=False).  This means
a CI environment that sets DATABASE_URL or VOYAGE_API_KEY explicitly is never
disturbed by a stale .env file.
"""
from __future__ import annotations


def load_local_env() -> None:
    """Load .env into the process environment for local CLI runs.

    No-ops silently when python-dotenv is not installed, so this function
    is safe to call in any environment without making dotenv a hard dependency.
    Install it with: pip install -e .[dev]
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(override=False)
