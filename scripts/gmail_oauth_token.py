"""OAuth helper — obtain a gmail.readonly token for gmail_smoke_ingest.py.

Based on the Gmail Python quickstart:
https://developers.google.com/gmail/api/quickstart/python

BEFORE RUNNING
--------------
1. Go to https://console.cloud.google.com and create (or reuse) a project.
2. Enable the Gmail API.
3. Create OAuth credentials: APIs & Services → Credentials → Create →
   OAuth client ID → Desktop app. Download the JSON and save it as
   credentials.json in this directory (it is gitignored).
4. On first run a browser window opens; log in with the Gmail account you
   want to test against.

USAGE
-----
  python scripts/gmail_oauth_token.py
  python scripts/gmail_oauth_token.py --credentials path/to/credentials.json

OUTPUT
------
Prints the token JSON to stdout in the exact shape expected by
gmail_smoke_ingest.py.  Copy-paste it into the GMAIL_TOKEN env var:

  export GMAIL_TOKEN='<paste here>'

Or export per-mailbox after you know the DB UUID:

  export GMAIL_TOKEN_<uuid>='<paste here>'

The token is printed to stdout and NEVER written to disk by this script.
credentials.json and token.json are both gitignored.

SCOPE
-----
Only https://www.googleapis.com/auth/gmail.readonly is requested.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_CREDS = Path(__file__).resolve().parent / "credentials.json"


def main() -> None:
    p = argparse.ArgumentParser(description="Obtain a Gmail OAuth token for smoke ingest.")
    p.add_argument(
        "--credentials",
        default=str(DEFAULT_CREDS),
        help="Path to the OAuth client secrets JSON downloaded from Google Cloud Console "
             "(default: scripts/credentials.json).",
    )
    args = p.parse_args()

    creds_path = Path(args.credentials)
    if not creds_path.exists():
        sys.exit(
            f"ERROR: credentials file not found at {creds_path}\n"
            "Download it from Google Cloud Console:\n"
            "  APIs & Services → Credentials → OAuth client ID → Desktop app → Download JSON\n"
            "Save it as credentials.json (gitignored) and re-run this script."
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit(
            "ERROR: google-auth-oauthlib is not installed.\n"
            "Run: pip install -e .[gmail]"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
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

    print("\n" + "=" * 60)
    print("OAuth token obtained. Copy the JSON below into GMAIL_TOKEN:")
    print("=" * 60)
    print(token_json)
    print("=" * 60)
    print("\nExample:")
    print(f"  export GMAIL_TOKEN='{json.dumps(token_dict)}'")
    print("\nNever commit this value. credentials.json and token.json are gitignored.")


if __name__ == "__main__":
    main()
