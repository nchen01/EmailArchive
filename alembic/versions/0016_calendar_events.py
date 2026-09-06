"""Calendar live/enrichment layer - calendar_event + calendar_event_attendee (S51).

@sprint S51 - implements docs/s49-calendar-first-handoff-context-plan.md section 7
(Option B live layer) + section 9 (calendar_sync_window job).

Adds two SERVICE-DB tables that hold the creator-owned, windowed calendar events a
`calendar_sync_window` job fetches from the connected google_calendar account
(S50). They store ONLY the section-3 safe allow-list: event title, start/end,
all-day flag, organizer display + domain, recurring flag + a human recurrence
summary (NEVER the raw RRULE), conferencing-PRESENCE boolean, and attendee
display + domain (NO attendee email, NO response status - so nothing here can act
as an attendance / productivity signal). Private / private-visibility events are
excluded at ingest and never reach these tables (their titles/attendees are never
persisted).

This is the LIVE layer only - it is creator-owned and NEVER read by recipient
routes. The frozen package-local recipient snapshot (handoff_calendar_item) is a
LATER sprint (S52) and is deliberately NOT created here.

Service-DB only - no ekc_schemas shared-contract change, so SCHEMA_VERSION is NOT
bumped (mirrors the S23/S24 precedent for mailbox_provider_account / job). Purely
additive.

Revision ID: 0016_calendar_events
Revises: 0015_calendar_provider
Create Date: 2026-09-03
"""
from alembic import op

revision = "0016_calendar_events"
down_revision = "0015_calendar_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_event (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           uuid NOT NULL REFERENCES tenant(id),
            mailbox_id          uuid NOT NULL REFERENCES mailbox(id),
            provider_account_id uuid NOT NULL REFERENCES mailbox_provider_account(id),
            calendar_item_id    text NOT NULL,
            calendar_label      text,
            title               text NOT NULL,
            starts_at           timestamptz,
            ends_at             timestamptz,
            all_day             boolean NOT NULL DEFAULT false,
            organizer_display   text,
            organizer_domain    text,
            is_recurring        boolean NOT NULL DEFAULT false,
            recurrence_summary  text,
            has_conferencing    boolean NOT NULL DEFAULT false,
            attendee_count      integer NOT NULL DEFAULT 0,
            synced_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_calendar_event_item UNIQUE (mailbox_id, calendar_item_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_calendar_event_mailbox "
        "ON calendar_event (mailbox_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_event_attendee (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            calendar_event_id uuid NOT NULL REFERENCES calendar_event(id) ON DELETE CASCADE,
            display           text,
            domain            text
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_calendar_event_attendee_event "
        "ON calendar_event_attendee (calendar_event_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calendar_event_attendee;")
    op.execute("DROP TABLE IF EXISTS calendar_event;")
