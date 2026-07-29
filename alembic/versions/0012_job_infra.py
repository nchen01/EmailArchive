"""Background job infrastructure — the `job` table (S24).

@sprint S24 — implements docs/s21-background-job-orchestration-plan.md (§2, §3, §11).

A single tenant-scoped `job` table for durable background work. Infrastructure
only — no ingest/enrichment/backfill is moved into jobs yet. params/progress/
summary/error_* hold SAFE metadata only (sanitized in services/jobs/sanitize.py).

Includes a PARTIAL unique index so the same idempotency_key cannot have two ACTIVE
(queued/running) jobs in a tenant, while a key can be reused once its job is
terminal. A partial index on queued jobs backs the worker claim query.

Service-DB only — no ekc_schemas change, so SCHEMA_VERSION is NOT bumped. Additive.

Revision ID: 0012_job_infra
Revises: 0011_gmail_oauth
Create Date: 2026-07-28
"""
from alembic import op

revision = "0012_job_infra"
down_revision = "0011_gmail_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job (
            id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             uuid NOT NULL,
            requested_by_user_id  uuid,
            mailbox_id            uuid,
            job_type              text NOT NULL,
            status                text NOT NULL DEFAULT 'queued',
            params                jsonb NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key       text,
            progress              jsonb NOT NULL DEFAULT '{}'::jsonb,
            summary               text,
            error_category        text,
            error_message         text,
            attempt               integer NOT NULL DEFAULT 0,
            max_attempts          integer NOT NULL DEFAULT 1,
            next_retry_at         timestamptz,
            cancel_requested_at   timestamptz,
            lease_expires_at      timestamptz,
            worker_id             text,
            created_at            timestamptz NOT NULL DEFAULT now(),
            started_at            timestamptz,
            finished_at           timestamptz,
            CONSTRAINT ck_job_status CHECK (
                status IN ('queued','running','succeeded','failed','canceled','partially_succeeded')
            )
        );
        """
    )
    # One active job per (tenant, idempotency_key): dedupe double-submits.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_idem
        ON job (tenant_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL AND status IN ('queued','running');
        """
    )
    # Worker claim query: queued jobs due to run.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_job_claimable
        ON job (created_at)
        WHERE status = 'queued';
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_tenant ON job (tenant_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_mailbox ON job (mailbox_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job;")
