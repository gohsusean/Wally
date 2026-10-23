#!/usr/bin/env python3
"""Repair existing Google OAuth through local browser consent; never print tokens."""

import argparse

from wally.adapters.google.setup import repair_oauth
from wally.config.loader import find_project_root, load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    print("Opening Google consent in your local browser. Authorize Wally there.", flush=True)
    try:
        root = find_project_root()
        repair_oauth(load_settings(project_root=root), root / ".env", port=args.port)
    except Exception as exc:
        # OAuth exceptions can contain authorization codes and token responses.
        print(f"OAuth repair did not complete ({type(exc).__name__}); no tokens displayed.")
        return 1
    print("Google refresh token saved privately. No email was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
