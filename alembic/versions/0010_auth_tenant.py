"""auth + tenant boundary — tenant/user/membership + mailbox ownership (S22).

@sprint S22 — first implementation slice of docs/s19-auth-tenant-boundary-plan.md.

Adds the production identity/tenant/authorization foundation:
  - tenant            : the isolation boundary (one customer org)
  - app_user          : an authenticated identity within one tenant
  - tenant_membership : role grants (creator / admin / security_reviewer)
  - mailbox.tenant_id, mailbox.owner_user_id : mailbox ownership binding

Both mailbox columns are NULLABLE so existing ingest/seed inserts keep working.
This migration backfills a deterministic LOCAL DEV tenant + user (fixed UUIDs that
match services/api/auth.py) and assigns every existing mailbox to them, so the
localhost demo (puluo + seeded handoff-demo) keeps working under AUTH_MODE=dev.
No production data exists yet, so there is no production backfill.

Service-DB only — no ekc_schemas shared contract changes, so SCHEMA_VERSION is
NOT bumped.

Revision ID: 0010_auth_tenant
Revises: 0009_recipient_code_consumed
Create Date: 2026-07-28
"""
from alembic import op

revision = "0010_auth_tenant"
down_revision = "0009_recipient_code_consumed"
branch_labels = None
depends_on = None

# Fixed local dev identity — MUST match services/api/auth.py DEV_TENANT_ID/DEV_USER_ID.
DEV_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEV_USER_ID = "22222222-2222-2222-2222-222222222222"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name       text NOT NULL,
            status     text NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_user (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenant(id),
            idp_subject text NOT NULL,
            email       text NOT NULL,
            status      text NOT NULL DEFAULT 'active',
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_user_tenant_subject UNIQUE (tenant_id, idp_subject)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_membership (
            user_id    uuid NOT NULL REFERENCES app_user(id),
            role       text NOT NULL,
            granted_by uuid,
            granted_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, role),
            CONSTRAINT ck_tenant_membership_role
                CHECK (role IN ('creator','admin','security_reviewer'))
        );
        """
    )

    op.execute("ALTER TABLE mailbox ADD COLUMN IF NOT EXISTS tenant_id uuid;")
    op.execute("ALTER TABLE mailbox ADD COLUMN IF NOT EXISTS owner_user_id uuid;")

    # Deterministic local dev tenant/user backfill so existing mailboxes stay
    # accessible under AUTH_MODE=dev. Idempotent.
    op.execute(
        f"""
        INSERT INTO tenant (id, name, status)
        VALUES ('{DEV_TENANT_ID}', 'Local Dev Tenant', 'active')
        ON CONFLICT (id) DO NOTHING;
        """
    )
    op.execute(
        f"""
        INSERT INTO app_user (id, tenant_id, idp_subject, email, status)
        VALUES ('{DEV_USER_ID}', '{DEV_TENANT_ID}', 'dev-local', 'dev@localhost', 'active')
        ON CONFLICT (id) DO NOTHING;
        """
    )
    op.execute(
        f"""
        INSERT INTO tenant_membership (user_id, role)
        VALUES ('{DEV_USER_ID}', 'creator'), ('{DEV_USER_ID}', 'admin')
        ON CONFLICT (user_id, role) DO NOTHING;
        """
    )
    op.execute(
        f"""
        UPDATE mailbox
           SET tenant_id = '{DEV_TENANT_ID}', owner_user_id = '{DEV_USER_ID}'
         WHERE tenant_id IS NULL OR owner_user_id IS NULL;
        """
    )

    # Optional FKs (added after backfill so they validate cleanly).
    op.execute(
        "ALTER TABLE mailbox "
        "ADD CONSTRAINT fk_mailbox_tenant FOREIGN KEY (tenant_id) REFERENCES tenant(id);"
    )
    op.execute(
        "ALTER TABLE mailbox "
        "ADD CONSTRAINT fk_mailbox_owner_user FOREIGN KEY (owner_user_id) REFERENCES app_user(id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mailbox DROP CONSTRAINT IF EXISTS fk_mailbox_owner_user;")
    op.execute("ALTER TABLE mailbox DROP CONSTRAINT IF EXISTS fk_mailbox_tenant;")
    op.execute("ALTER TABLE mailbox DROP COLUMN IF EXISTS owner_user_id;")
    op.execute("ALTER TABLE mailbox DROP COLUMN IF EXISTS tenant_id;")
    op.execute("DROP TABLE IF EXISTS tenant_membership;")
    op.execute("DROP TABLE IF EXISTS app_user;")
    op.execute("DROP TABLE IF EXISTS tenant;")
