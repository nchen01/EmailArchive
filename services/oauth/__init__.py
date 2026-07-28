"""Gmail OAuth + token-vault seam (S23 — implements docs/s20-oauth-token-vault-plan.md).

The app DB stores only a ``vault_ref`` + safe provider metadata; raw refresh/access
tokens live only inside the token vault (services/oauth/vault.py) and never touch
the DB, logs, audit metadata, API responses, or frontend state.
"""
