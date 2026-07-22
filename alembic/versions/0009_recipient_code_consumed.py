"""handoff recipient one-time capability code — consumed marker (S17.5).

@sprint S17 — ticket S17.5 (pre-merge blocker fix).

S17.2 §7.1 requires the recipient capability code to be truly one-time:
"one-time code is consumed/rotated on exchange." The original 0008 table stored
only the code hash but left it reusable, so the same raw share code could be
exchanged for a fresh session repeatedly until package expiry/revocation.

This migration adds a nullable `capability_code_consumed_at timestamptz` to
`handoff_recipient`. The session-exchange endpoint atomically claims the code by
setting this column (conditional UPDATE guarded on it being NULL); a second
exchange matches zero rows and returns the same neutral "unavailable" response as
an invalid/expired/revoked code. Only hashes remain stored — no raw code.

Separate additive migration (rather than editing 0008) so it applies cleanly to
databases that already ran 0008. No pipeline contract changes, so SCHEMA_VERSION
is not bumped.

Revision ID: 0009_recipient_code_consumed
Revises: 0008_handoff_recipient
Create Date: 2026-07-21
"""
from alembic import op

revision = "0009_recipient_code_consumed"
down_revision = "0008_handoff_recipient"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: NULL means "code not yet exchanged". Set once, atomically, on the
    # first successful session exchange. Existing rows (pre-fix grants) default to
    # NULL and remain exchangeable exactly once.
    op.execute(
        "ALTER TABLE handoff_recipient "
        "ADD COLUMN capability_code_consumed_at timestamptz;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE handoff_recipient DROP COLUMN IF EXISTS capability_code_consumed_at;")
