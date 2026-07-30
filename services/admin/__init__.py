"""S29 — read-only Admin / Audit Viewer read models (docs/s28-admin-audit-ops-plan.md).

Safe-metadata-only governance/ops read service. Every DTO here is an explicit
allow-list — it names the safe fields it exposes and never spreads a raw ORM row
or JSONB blob. No function in this package returns mailbox/package content
(evidence bodies, claim text, scope detail, source headers), OAuth tokens/codes/
vault refs, raw job params/errors, DB URLs, env values, or tracebacks. All reads
are tenant-scoped by the caller's principal (cross-tenant rows are absent → 404).
"""
