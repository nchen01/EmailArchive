"""Return handoff / coverage delta (S34).

@sprint S34 — implements docs/s33-return-handoff-coverage-delta-plan.md (§10).

A return handoff is a reciprocal package (D15): created from the coverer's mailbox
and sent back to the original covered employee. This migration is additive:

- `handoff_package.package_type` discriminates `coverage` (default; existing rows
  backfill via the DEFAULT) from `return_delta`.
- The `reason` CHECK gains a safe `coverage_return` value (§21.2 — done here
  because we are already altering package constraints).
- `handoff_return_context` records, per return package, how the return draft was
  seeded (original package provenance + carried scope descriptors + seed method).
  SAFE metadata only — no message/evidence bodies, headers, tokens, or provider data.

Service-DB only — no ekc_schemas change, so SCHEMA_VERSION is NOT bumped.

Revision ID: 0013_return_handoff
Revises: 0012_job_infra
Create Date: 2026-07-31
"""
from alembic import op

revision = "0013_return_handoff"
down_revision = "0012_job_infra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) package_type discriminator (existing rows → 'coverage' via DEFAULT).
    op.execute(
        """
        ALTER TABLE handoff_package
            ADD COLUMN IF NOT EXISTS package_type text NOT NULL DEFAULT 'coverage';
        """
    )
    op.execute(
        """
        ALTER TABLE handoff_package
            DROP CONSTRAINT IF EXISTS ck_handoff_package_type;
        ALTER TABLE handoff_package
            ADD CONSTRAINT ck_handoff_package_type
            CHECK (package_type IN ('coverage','return_delta'));
        """
    )

    # 2) Extend the reason enum with a safe 'coverage_return' value (§21.2).
    op.execute(
        """
        ALTER TABLE handoff_package
            DROP CONSTRAINT IF EXISTS handoff_package_reason_check;
        ALTER TABLE handoff_package
            ADD CONSTRAINT handoff_package_reason_check
            CHECK (reason IN ('vacation','leave','transfer','delegation','other','coverage_return'));
        """
    )

    # 3) Return context: one row per return package (safe provenance + seed record).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS handoff_return_context (
            package_id               uuid PRIMARY KEY
                                       REFERENCES handoff_package(id) ON DELETE CASCADE,
            original_package_id      uuid NOT NULL,
            original_lineage_id      uuid,
            original_creator_email   text NOT NULL,
            original_recipient_email text NOT NULL,
            return_date_from         date,
            return_date_to           date,
            carried_project_ids      uuid[] NOT NULL DEFAULT '{}'::uuid[],
            carried_person_ids       uuid[] NOT NULL DEFAULT '{}'::uuid[],
            carried_domains          text[] NOT NULL DEFAULT '{}'::text[],
            carried_area_labels      text[] NOT NULL DEFAULT '{}'::text[],
            seed_method              text NOT NULL DEFAULT 'snapshot_hints',
            created_at               timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_return_seed_method
                CHECK (seed_method IN ('structured','snapshot_hints','mixed'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_return_context_original "
        "ON handoff_return_context (original_package_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS handoff_return_context;")
    op.execute("ALTER TABLE handoff_package DROP CONSTRAINT IF EXISTS ck_handoff_package_type;")
    op.execute("ALTER TABLE handoff_package DROP COLUMN IF EXISTS package_type;")
    op.execute(
        """
        ALTER TABLE handoff_package
            DROP CONSTRAINT IF EXISTS handoff_package_reason_check;
        ALTER TABLE handoff_package
            ADD CONSTRAINT handoff_package_reason_check
            CHECK (reason IN ('vacation','leave','transfer','delegation','other'));
        """
    )
