"""L1 tables: org, person, identity, edge + indexes (spec 04 §5,§7).

@sprint S0 — ticket 4.3.

Revision ID: 0003_l1_tables
Revises: 0002_l0_tables
Create Date: 2026-06-03
"""
from alembic import op

revision = "0003_l1_tables"
down_revision = "0002_l0_tables"
branch_labels = None
depends_on = None

_ROLE_VALUES = "'account_exec','lead','internal','manager','vendor','unknown'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE org (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          name text NOT NULL,
          domains text[] NOT NULL DEFAULT '{}',
          internal boolean NOT NULL DEFAULT false
        );
        """
    )

    op.execute(
        f"""
        CREATE TABLE person (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          canonical_email text NOT NULL,
          names text[] NOT NULL DEFAULT '{{}}',
          org_id uuid REFERENCES org(id) ON DELETE SET NULL,
          role text NOT NULL DEFAULT 'unknown' CHECK (role IN ({_ROLE_VALUES})),
          role_confidence real NOT NULL DEFAULT 0.0 CHECK (role_confidence BETWEEN 0 AND 1),
          UNIQUE (mailbox_id, canonical_email)
        );
        """
    )

    # identity.mailbox_id is TEXT (not a UUID FK) — spec 04 §5 note.
    op.execute(
        """
        CREATE TABLE identity (
          mailbox_id text NOT NULL,
          email text NOT NULL,
          display_names text[] NOT NULL DEFAULT '{}',
          person_id uuid REFERENCES person(id) ON DELETE CASCADE,
          PRIMARY KEY (mailbox_id, email)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE edge (
          mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
          message_count int NOT NULL CHECK (message_count >= 0),
          sent_to_count int NOT NULL CHECK (sent_to_count >= 0),
          received_count int NOT NULL CHECK (received_count >= 0),
          first_contact timestamptz NOT NULL,
          last_contact timestamptz NOT NULL,
          weight real NOT NULL CHECK (weight >= 0),
          PRIMARY KEY (mailbox_id, person_id)
        );
        """
    )

    # Indexes (spec 04 §7 — L1, edge subset).
    op.execute("CREATE INDEX ix_edge_weight ON edge (mailbox_id, weight DESC);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS edge;")
    op.execute("DROP TABLE IF EXISTS identity;")
    op.execute("DROP TABLE IF EXISTS person;")
    op.execute("DROP TABLE IF EXISTS org;")
