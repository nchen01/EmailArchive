"""message_embedding table + HNSW index + subject_clean_tsv FTS column (S7.1).

@sprint S7 — ticket 7.1 (D12b, spec 04 §6).

Adds:
- message.subject_clean_tsv: combined subject + clean_text tsvector for FTS (S7.7).
- message_embedding: one row per (message, embed_model). voyage-4 at 1024-dim.
  UNIQUE on (message_id, embed_model) enables idempotent upserts and future
  multi-model coexistence. content_hash allows the backfill script to skip
  messages whose text has not changed since last embedding.
- HNSW index on embedding using cosine distance (vector_cosine_ops).
- Covering index on (mailbox_id, embed_model) for backfill and retrieval.
- schema_meta SCHEMA_VERSION bumped from 0.1.0 to 0.2.0 (spec 04 §9 DoD).

Revision ID: 0006_message_embedding
Revises: 0005_fix_event_citation_check
Create Date: 2026-06-10
"""
from alembic import op

# Pin both values as constants. Do NOT import from ekc_schemas — the package
# constant reflects the current code version, not migration 0006's historical
# target. A future SCHEMA_VERSION bump would cause this migration to write the
# wrong value when replayed from scratch on a clean database.
_SCHEMA_VERSION      = "0.2.0"
_PREV_SCHEMA_VERSION = "0.1.0"

revision = "0006_message_embedding"
down_revision = "0005_fix_event_citation_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Combined FTS column: subject + clean_text (S7.7).
    # Follows the same GENERATED ALWAYS AS STORED pattern as clean_text_tsv
    # (migration 0002). Not mapped in the ORM — the Python layer uses
    # websearch_to_tsquery against it via raw SQL.
    op.execute(
        """
        ALTER TABLE message
          ADD COLUMN subject_clean_tsv tsvector
          GENERATED ALWAYS AS (
            to_tsvector('english',
              coalesce(subject, '') || ' ' || coalesce(clean_text, ''))
          ) STORED;
        """
    )
    op.execute(
        "CREATE INDEX ix_message_subject_clean_fts "
        "ON message USING gin (subject_clean_tsv);"
    )

    # message_embedding — one embedding row per (message, embed_model).
    # vector(1024) is locked to voyage-4 for S7. Future model migrations
    # require a new table or an ALTER COLUMN (pgvector supports this).
    op.execute(
        """
        CREATE TABLE message_embedding (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          mailbox_id   uuid NOT NULL REFERENCES mailbox(id)  ON DELETE CASCADE,
          message_id   uuid NOT NULL REFERENCES message(id)  ON DELETE CASCADE,
          embed_model  text NOT NULL,
          embed_dim    int  NOT NULL CHECK (embed_dim > 0),
          content_hash text NOT NULL,
          embedded_at  timestamptz NOT NULL DEFAULT now(),
          embedding    vector(1024) NOT NULL,
          CONSTRAINT uq_message_embedding_msg_model UNIQUE (message_id, embed_model)
        );
        """
    )

    # HNSW index for approximate nearest-neighbour cosine search.
    # m=16, ef_construction=64 are conservative defaults; tune after retrieval eval.
    op.execute(
        """
        CREATE INDEX ix_message_embedding_hnsw
          ON message_embedding
          USING hnsw (embedding vector_cosine_ops)
          WITH (m = 16, ef_construction = 64);
        """
    )

    # Covering index for mailbox + model filtering (used by backfill and retrieval).
    op.execute(
        "CREATE INDEX ix_message_embedding_mailbox_model "
        "ON message_embedding (mailbox_id, embed_model);"
    )

    # Bump schema_meta to this migration's target version (spec 04 §9 DoD).
    op.execute(
        f"UPDATE schema_meta SET v = '{_SCHEMA_VERSION}' WHERE k = 'SCHEMA_VERSION';"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS message_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_message_subject_clean_fts;")
    op.execute("ALTER TABLE message DROP COLUMN IF EXISTS subject_clean_tsv;")
    op.execute(
        f"UPDATE schema_meta SET v = '{_PREV_SCHEMA_VERSION}' WHERE k = 'SCHEMA_VERSION';"
    )
