"""Recipient handoff project grouping: snapshot coverage labels (S39).

@sprint S39 - implements docs/s39-recipient-project-grouping-plan.md.

Additive, service-DB-only migration. Adds a nullable ``project_label`` column to
``handoff_claim`` so a published package can carry the display label of each
claim's project, FROZEN at generate time from the creator/coverer-owned mailbox's
project table. The recipient view reads this snapshot label to group by real
project and never resolves ``project_id`` against live project rows.

Existing rows keep ``project_label = NULL`` (no backfill); the recipient UI falls
back to the existing coverageAreas text-clustering for those packages.

Service-DB only - no ekc_schemas change, so SCHEMA_VERSION is NOT bumped.

Revision ID: 0014_handoff_claim_project_label
Revises: 0013_return_handoff
Create Date: 2026-08-14
"""
from alembic import op

revision = "0014_handoff_claim_project_label"
down_revision = "0013_return_handoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE handoff_claim
            ADD COLUMN IF NOT EXISTS project_label text;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE handoff_claim
            DROP COLUMN IF EXISTS project_label;
        """
    )
