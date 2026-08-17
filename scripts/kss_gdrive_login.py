#!/usr/bin/env python3
"""One-shot Google Drive login for the 20:00 left-scan job.

Writes $KSS_STATE_ROOT/secrets/gdrive_oauth.json (0600).
Requires a Desktop OAuth client JSON at --client-secrets or
$KSS_STATE_ROOT/secrets/gdrive_client.json.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorize Drive read access for left-scan ingest")
    parser.add_argument(
        "--state-root",
        default=os.environ.get("KSS_STATE_ROOT")
        or str(Path.home() / "Library" / "Application Support" / "KSS"),
    )
    parser.add_argument("--client-secrets", help="Google Desktop OAuth client JSON")
    args = parser.parse_args(argv)
    state_root = Path(args.state_root).expanduser()
    secrets_dir = state_root / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    token_path = secrets_dir / "gdrive_oauth.json"
    client_path = Path(args.client_secrets).expanduser() if args.client_secrets else secrets_dir / "gdrive_client.json"
    if not client_path.is_file():
        print(
            "missing Desktop OAuth client JSON.\n"
            f"place it at {client_path} or pass --client-secrets.\n"
            "alternative: set KSS_LEFT_SCAN_DIR to a locally synced 「左侧机会扫描」 folder.",
            file=sys.stderr,
        )
        return 2
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("google-auth-oauthlib is not installed in this interpreter", file=sys.stderr)
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    payload = {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "access_token": creds.token,
    }
    token_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"wrote {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
