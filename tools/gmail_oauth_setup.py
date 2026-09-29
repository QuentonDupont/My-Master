#!/usr/bin/env python3
"""One-time, interactive: mint a Gmail OAuth2 refresh token, no App Password
and no 2-Step Verification required. Run this yourself — it opens your own
browser for your own Google login, which is not something to hand to an
assistant.

Before running this, create an OAuth client (about 2 minutes, no 2FA
involved — this is unrelated to your account's 2-Step Verification setting):

  1. console.cloud.google.com/apis/library/gmail.googleapis.com
     -> pick or create a project -> Enable.
  2. console.cloud.google.com/apis/credentials/consent
     -> User Type "External" -> fill the required fields (app name, your
     email twice) -> Save and continue through Scopes (skip) and Test users
     -> add your own Gmail address as a test user -> Save.
     ("Testing" mode is fine for personal use. Test-mode tokens can expire
     after 7 days of inactivity, and the account itself never expires — if
     this setup script starts failing months from now, just run it again.)
  3. console.cloud.google.com/apis/credentials
     -> Create Credentials -> OAuth client ID -> Application type
     "Desktop app" -> Create.
  4. Copy the Client ID and Client Secret it shows you.

Then run:

    python3 tools/gmail_oauth_setup.py --client-id ... --client-secret ...

or put GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET in .env first and
run it with no flags. It opens your browser, you approve access to your own
account, and the refresh token it gets back is written straight to .env —
nothing to copy-paste.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://mail.google.com/"
PORT = 8765 + 17  # unlikely to collide with other local services


class _CodeCatcher(BaseHTTPRequestHandler):
    """Catches the one redirect Google sends back, then shuts down."""
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeCatcher.code = (qs.get("code") or [None])[0]
        _CodeCatcher.error = (qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Done — you can close this tab." if _CodeCatcher.code else \
              f"Google returned an error: {_CodeCatcher.error}"
        self.wfile.write(f"<html><body style='font:16px sans-serif;padding:2em'>"
                         f"{msg}</body></html>".encode("utf-8"))

    def log_message(self, fmt, *args):  # quiet
        pass


def _exchange(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
    }).encode("ascii")
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"\nToken exchange failed (HTTP {exc.code}): "
              f"{exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        raise SystemExit(1)


def _write_env(pairs: dict[str, str]) -> None:
    """Update .env in place: replace a KEY=... line if present, else append."""
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keys_left = dict(pairs)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in keys_left:
            lines[i] = f"{key}={keys_left.pop(key)}"
    for key, value in keys_left.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", default="")
    ap.add_argument("--client-secret", default="")
    ap.add_argument("--email", default="",
                    help="defaults to REPORT_EMAIL_FROM in .env")
    args = ap.parse_args()

    client_id = args.client_id or config.env("GMAIL_OAUTH_CLIENT_ID")
    client_secret = args.client_secret or config.env("GMAIL_OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("Need a Client ID and Client Secret — pass --client-id / "
              "--client-secret, or put GMAIL_OAUTH_CLIENT_ID / "
              "GMAIL_OAUTH_CLIENT_SECRET in .env first. See this file's "
              "module docstring for how to create them (no 2FA needed).",
              file=sys.stderr)
        return 1

    redirect_uri = f"http://localhost:{PORT}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent",
    })

    print(f"Opening your browser to authorise access to your own Gmail...\n"
         f"If it doesn't open, go to:\n  {url}\n")
    server = HTTPServer(("127.0.0.1", PORT), _CodeCatcher)
    webbrowser.open(url)
    print("Waiting for you to approve in the browser...")
    while _CodeCatcher.code is None and _CodeCatcher.error is None:
        server.handle_request()

    if _CodeCatcher.error:
        print(f"\nGoogle returned an error: {_CodeCatcher.error}", file=sys.stderr)
        return 1

    tokens = _exchange(client_id, client_secret, _CodeCatcher.code, redirect_uri)
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("\nNo refresh_token in the response. This usually means you've "
              "authorised this same OAuth client before — Google only issues "
              "a refresh token on first consent (or when you force it). Try: "
              "myaccount.google.com/permissions -> remove access for this "
              "app -> run this script again.", file=sys.stderr)
        return 1

    email = args.email or config.env("REPORT_EMAIL_FROM")
    to_write = {"GMAIL_OAUTH_CLIENT_ID": client_id,
               "GMAIL_OAUTH_CLIENT_SECRET": client_secret,
               "GMAIL_OAUTH_REFRESH_TOKEN": refresh}
    if email:
        to_write["REPORT_EMAIL_FROM"] = email
    _write_env(to_write)
    print(f"\nDone. Wrote GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET / "
         f"GMAIL_OAUTH_REFRESH_TOKEN to {ROOT / '.env'}.\n"
         f"Test it:  ./tools/install_daily_report.sh test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
