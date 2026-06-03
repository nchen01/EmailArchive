"""Projects & events: project, project_member, thread_project_assignment, event
+ indexes (spec 04 §5,§7).

@sprint S0 — ticket 4.4.

Revision ID: 0004_l1_projects
Revises: 0003_l1_tables
Create Date: 2026-06-03
"""
from alembic import op

revision = "0004_l1_projects"
down_revision = "0003_l1_tables"
branch_labels = None
depends_on = None

_LABEL_SOURCE_VALUES = "'ctfidf','entity','fallback','user'"
_EVENT_TYPE_VALUES = "'proposed','did','outcome'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE project (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          label text NOT NULL,
          label_source text NOT NULL CHECK (label_source IN ({_LABEL_SOURCE_VALUES})),
          start timestamptz NOT NULL,
          "end" timestamptz NOT NULL,
          confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
          debug jsonb
        );
        """
    )

    op.execute(
        """
        CREATE TABLE project_member (
          project_id uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
          person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
          involvement real NOT NULL CHECK (involvement >= 0),
          message_count int NOT NULL CHECK (message_count >= 0),
          PRIMARY KEY (project_id, person_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE thread_project_assignment (
          thread_id uuid NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
          project_id uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
          weight real NOT NULL CHECK (weight > 0 AND weight <= 1),
          is_primary boolean NOT NULL,
          PRIMARY KEY (thread_id, project_id)
        );
        """
    )

    op.execute(
        f"""
        CREATE TABLE event (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          actor_person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
          type text NOT NULL CHECK (type IN ({_EVENT_TYPE_VALUES})),
          summary text NOT NULL,
          project_id uuid REFERENCES project(id) ON DELETE SET NULL,
          source_message_ids text[] NOT NULL,
          confidence real NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0 AND 1),
          -- cardinality() returns 0 for an empty array (array_length returns NULL,
          -- which would let it slip through). Enforces "no citation, no claim".
          CONSTRAINT ck_event_has_source CHECK (cardinality(source_message_ids) >= 1)
        );
        """
    )

    # Indexes (spec 04 §7 — L1 project/event subset).
    op.execute(
        "CREATE INDEX ix_tpa_project ON thread_project_assignment (project_id);"
    )
    op.execute("CREATE INDEX ix_event_project ON event (mailbox_id, project_id);")
    op.execute("CREATE INDEX ix_event_srcs ON event USING gin (source_message_ids);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS event;")
    op.execute("DROP TABLE IF EXISTS thread_project_assignment;")
    op.execute("DROP TABLE IF EXISTS project_member;")
    op.execute("DROP TABLE IF EXISTS project;")
