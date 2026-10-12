"""Refresh a Gmail OAuth2 access token. Stdlib only — no google-auth dependency.

The whole exchange is one HTTPS POST:
    https://oauth2.googleapis.com/token
    client_id + client_secret + refresh_token + grant_type=refresh_token
    -> {"access_token": "...", "expires_in": 3599, ...}

The refresh token itself is minted once, interactively, by
`tools/gmail_oauth_setup.py` — this module only ever exchanges it for a
short-lived access token, which is what SMTP XOAUTH2 actually needs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthError(RuntimeError):
    pass


def refresh_access_token(client_id: str, client_secret: str,
                         refresh_token: str, timeout: int = 20) -> str:
    """Exchange a stored refresh token for a fresh access token."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("ascii")
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise OAuthError(
            f"token refresh failed (HTTP {exc.code}): {detail[:300]}. "
            f"A refresh token stops working if access was revoked in "
            f"myaccount.google.com/permissions, or if the OAuth consent "
            f"screen is in Testing mode and its 7-day test window lapsed — "
            f"re-run tools/gmail_oauth_setup.py if so."
        ) from None
    except urllib.error.URLError as exc:
        raise OAuthError(f"could not reach {TOKEN_URL}: {exc.reason}") from None

    token = body.get("access_token")
    if not token:
        raise OAuthError(f"no access_token in response: {body}")
    return token


def xoauth2_string(email: str, access_token: str) -> str:
    """The raw (pre-base64) SASL string SMTP XOAUTH2 expects."""
    return f"user={email}\1auth=Bearer {access_token}\1\1"
