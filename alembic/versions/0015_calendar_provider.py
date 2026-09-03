"""Widen mailbox_provider_account.provider CHECK to allow google_calendar (S50).

@sprint S50 - implements docs/s49-calendar-first-handoff-context-plan.md section 8.

Google Calendar connect (S50) reuses the shipped mailbox_provider_account +
oauth_state tables and the S23 OAuth/token-vault boundary, but as a DISTINCT
provider row (provider = 'google_calendar') so calendar can be connected /
disconnected and audited independently of Gmail. This migration only widens the
provider CHECK constraint from gmail-only to gmail + google_calendar. No new
tables, no columns; NO calendar_event / handoff_calendar_item tables (those are
deferred to S51/S52).

Service-DB only - no ekc_schemas shared-contract change, so SCHEMA_VERSION is NOT
bumped. Purely additive on existing data (existing rows are all 'gmail', still
valid under the widened constraint).

Revision ID: 0015_calendar_provider
Revises: 0014_handoff_claim_project_label
Create Date: 2026-09-01
"""
from alembic import op

revision = "0015_calendar_provider"
down_revision = "0014_handoff_claim_project_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mailbox_provider_account "
        "DROP CONSTRAINT IF EXISTS ck_provider_account_provider;"
    )
    op.execute(
        "ALTER TABLE mailbox_provider_account "
        "ADD CONSTRAINT ck_provider_account_provider "
        "CHECK (provider IN ('gmail','google_calendar'));"
    )


def downgrade() -> None:
    # Revert to gmail-only. Safe only if no google_calendar rows exist; a stray
    # calendar row would block the downgrade (intended - do not silently drop data).
    op.execute(
        "ALTER TABLE mailbox_provider_account "
        "DROP CONSTRAINT IF EXISTS ck_provider_account_provider;"
    )
    op.execute(
        "ALTER TABLE mailbox_provider_account "
        "ADD CONSTRAINT ck_provider_account_provider CHECK (provider IN ('gmail'));"
    )
