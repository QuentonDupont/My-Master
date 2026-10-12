"""Slack Web API wrapper — READ ONLY.

Kept for the Digital Twin, which reads the channels allowed in
config/twin.yml. The Slack Leader, its write client and its poller were
removed on 1 Oct 2026 ("it will be handled elsewhere"); on 12 Oct 2026 the
board owner chose to keep this read client for the Twin. There is no write
client in this repo: `SlackReadClient` has no method that posts, and
tests/test_slack_read_only.py asserts that stays true.

Needs SLACK_BOT_TOKEN (xoxb-). The app-level token (xapp-) authenticates the app
for Socket Mode and cannot read or post: Slack answers `not_allowed_token_type`.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

from core import config, log

LOG = log.get("slack")

API = "https://slack.com/api"

#: Every Slack Web API method this repo may call. All are reads. Anything else
#: — chat.postMessage, chat.delete, reactions.add, ... — is refused before a
#: request is built.
READ_METHODS = frozenset({
    "auth.test", "users.conversations", "conversations.history",
    "conversations.replies", "users.info", "conversations.info",
    "chat.getPermalink",
})


class SlackError(RuntimeError):
    def __init__(self, method: str, error: str, detail: dict | None = None) -> None:
        self.method, self.error, self.detail = method, error, detail or {}
        super().__init__(f"{method}: {error}")


class _Base:
    #: which env var this class takes its token from
    TOKEN_ENV = "SLACK_BOT_TOKEN"

    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token if token is not None else config.env(self.TOKEN_ENV)
        self.timeout = timeout

    def _call(self, method: str, params: dict | None = None) -> dict:
        if method not in READ_METHODS:
            raise SlackError(method, "refused: this repo only reads Slack")
        if not self.token:
            raise SlackError(method, "SLACK_BOT_TOKEN is not set")
        url = f"{API}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise SlackError(method, str(getattr(exc, "reason", exc))) from None
        if not payload.get("ok"):
            raise SlackError(method, payload.get("error", "unknown"), payload)
        return payload


class SlackReadClient(_Base):
    """Read-only. Nothing here posts, edits or deletes.

    Prefers `SLACK_USER_TOKEN` (xoxp-) when one is set, falling back to the bot
    token. A user token reads as the person who authorised it: their channels and
    their DMs, and `auth_test()` returns *their* user id — which is what makes
    "someone tagged me" visible at all. A bot can only ever see channels it was
    invited to and can never see a person's DMs.

    There is no posting in this repo at all: `_call` refuses any API method
    not in READ_METHODS.
    """

    TOKEN_ENV = "SLACK_USER_TOKEN"

    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        if token is None:
            token = config.env("SLACK_USER_TOKEN") or config.env("SLACK_BOT_TOKEN")
        super().__init__(token, timeout)

    @property
    def acting_as_user(self) -> bool:
        return (self.token or "").startswith("xoxp-")

    def auth_test(self) -> dict:
        return self._call("auth.test")

    def conversations(self,
                      types: str = "public_channel,private_channel,im,mpim",
                      limit: int = 200) -> list[dict]:
        """Conversations the token can see.

        DMs (`im`) and group DMs (`mpim`) are included: with a user token those
        are where most things addressed to a person actually arrive.
        """
        out: list[dict] = []
        cursor = None
        while True:
            params = {"types": types, "limit": limit, "exclude_archived": "true"}
            if cursor:
                params["cursor"] = cursor
            page = self._call("users.conversations", params=params)
            out.extend(page.get("channels", []))
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return out

    def history(self, channel: str, oldest: str | None = None,
                limit: int = 50) -> list[dict]:
        """Recent messages in one channel, oldest first.

        `oldest` is exclusive on Slack's side, so a stored cursor is never
        re-delivered. One page only: the poller runs often, and a channel that
        outruns the page between sweeps is caught by the ledger, not by paging
        back through history.
        """
        params: dict = {"channel": channel, "limit": limit, "inclusive": "false"}
        if oldest:
            params["oldest"] = oldest
        page = self._call("conversations.history", params=params)
        return sorted(page.get("messages", []),
                      key=lambda m: float(m.get("ts", 0)))

    def thread(self, channel: str, thread_ts: str, limit: int = 100) -> list[dict]:
        """Every message in one thread, oldest first."""
        out: list[dict] = []
        cursor = None
        while True:
            params = {"channel": channel, "ts": thread_ts, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            page = self._call("conversations.replies", params=params)
            out.extend(page.get("messages", []))
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return out

    def user(self, user_id: str) -> dict:
        return self._call("users.info", params={"user": user_id}).get("user", {})

    def channel(self, channel_id: str) -> dict:
        return self._call("conversations.info",
                          params={"channel": channel_id}).get("channel", {})

    def permalink(self, channel: str, ts: str) -> str:
        return self._call("chat.getPermalink",
                          params={"channel": channel, "message_ts": ts}).get(
                              "permalink", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.slack_client",
                                 description="read-only CLI for checking Slack access")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    p_t = sub.add_parser("thread")
    p_t.add_argument("channel")
    p_t.add_argument("ts")
    args = ap.parse_args(argv)

    client = SlackReadClient()
    try:
        if args.cmd == "whoami":
            me = client.auth_test()
            out = {k: me.get(k) for k in
                   ("team", "user", "bot_id", "user_id", "url")}
            out["token"] = "SLACK_USER_TOKEN (acting as the person)" \
                if client.acting_as_user else "SLACK_BOT_TOKEN (acting as the app)"
            print(json.dumps(out, indent=2))
        else:
            for msg in client.thread(args.channel, args.ts):
                print(f"{msg.get('ts')}  {msg.get('user') or msg.get('bot_id')}: "
                      f"{(msg.get('text') or '')[:120]}")
    except SlackError as exc:
        print(f"SLACK ERROR {exc.error}")
        if exc.error == "not_allowed_token_type":
            print("  that is an app-level token (xapp-); this needs the bot token (xoxb-)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
