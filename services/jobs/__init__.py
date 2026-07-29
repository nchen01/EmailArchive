"""Background job infrastructure (S24 — implements docs/s21-background-job-orchestration-plan.md).

Durable, tenant-scoped jobs on a Postgres-backed table with worker claim/lease
mechanics. Infrastructure only — no ingest/enrichment/backfill is moved into jobs
yet. All job metadata (params/progress/summary/errors) is safe-scalar-only; see
sanitize.py. Recipients never touch jobs.
"""
