#!/usr/bin/env python3
"""One-time Google OAuth setup for Wally communications (v0.6).

Creates OAuth credentials in Google Cloud Console (Desktop app), then runs
the local consent flow and prints a refresh token for .env.

Usage:
  1. Create a project at https://console.cloud.google.com/
  2. Enable Gmail API and Google Calendar API
  3. Create OAuth 2.0 Client ID (Desktop app) — download client JSON or copy id/secret
  4. uv run python scripts/google_auth.py --client-id ... --client-secret ...
  5. Add printed GOOGLE_REFRESH_TOKEN to .env
"""

from __future__ import annotations

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow
from wally.adapters.google.auth import DEFAULT_SCOPES


def main() -> int:
    parser = argparse.ArgumentParser(description="Obtain Google OAuth refresh token for Wally")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    client_config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=list(DEFAULT_SCOPES))
    creds = flow.run_local_server(port=args.port)

    print("\nAdd these to your .env:\n")
    print(f"GOOGLE_CLIENT_ID={args.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={args.client_secret}")
    if creds.refresh_token:
        print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    else:
        print("# No refresh token returned — revoke prior access and retry with prompt=consent")
    print("\nThen set providers.communications.enabled: true in config/macbook.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
