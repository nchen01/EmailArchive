"""handoff package tables — draft/generate slice (S17.3, D14, spec s17.2).

@sprint S17 — ticket S17.3.

Adds the read-only handoff-package domain model needed for the creator
draft/scope/generate flow (no publish, no recipient/session yet — those land with
the publish/recipient-view work):

- handoff_package     — lifecycle root (one row per version).
- handoff_scope       — 1:1 selection (date/projects/people/threads + exclusions).
- handoff_claim       — cited continuity statements (>=1 source header each).
- handoff_evidence    — snapshotted SAFE message content (unique per package+header).
- handoff_exclusion   — creator/audit-only record of what was withheld.
- handoff_audit_event — append-only; retained (no FK cascade); safe metadata only.

handoff_recipient is intentionally deferred to the publish/recipient sprint.

These are API-layer product objects, NOT ekc_schemas pipeline contracts, so
SCHEMA_VERSION is deliberately NOT bumped by this migration.

Revision ID: 0007_handoff_package_tables
Revises: 0006_message_embedding
Create Date: 2026-07-16
"""
from alembic import op

revision = "0007_handoff_package_tables"
down_revision = "0006_message_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE handoff_package (
          id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id            uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          creator_email         text NOT NULL,
          creator_person_id     uuid REFERENCES person(id) ON DELETE SET NULL,
          status                text NOT NULL DEFAULT 'draft'
                                  CHECK (status IN ('draft','generated','submitted','approved',
                                                    'rejected','published','expired','revoked','superseded')),
          reason                text NOT NULL
                                  CHECK (reason IN ('vacation','leave','transfer','delegation','other')),
          title                 text NOT NULL DEFAULT '',
          policy_mode           text NOT NULL DEFAULT 'standard',
          version               int  NOT NULL DEFAULT 1,
          supersedes_package_id uuid REFERENCES handoff_package(id) ON DELETE SET NULL,
          lineage_id            uuid NOT NULL,
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now(),
          published_at          timestamptz,
          expires_at            timestamptz,
          revoked_at            timestamptz
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_package_mailbox ON handoff_package (mailbox_id);")
    op.execute("CREATE INDEX ix_handoff_package_lineage ON handoff_package (lineage_id);")

    op.execute(
        """
        CREATE TABLE handoff_scope (
          package_id                   uuid PRIMARY KEY REFERENCES handoff_package(id) ON DELETE CASCADE,
          date_from                    date,
          date_to                      date,
          included_project_ids         uuid[] NOT NULL DEFAULT '{}',
          included_person_ids          uuid[] NOT NULL DEFAULT '{}',
          included_thread_ids          uuid[] NOT NULL DEFAULT '{}',
          excluded_thread_ids          uuid[] NOT NULL DEFAULT '{}',
          excluded_message_id_headers  text[] NOT NULL DEFAULT '{}',
          allowed_domains              text[] NOT NULL DEFAULT '{}',
          keyword_filters              text[] NOT NULL DEFAULT '{}'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE handoff_claim (
          id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          package_id                uuid NOT NULL REFERENCES handoff_package(id) ON DELETE CASCADE,
          kind                      text NOT NULL
                                      CHECK (kind IN ('briefing','project_state','open_loop',
                                                      'decision','blocker','person_note')),
          text                      text NOT NULL,
          project_id                uuid,
          source_message_id_headers text[] NOT NULL
                                      CHECK (cardinality(source_message_id_headers) >= 1),
          confidence                numeric NOT NULL DEFAULT 0
                                      CHECK (confidence BETWEEN 0 AND 1)
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_claim_package ON handoff_claim (package_id);")

    op.execute(
        """
        CREATE TABLE handoff_evidence (
          id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          package_id        uuid NOT NULL REFERENCES handoff_package(id) ON DELETE CASCADE,
          message_id_header text NOT NULL,
          subject           text NOT NULL DEFAULT '',
          sender_display    text NOT NULL DEFAULT '',
          sender_domain     text NOT NULL DEFAULT '',
          ts                timestamptz,
          body_snapshot     text NOT NULL DEFAULT '',
          source_type       text,
          snapshotted_at    timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_handoff_evidence_pkg_header UNIQUE (package_id, message_id_header)
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_evidence_package ON handoff_evidence (package_id);")

    op.execute(
        """
        CREATE TABLE handoff_exclusion (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          package_id     uuid NOT NULL REFERENCES handoff_package(id) ON DELETE CASCADE,
          exclusion_type text NOT NULL
                           CHECK (exclusion_type IN ('sensitivity','user_removed','policy_removed',
                                                     'duplicate','low_confidence')),
          target_type    text NOT NULL
                           CHECK (target_type IN ('message','thread','project','person','claim')),
          target_ref     text NOT NULL DEFAULT '',
          aggregate_label text NOT NULL DEFAULT '',
          audit_reason   text,
          created_at     timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_exclusion_package ON handoff_exclusion (package_id);")

    # Append-only, retained: no FK cascade so audit survives package deletion.
    op.execute(
        """
        CREATE TABLE handoff_audit_event (
          id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
          package_id uuid NOT NULL,
          lineage_id uuid,
          actor      text NOT NULL,
          action     text NOT NULL,
          ts         timestamptz NOT NULL DEFAULT now(),
          metadata   jsonb NOT NULL DEFAULT '{}'
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_audit_package ON handoff_audit_event (package_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS handoff_audit_event;")
    op.execute("DROP TABLE IF EXISTS handoff_exclusion;")
    op.execute("DROP TABLE IF EXISTS handoff_evidence;")
    op.execute("DROP TABLE IF EXISTS handoff_claim;")
    op.execute("DROP TABLE IF EXISTS handoff_scope;")
    op.execute("DROP TABLE IF EXISTS handoff_package;")
