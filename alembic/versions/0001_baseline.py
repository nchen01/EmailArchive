"""Baseline: extensions + tenant root & operational tables (spec 04 §2-3).

@sprint S0 — ticket 4.1.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-03
"""
from alembic import op

from ekc_schemas import SCHEMA_VERSION

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector";')

    op.execute(
        """
        CREATE TABLE mailbox (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          provider        text NOT NULL CHECK (provider IN ('gmail','msgraph')),
          owner_email     text NOT NULL,
          owner_person_id uuid,
          status          text NOT NULL DEFAULT 'active',
          embed_model     text NOT NULL,
          embed_dim       int  NOT NULL,
          display_threshold real NOT NULL DEFAULT 0.4,
          config          jsonb NOT NULL DEFAULT '{}',
          created_at      timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE sync_state (
          mailbox_id   uuid PRIMARY KEY REFERENCES mailbox(id) ON DELETE CASCADE,
          sync_token   text,
          last_run_at  timestamptz
        );
        """
    )

    op.execute(
        """
        CREATE TABLE audit_log (
          id            bigserial PRIMARY KEY,
          mailbox_id    uuid NOT NULL,
          actor         text NOT NULL,
          action        text NOT NULL,
          scope         text,
          message_count int,
          started_at    timestamptz NOT NULL,
          finished_at   timestamptz,
          sync_token    text
        );
        """
    )

    op.execute(
        """
        CREATE TABLE project_label_override (
          mailbox_id        uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          cluster_signature text NOT NULL,
          label             text NOT NULL,
          updated_at        timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (mailbox_id, cluster_signature)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE schema_meta (
          k text PRIMARY KEY, v text NOT NULL
        );
        """
    )

    # Seed SCHEMA_VERSION from ekc_schemas (spec 04 §9).
    op.execute(
        f"INSERT INTO schema_meta (k, v) VALUES ('SCHEMA_VERSION', '{SCHEMA_VERSION}');"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS schema_meta;")
    op.execute("DROP TABLE IF EXISTS project_label_override;")
    op.execute("DROP TABLE IF EXISTS audit_log;")
    op.execute("DROP TABLE IF EXISTS sync_state;")
    op.execute("DROP TABLE IF EXISTS mailbox;")
