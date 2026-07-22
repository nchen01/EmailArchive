"""handoff recipient + recipient session — publish/recipient foundation (S17.5).

@sprint S17 — ticket S17.5.

Adds the first recipient-side tables needed to publish a generated package to one
coverage recipient and grant safe, package-local access:

- handoff_recipient          — exactly ONE per package (unique package_id). The
                               recipient authenticates via a one-time capability
                               CODE; the server stores only its sha256 hash
                               (`capability_code_hash`), never the raw code. The
                               raw code lives only in the share-link URL fragment.
- handoff_recipient_session  — short-lived, package-scoped session issued when the
                               recipient exchanges the capability code. Only the
                               sha256 hash of the bearer token is stored
                               (`session_token_hash`, unique).

No raw capability token or session token is ever persisted — only hashes — so a
leaked DB row cannot be replayed as a recipient.

Still deferred (S17.6+): recipient "ask" (package-scoped cover-for-me), new-version
supersede flow, manager approval, multi-recipient, and any recipient/publish UI.

These are API-layer product objects (not ekc_schemas pipeline contracts), so
SCHEMA_VERSION is deliberately NOT bumped by this migration.

Revision ID: 0008_handoff_recipient
Revises: 0007_handoff_package_tables
Create Date: 2026-07-16
"""
from alembic import op

revision = "0008_handoff_recipient"
down_revision = "0007_handoff_package_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One recipient per package (MVP): unique package_id. capability_code_hash is
    # the lookup key on session exchange, so it is unique + indexed.
    op.execute(
        """
        CREATE TABLE handoff_recipient (
          id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          package_id            uuid NOT NULL UNIQUE
                                  REFERENCES handoff_package(id) ON DELETE CASCADE,
          recipient_email       text NOT NULL,
          recipient_person_id   uuid REFERENCES person(id) ON DELETE SET NULL,
          capability_code_hash  text NOT NULL UNIQUE,
          granted_at            timestamptz NOT NULL DEFAULT now(),
          expires_at            timestamptz NOT NULL,
          revoked_at            timestamptz,
          last_accessed_at      timestamptz
        );
        """
    )

    # Short-lived bearer sessions. Only the token hash is stored (unique lookup
    # key). expires_at is the session's own (short) lifetime, always <= the
    # package/recipient expiry; access control re-checks the package at request
    # time regardless of this row.
    op.execute(
        """
        CREATE TABLE handoff_recipient_session (
          id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          package_id         uuid NOT NULL REFERENCES handoff_package(id) ON DELETE CASCADE,
          recipient_id       uuid NOT NULL REFERENCES handoff_recipient(id) ON DELETE CASCADE,
          session_token_hash text NOT NULL UNIQUE,
          issued_at          timestamptz NOT NULL DEFAULT now(),
          expires_at         timestamptz NOT NULL,
          revoked_at         timestamptz
        );
        """
    )
    op.execute("CREATE INDEX ix_handoff_recipient_session_package ON handoff_recipient_session (package_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS handoff_recipient_session;")
    op.execute("DROP TABLE IF EXISTS handoff_recipient;")
