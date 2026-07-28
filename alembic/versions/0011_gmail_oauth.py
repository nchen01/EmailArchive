"""Gmail OAuth + token vault — provider account + oauth state tables (S23).

@sprint S23 — implements docs/s20-oauth-token-vault-plan.md.

Adds two service-DB tables:
  - mailbox_provider_account : a connected Gmail account bound to a mailbox owner
    + tenant. Stores ONLY a vault_ref + safe provider metadata — never a raw
    access/refresh token (those live only in the token vault).
  - oauth_state : single-use CSRF/PKCE state bound to {tenant,user,mailbox,
    provider}, holding the ephemeral PKCE code_verifier (not an OAuth token).

Service-DB only — no ekc_schemas shared contract change, so SCHEMA_VERSION is NOT
bumped. Purely additive; safe on existing data.

Revision ID: 0011_gmail_oauth
Revises: 0010_auth_tenant
Create Date: 2026-07-28
"""
from alembic import op

revision = "0011_gmail_oauth"
down_revision = "0010_auth_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mailbox_provider_account (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              uuid NOT NULL REFERENCES tenant(id),
            owner_user_id          uuid NOT NULL REFERENCES app_user(id),
            mailbox_id             uuid NOT NULL REFERENCES mailbox(id),
            provider               text NOT NULL DEFAULT 'gmail',
            provider_account_email text NOT NULL,
            provider_account_sub   text,
            vault_ref              text,
            scopes_granted         text[] NOT NULL DEFAULT '{}'::text[],
            status                 text NOT NULL DEFAULT 'connected',
            connected_at           timestamptz NOT NULL DEFAULT now(),
            last_verified_at       timestamptz,
            disconnected_at        timestamptz,
            expected_account_email text,
            mismatch_reason        text,
            CONSTRAINT uq_provider_account_mailbox UNIQUE (tenant_id, mailbox_id, provider),
            CONSTRAINT ck_provider_account_provider CHECK (provider IN ('gmail')),
            CONSTRAINT ck_provider_account_status CHECK (
                status IN ('connected','refresh_failed','revoked','disconnected','mismatch_blocked')
            )
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_state (
            id            text PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            user_id       uuid NOT NULL,
            mailbox_id    uuid NOT NULL,
            provider      text NOT NULL DEFAULT 'gmail',
            code_verifier text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL,
            consumed_at   timestamptz
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oauth_state;")
    op.execute("DROP TABLE IF EXISTS mailbox_provider_account;")
