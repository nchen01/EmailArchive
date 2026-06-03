"""L0 tables: thread, message, message_attachment + indexes + FTS (spec 04 §4,§7).

@sprint S0 — ticket 4.2.

Revision ID: 0002_l0_tables
Revises: 0001_baseline
Create Date: 2026-06-03
"""
from alembic import op

revision = "0002_l0_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_SENSITIVITY_VALUES = "'none','privileged','legal','hr','personal'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE thread (
          id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id             uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          provider_thread_ids    text[] NOT NULL DEFAULT '{}',
          root_message_id_header text,
          subject_norm           text NOT NULL DEFAULT '',
          participants           text[] NOT NULL DEFAULT '{}',
          t_start                timestamptz NOT NULL,
          t_end                  timestamptz NOT NULL,
          lineage_conflict       boolean NOT NULL DEFAULT false,
          created_at             timestamptz NOT NULL DEFAULT now(),
          updated_at             timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    # message: clean_text_tsv is a GENERATED column (hand-written, not autogen).
    op.execute(
        f"""
        CREATE TABLE message (
          id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id         uuid NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
          message_id_header  text NOT NULL,
          provider_id        text NOT NULL,
          thread_id          uuid NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
          sender_email       text NOT NULL,
          to_emails          text[] NOT NULL DEFAULT '{{}}',
          cc_emails          text[] NOT NULL DEFAULT '{{}}',
          addresses          jsonb NOT NULL DEFAULT '{{}}',
          ts                 timestamptz NOT NULL,
          subject            text NOT NULL DEFAULT '',
          clean_text         text NOT NULL DEFAULT '',
          clean_text_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', clean_text)) STORED,
          link_domains       text[] NOT NULL DEFAULT '{{}}',
          sensitivity        text[] NOT NULL DEFAULT '{{none}}',
          noise              boolean NOT NULL DEFAULT false,
          raw_uri            text,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          UNIQUE (mailbox_id, message_id_header),
          CONSTRAINT ck_message_sensitivity
            CHECK (sensitivity <@ ARRAY[{_SENSITIVITY_VALUES}]::text[])
        );
        """
    )

    op.execute(
        """
        CREATE TABLE message_attachment (
          message_id uuid NOT NULL REFERENCES message(id) ON DELETE CASCADE,
          sha256     text NOT NULL,
          filename   text,
          mimetype   text NOT NULL,
          size_bytes bigint NOT NULL,
          PRIMARY KEY (message_id, sha256)
        );
        """
    )

    # Indexes (spec 04 §7 — L0).
    op.execute("CREATE INDEX ix_message_thread ON message (mailbox_id, thread_id);")
    op.execute("CREATE INDEX ix_message_ts     ON message (mailbox_id, ts);")
    op.execute("CREATE INDEX ix_message_sender ON message (mailbox_id, sender_email);")
    op.execute("CREATE INDEX ix_message_fts    ON message USING gin (clean_text_tsv);")
    op.execute(
        "CREATE INDEX ix_message_live ON message (mailbox_id) WHERE noise = false;"
    )
    op.execute("CREATE INDEX ix_attach_sha ON message_attachment (sha256);")
    op.execute("CREATE INDEX ix_thread_parts ON thread USING gin (participants);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS message_attachment;")
    op.execute("DROP TABLE IF EXISTS message;")
    op.execute("DROP TABLE IF EXISTS thread;")
