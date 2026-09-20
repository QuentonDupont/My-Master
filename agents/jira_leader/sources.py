"""Where tickets come from. Read-only by construction.

`JiraTicketSource` is the real one. `FileTicketSource` reads a jsonl of issues so
the whole pipeline can run offline against tests/fixtures — same shape, no token.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import config, log
from core.jira_client import JiraReadClient

LOG = log.get("source")

FIELDS = ["summary", "description", "status", "created", "updated", "reporter",
          "assignee", "labels", "components", "priority", "issuetype", "comment",
          "attachment"]


class TicketSource:
    def open_tickets(self) -> list[dict]:
        raise NotImplementedError

    def get(self, key: str) -> dict:
        raise NotImplementedError


class JiraTicketSource(TicketSource):
    def __init__(self, client: JiraReadClient | None = None) -> None:
        self.client = client or JiraReadClient()

    def open_tickets(self) -> list[dict]:
        intake = config.boards()["intake"]
        extra = intake.get("requester_email_field")
        jql = (f'project = {intake["project"]} AND status = "{intake["open_status"]}" '
               f"ORDER BY created ASC")
        fields = FIELDS + ([extra] if extra else [])
        issues = self.client.search(jql, fields=fields)
        LOG.info("source.jira", count=len(issues))
        return issues

    def get(self, key: str) -> dict:
        return self.client.issue(key)


class KeyTicketSource(TicketSource):
    """Named tickets, whatever lane they are sitting in.

    The intake source only sees `open_status`, which is right for the standing
    poll: a ticket someone already moved on is not new work. But it means a
    ticket parked in Blocked can never be looked at again, and PESD1 has eleven
    of those, the oldest untouched for 454 days — several needing an answer
    rather than a developer.

    Scope is unchanged: keys outside allowed_projects are refused here, not
    filtered quietly, because asking for one is a mistake worth seeing.
    """

    def __init__(self, keys: list[str], client: JiraReadClient | None = None) -> None:
        allowed = set(config.allowed_projects())
        self.keys = [k.strip().upper() for k in keys if k.strip()]
        bad = [k for k in self.keys if k.split("-")[0] not in allowed]
        if bad:
            raise ValueError(
                f"out of scope: {', '.join(bad)} — boards.yml allows "
                f"{', '.join(sorted(allowed))}")
        self.client = client or JiraReadClient()

    def open_tickets(self) -> list[dict]:
        issues = []
        for key in self.keys:
            try:
                issues.append(self.client.issue(key))
            except Exception as exc:
                LOG.warn("source.key_failed", ticket=key, error=str(exc)[:120])
        LOG.info("source.keys", requested=len(self.keys), found=len(issues))
        return issues

    def get(self, key: str) -> dict:
        return self.client.issue(key)


class FileTicketSource(TicketSource):
    """Offline source. Also used by the tests."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _load(self) -> list[dict]:
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def open_tickets(self) -> list[dict]:
        open_status = config.boards()["intake"]["open_status"]
        issues = [i for i in self._load()
                  if ((i.get("fields", {}).get("status") or {}).get("name")
                      == open_status)]
        LOG.info("source.file", path=str(self.path), count=len(issues))
        return issues

    def get(self, key: str) -> dict:
        for issue in self._load():
            if issue["key"] == key:
                return issue
        raise KeyError(key)


def comment_count(issue: dict) -> int:
    return len(((issue.get("fields", {}) or {}).get("comment", {}) or {})
               .get("comments", []) or [])
