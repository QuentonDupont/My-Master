"""Google Workspace, read only: Gmail, Calendar and Drive.

There is no write class here on purpose. Every method is a GET; the twin's
rule is read and draft, and the way to keep a rule is to have no code that
breaks it (CLAUDE.md, invariant 2's spirit applied to the person's own mail).

Credentials come from .env, never from the repo:

    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN
        — an OAuth client of the person's own, authorised once in a browser
          with read-only scopes. The refresh token is exchanged for a short
          lived access token on each run.
    GOOGLE_ACCESS_TOKEN
        — or a ready access token, for a quick test.

Scopes the person should grant, and no wider:
    https://www.googleapis.com/auth/gmail.readonly
    https://www.googleapis.com/auth/calendar.readonly
    https://www.googleapis.com/auth/drive.readonly

    python -m core.google_client whoami
    python -m core.google_client mail --query "newer_than:1d"
    python -m core.google_client events --days 3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

from core import config, log

LOG = log.get("google")

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"


class GoogleError(RuntimeError):
    def __init__(self, what: str, detail: str) -> None:
        self.what, self.detail = what, detail
        super().__init__(f"{what}: {detail}")


class GoogleReadClient:
    """Read-only. No method here creates, sends, edits, shares or deletes."""

    def __init__(self, access_token: str | None = None, timeout: int = 30) -> None:
        self.timeout = timeout
        self._token = access_token if access_token is not None else config.env("GOOGLE_ACCESS_TOKEN")

    # -- auth ---------------------------------------------------------------
    def token(self) -> str:
        if self._token:
            return self._token
        client_id = config.env("GOOGLE_CLIENT_ID")
        secret = config.env("GOOGLE_CLIENT_SECRET")
        refresh = config.env("GOOGLE_REFRESH_TOKEN")
        if not (client_id and secret and refresh):
            raise GoogleError("auth", "GOOGLE_ACCESS_TOKEN or GOOGLE_CLIENT_ID + "
                              "GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN must be set")
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise GoogleError("auth", f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise GoogleError("auth", str(exc.reason)) from None
        self._token = payload.get("access_token") or ""
        if not self._token:
            raise GoogleError("auth", "no access_token in refresh response")
        return self._token

    def _get(self, url: str, params: dict | None = None) -> dict:
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")}, doseq=True)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token()}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise GoogleError(url.split("?")[0], f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise GoogleError(url.split("?")[0], str(exc.reason)) from None

    # -- gmail --------------------------------------------------------------
    def profile(self) -> dict:
        return self._get(f"{GMAIL}/profile")

    def messages(self, query: str, max_results: int = 25) -> list[dict]:
        """Message headers + snippet matching a Gmail search. Bodies are not
        fetched: the update needs who, what and when, not the whole thread."""
        page = self._get(f"{GMAIL}/messages",
                         {"q": query, "maxResults": max_results})
        out = []
        for ref in page.get("messages") or []:
            msg = self._get(f"{GMAIL}/messages/{ref['id']}",
                            {"format": "metadata",
                             "metadataHeaders": ["From", "Subject", "Date"]})
            headers = {h["name"]: h["value"]
                       for h in (msg.get("payload") or {}).get("headers") or []}
            out.append({
                "id": msg["id"], "thread_id": msg.get("threadId", ""),
                "from": headers.get("From", ""), "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""), "snippet": msg.get("snippet", ""),
                "internal_ms": int(msg.get("internalDate") or 0),
                "link": f"https://mail.google.com/mail/u/0/#all/{msg.get('threadId', msg['id'])}",
            })
        return out

    # -- calendar -----------------------------------------------------------
    def calendars(self) -> list[dict]:
        return self._get(f"{CALENDAR}/users/me/calendarList").get("items") or []

    def events(self, time_min: dt.datetime, time_max: dt.datetime,
               calendar_id: str = "primary", max_results: int = 50) -> list[dict]:
        page = self._get(
            f"{CALENDAR}/calendars/{urllib.parse.quote(calendar_id)}/events",
            {"timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(),
             "singleEvents": "true", "orderBy": "startTime",
             "maxResults": max_results})
        return page.get("items") or []

    # -- drive --------------------------------------------------------------
    def file(self, file_id: str) -> dict:
        return self._get(f"{DRIVE}/files/{file_id}",
                         {"fields": "id,name,mimeType,modifiedTime,webViewLink,"
                                    "lastModifyingUser(displayName)"})

    def modified_since(self, since: dt.datetime, folder_id: str = "",
                       max_results: int = 50) -> list[dict]:
        q = f"modifiedTime > '{since.astimezone(dt.timezone.utc).isoformat()}'"
        if folder_id:
            q += f" and '{folder_id}' in parents"
        page = self._get(f"{DRIVE}/files",
                         {"q": q, "orderBy": "modifiedTime desc",
                          "pageSize": max_results,
                          "fields": "files(id,name,mimeType,modifiedTime,"
                                    "webViewLink,lastModifyingUser(displayName))"})
        return page.get("files") or []


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - network path
    ap = argparse.ArgumentParser(description="Google read-only client.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    m = sub.add_parser("mail")
    m.add_argument("--query", default="newer_than:1d")
    e = sub.add_parser("events")
    e.add_argument("--days", type=int, default=3)
    d = sub.add_parser("drive")
    d.add_argument("--hours", type=int, default=24)
    args = ap.parse_args(argv)
    client = GoogleReadClient()
    now = dt.datetime.now().astimezone()
    if args.cmd == "whoami":
        print(json.dumps(client.profile(), indent=2))
    elif args.cmd == "mail":
        print(json.dumps(client.messages(args.query), indent=2))
    elif args.cmd == "events":
        print(json.dumps(client.events(now, now + dt.timedelta(days=args.days)), indent=2))
    elif args.cmd == "drive":
        print(json.dumps(client.modified_since(now - dt.timedelta(hours=args.hours)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
