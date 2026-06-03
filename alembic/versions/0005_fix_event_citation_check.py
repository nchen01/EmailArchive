"""Harden event citation CHECK: array_length('{}',1) is NULL (slips through);
use cardinality() which returns 0 for an empty array (spec 04 DoD).

@sprint S0 — ticket 4.4 (fix).

Revision ID: 0005_fix_event_citation_check
Revises: 0004_l1_projects
Create Date: 2026-06-03
"""
from alembic import op

revision = "0005_fix_event_citation_check"
down_revision = "0004_l1_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the original anonymous/array_length CHECK if present, add the named one.
    op.execute(
        """
        DO $$
        DECLARE c text;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'event'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%source_message_ids%'
          LOOP
            EXECUTE format('ALTER TABLE event DROP CONSTRAINT %I', c);
          END LOOP;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE event ADD CONSTRAINT ck_event_has_source "
        "CHECK (cardinality(source_message_ids) >= 1);"
    )


def downgrade() -> None:
    # 0004 already creates ck_event_has_source with cardinality() — the correct form.
    # Restoring array_length() would reintroduce the D8 bug and leave the schema
    # inconsistent with what 0004 defines. Downgrade is therefore a no-op.
    pass
