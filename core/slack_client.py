"""Slack Web API wrapper. Read and write are two classes, as with Jira.

* `SlackReadClient` cannot post — it has no method that writes.
* `SlackWriteClient` refuses to do anything unless constructed with
  `execute=True`; the default is a dry run that logs what it would have said.
  Only `core.slack_execute` may construct it with execute=True.

Every write has its undo beside it (invariant 8). A deleted message was still
delivered, so a reply is never posted without human approval.

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


class SlackError(RuntimeError):
    def __init__(self, method: str, error: str, detail: dict | None = None) -> None:
        self.method, self.error, self.detail = method, error, detail or {}
        super().__init__(f"{method}: {error}")


class _Base:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token if token is not None else config.env("SLACK_BOT_TOKEN")
        self.timeout = timeout

    def _call(self, method: str, params: dict | None = None,
              body: dict | None = None) -> dict:
        if not self.token:
            raise SlackError(method, "SLACK_BOT_TOKEN is not set")
        url = f"{API}/{method}"
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise SlackError(method, str(getattr(exc, "reason", exc))) from None
        if not payload.get("ok"):
            raise SlackError(method, payload.get("error", "unknown"), payload)
        return payload


class SlackReadClient(_Base):
    """Read-only. Nothing here posts, edits or deletes."""

    def auth_test(self) -> dict:
        return self._call("auth.test")

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


class SlackWriteClient(_Base):
    """Every method is a no-op unless execute=True. Every method has an undo."""

    def __init__(self, *args, execute: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.execute = bool(execute)
        self.performed: list[dict] = []

    def _do(self, action: str, detail: dict, fn) -> dict:
        if not self.execute:
            LOG.info("slack.dry_run", action=action, **detail)
            return {"dry_run": True, "action": action, **detail}
        result = fn()
        self.performed.append({"action": action, **detail})
        LOG.info("slack.write", action=action, **detail)
        return result

    def post_reply(self, channel: str, thread_ts: str, text: str) -> dict:
        return self._do("post_reply",
                        {"channel": channel, "thread_ts": thread_ts,
                         "chars": len(text)},
                        lambda: self._call("chat.postMessage", body={
                            "channel": channel, "thread_ts": thread_ts,
                            "text": text, "unfurl_links": False}))

    def delete_message(self, channel: str, ts: str) -> dict:  # undo of post_reply
        return self._do("delete_message", {"channel": channel, "ts": ts},
                        lambda: self._call("chat.delete", body={
                            "channel": channel, "ts": ts}))

    def add_reaction(self, channel: str, ts: str, name: str) -> dict:
        return self._do("add_reaction", {"channel": channel, "ts": ts, "name": name},
                        lambda: self._call("reactions.add", body={
                            "channel": channel, "timestamp": ts, "name": name}))

    def remove_reaction(self, channel: str, ts: str, name: str) -> dict:  # undo
        return self._do("remove_reaction",
                        {"channel": channel, "ts": ts, "name": name},
                        lambda: self._call("reactions.remove", body={
                            "channel": channel, "timestamp": ts, "name": name}))


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
            print(json.dumps({k: me.get(k) for k in
                              ("team", "user", "bot_id", "user_id", "url")}, indent=2))
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
