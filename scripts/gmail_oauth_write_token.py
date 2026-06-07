"""OAuth helper — obtain a gmail.insert token for generate_smoke_dataset.py.

Requests the gmail.insert scope (write-only: insert messages, no read/delete).
Same flow as gmail_oauth_token.py but with a different scope.

BEFORE RUNNING
--------------
1. Go to https://console.cloud.google.com and open your project.
2. Enable the Gmail API (if not already done).
3. Credentials → OAuth client ID → Desktop app → Download JSON.
   Save it as scripts/credentials.json (gitignored).
4. On first run, a browser window opens; log in with the throwaway Gmail account.

USAGE
-----
  python scripts/gmail_oauth_write_token.py
  python scripts/gmail_oauth_write_token.py --credentials path/to/credentials.json

OUTPUT
------
Prints the token JSON to stdout. Export it:

  export GMAIL_WRITE_TOKEN='<paste here>'

The token is printed to stdout and NEVER written to disk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/gmail.insert"
DEFAULT_CREDS = Path(__file__).resolve().parent / "credentials.json"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Obtain a Gmail OAuth token with gmail.insert scope."
    )
    p.add_argument(
        "--credentials",
        default=str(DEFAULT_CREDS),
        help="Path to the OAuth client secrets JSON (default: scripts/credentials.json).",
    )
    args = p.parse_args()

    creds_path = Path(args.credentials)
    if not creds_path.exists():
        sys.exit(
            f"ERROR: credentials file not found at {creds_path}\n"
            "Download from Google Cloud Console:\n"
            "  APIs & Services → Credentials → OAuth client ID → Desktop app → Download JSON\n"
            "Save as scripts/credentials.json (gitignored) and re-run."
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit(
            "ERROR: google-auth-oauthlib is not installed.\n"
            "Run: pip install -e .[gmail]"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), [SCOPE])
    creds = flow.run_local_server(port=0, open_browser=True)

    token_dict = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes),
    }

    token_json = json.dumps(token_dict, indent=2)
    one_liner  = json.dumps(token_dict)
    print("\n" + "=" * 60)
    print("Token obtained (gmail.insert scope). Export it:")
    print("=" * 60)
    print(token_json)
    print("=" * 60)
    print("\nbash / zsh:")
    print(f"  export GMAIL_WRITE_TOKEN='{one_liner}'")
    print("\nPowerShell (closing '@' must be at column 1 — do not indent):")
    print("$env:GMAIL_WRITE_TOKEN = @'")
    print(token_json)
    print("'@")
    print("\nNever commit this value.")


if __name__ == "__main__":
    main()
