"""Job handlers — importing this package registers every job type (S24/S25).

The API app (main.py) and the worker (scripts/run_worker.py) import this so that
`gmail_ingest_window` (and future handlers) are registered before enqueue/claim.
The `noop` validation type is registered in services/jobs/registry.py.
"""
from . import gmail_ingest  # noqa: F401 — registers "gmail_ingest_window"
from . import pipeline  # noqa: F401 — l1_enrichment/event_extraction/embedding_backfill/project_materialization
