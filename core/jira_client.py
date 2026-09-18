"""Thin Jira REST wrapper. Read and write are two classes on purpose.

* `JiraReadClient` cannot write — it has no method that issues POST/PUT/DELETE.
* `JiraWriteClient` refuses to do anything unless constructed with
  `execute=True`; the default is a dry run that logs what it *would* have done.
  Only `core.execute` may construct it with execute=True.

Both refuse any project outside `config/boards.yml: allowed_projects`.
Jira Cloud REST v2 is used so bodies are plain text rather than ADF.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from core import config, log

LOG = log.get("jira")

PROJECT_IN_JQL = re.compile(r"project\s*(?:=|in)\s*(\([^)]*\)|[A-Za-z0-9_\"']+)", re.I)
KEY_RE = re.compile(r"^([A-Z][A-Z0-9]+)-\d+$")

DEFAULT_SEARCH_FIELDS = ["summary", "status", "created", "updated", "reporter",
                         "assignee", "labels", "components", "priority", "issuetype",
                         "resolution"]


class JiraError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status, self.url, self.body = status, url, body
        super().__init__(f"HTTP {status} for {url}: {body[:400]}")


class ScopeError(RuntimeError):
    """Raised when something outside PESD1/PRDT is touched."""


def assert_key_allowed(key: str) -> str:
    m = KEY_RE.match(key or "")
    if not m:
        raise ScopeError(f"{key!r} is not a Jira issue key")
    project = m.group(1)
    if project not in config.allowed_projects():
        raise ScopeError(f"{project} is outside allowed_projects {config.allowed_projects()}")
    return key


def assert_project_allowed(project: str) -> str:
    if project not in config.allowed_projects():
        raise ScopeError(f"{project} is outside allowed_projects {config.allowed_projects()}")
    return project


def assert_jql_scoped(jql: str) -> str:
    """Every query must name a project, and only allowed ones."""
    found = PROJECT_IN_JQL.findall(jql or "")
    if not found:
        raise ScopeError("JQL must constrain `project` to an allowed board")
    for group in found:
        for token in re.split(r"[(),\s]+", group):
            token = token.strip("\"' ")
            if token and token not in config.allowed_projects():
                raise ScopeError(f"JQL names project {token!r}, outside allowed_projects")
    return jql


class _Base:
    def __init__(self, base_url: str | None = None, email: str | None = None,
                 token: str | None = None, timeout: int = 30) -> None:
        self.base_url = (base_url or config.base_url()).rstrip("/")
        self.email = email if email is not None else config.env("JIRA_EMAIL")
        self.token = token if token is not None else config.env("JIRA_API_TOKEN")
        self.timeout = timeout

    def _auth_header(self) -> str:
        raw = f"{self.email}:{self.token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None) -> Any:
        if not self.email or not self.token:
            raise RuntimeError(
                "JIRA_EMAIL / JIRA_API_TOKEN missing — copy config/.env.example to .env"
            )
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth_header())
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", "replace")
            LOG.error("jira.http_error", method=method, path=path, status=exc.code,
                      detail=detail[:500])
            raise JiraError(exc.code, url, detail) from None
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise JiraError(0, url, str(exc.reason)) from None


class JiraReadClient(_Base):
    """Read-only. No method here mutates anything."""

    def myself(self) -> dict:
        return self._request("GET", "/rest/api/2/myself")

    def fields(self) -> list[dict]:
        return self._request("GET", "/rest/api/2/field")

    def issue(self, key: str, fields: str = "*all") -> dict:
        assert_key_allowed(key)
        return self._request("GET", f"/rest/api/2/issue/{key}",
                             params={"fields": fields, "expand": "renderedFields"})

    def comments(self, key: str) -> list[dict]:
        assert_key_allowed(key)
        out, start = [], 0
        while True:
            page = self._request("GET", f"/rest/api/2/issue/{key}/comment",
                                 params={"startAt": start, "maxResults": 100})
            out.extend(page.get("comments", []))
            start += page.get("maxResults", 0) or 0
            if start >= page.get("total", 0):
                return out

    def transitions(self, key: str) -> list[dict]:
        assert_key_allowed(key)
        return self._request("GET", f"/rest/api/2/issue/{key}/transitions").get(
            "transitions", [])

    def search(self, jql: str, fields: list[str] | None = None,
               max_results: int = 100, limit: int | None = None) -> list[dict]:
        """Search via /search/jql.

        The old /rest/api/2/search was removed by Atlassian (HTTP 410). The
        replacement pages with an opaque `nextPageToken` and returns no total,
        so this loops until `isLast` rather than counting up to one.
        """
        assert_jql_scoped(jql)
        issues: list[dict] = []
        token: str | None = None
        while True:
            body: dict = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields or DEFAULT_SEARCH_FIELDS,
            }
            if token:
                body["nextPageToken"] = token
            page = self._request("POST", "/rest/api/2/search/jql", body=body)
            batch = page.get("issues", [])
            issues.extend(batch)
            token = page.get("nextPageToken")
            if page.get("isLast") or not token or not batch:
                break
            if limit and len(issues) >= limit:
                break
        LOG.info("jira.search", jql=jql, returned=len(issues))
        return issues[:limit] if limit else issues

    def iter_search(self, jql: str, fields: list[str] | None = None) -> Iterator[dict]:
        yield from self.search(jql, fields=fields)

    def issue_types(self, project: str) -> list[dict]:
        """Issue types available in a project — use this to set boards.yml."""
        assert_project_allowed(project)
        meta = self._request("GET", "/rest/api/2/issue/createmeta",
                             params={"projectKeys": project, "expand": "projects.issuetypes"})
        for proj in meta.get("projects", []):
            if proj.get("key") == project:
                return proj.get("issuetypes", [])
        return []

    def issue_link_types(self) -> list[dict]:
        return self._request("GET", "/rest/api/2/issueLinkType").get("issueLinkTypes", [])

    def my_permissions(self, project: str, permissions: list[str]) -> dict:
        """What this token may actually do on a project. Read-only."""
        assert_project_allowed(project)
        data = self._request("GET", "/rest/api/2/mypermissions",
                             params={"projectKey": project,
                                     "permissions": ",".join(permissions)})
        return {name: bool(spec.get("havePermission"))
                for name, spec in (data.get("permissions") or {}).items()}

    def board_configuration(self, board_id: int | str) -> dict:
        """Agile board config. Refuses a board that is not on an allowed project."""
        data = self._request("GET", f"/rest/agile/1.0/board/{board_id}/configuration")
        location_key = ((data.get("location") or {}).get("projectKey")
                        or (data.get("location") or {}).get("key"))
        if location_key and location_key not in config.allowed_projects():
            raise ScopeError(f"board {board_id} belongs to {location_key}, "
                             f"outside allowed_projects")
        return data

    def required_fields(self, project: str, issue_type: str) -> dict:
        """Fields the project demands on create, with their allowed values."""
        assert_project_allowed(project)
        meta = self._request("GET", "/rest/api/2/issue/createmeta",
                             params={"projectKeys": project,
                                     "issuetypeNames": issue_type,
                                     "expand": "projects.issuetypes.fields"})
        for proj in meta.get("projects", []):
            for it in proj.get("issuetypes", []):
                if it.get("name") == issue_type:
                    return {fid: f for fid, f in (it.get("fields") or {}).items()
                            if f.get("required") and not f.get("hasDefaultValue")
                            and fid not in ("project", "issuetype", "summary",
                                            "description", "reporter")}
        return {}

    def board_issues(self, board_id: int | str, jql: str = "",
                     fields: list[str] | None = None,
                     limit: int | None = None) -> list[dict]:
        """Issues on an agile board, honouring the board's own filter.

        Every returned key is checked: a board whose filter reaches outside the
        allowed projects raises rather than quietly widening scope.
        """
        out: list[dict] = []
        start = 0
        while True:
            params = {"startAt": start, "maxResults": 100,
                      "fields": ",".join(fields or DEFAULT_SEARCH_FIELDS)}
            if jql:
                params["jql"] = jql
            page = self._request("GET", f"/rest/agile/1.0/board/{board_id}/issue",
                                 params=params)
            batch = page.get("issues", [])
            for issue in batch:
                assert_key_allowed(issue["key"])
            out.extend(batch)
            start += len(batch)
            if not batch or start >= page.get("total", 0):
                break
            if limit and len(out) >= limit:
                break
        LOG.info("jira.board_issues", board=board_id, returned=len(out))
        return out[:limit] if limit else out

    def creatable_fields(self, project: str, issue_type: str) -> set[str]:
        """Field ids the create screen for this issue type actually accepts."""
        assert_project_allowed(project)
        meta = self._request("GET", "/rest/api/2/issue/createmeta",
                             params={"projectKeys": project,
                                     "issuetypeNames": issue_type,
                                     "expand": "projects.issuetypes.fields"})
        for proj in meta.get("projects", []):
            for it in proj.get("issuetypes", []):
                if it.get("name") == issue_type:
                    return set((it.get("fields") or {}).keys())
        return set()

    def find_users(self, query: str, max_results: int = 10) -> list[dict]:
        return self._request("GET", "/rest/api/2/user/search",
                             params={"query": query, "maxResults": max_results})


class JiraWriteClient(_Base):
    """Every method is a no-op unless execute=True. Every method has an undo."""

    def __init__(self, *args, execute: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.execute = bool(execute)
        self.performed: list[dict] = []

    def _do(self, action: str, detail: dict, fn) -> dict:
        if not self.execute:
            LOG.info("jira.dry_run", action=action, **detail)
            return {"dry_run": True, "action": action, **detail}
        result = fn()
        self.performed.append({"action": action, **detail, "result": result})
        LOG.info("jira.write", action=action, **detail)
        return result

    # -- comments ----------------------------------------------------------
    def add_comment(self, key: str, body: str) -> dict:
        assert_key_allowed(key)
        return self._do("add_comment", {"key": key, "chars": len(body)},
                        lambda: self._request("POST", f"/rest/api/2/issue/{key}/comment",
                                              body={"body": body}))

    def delete_comment(self, key: str, comment_id: str) -> dict:  # undo of add_comment
        assert_key_allowed(key)
        return self._do("delete_comment", {"key": key, "comment_id": comment_id},
                        lambda: self._request(
                            "DELETE", f"/rest/api/2/issue/{key}/comment/{comment_id}"))

    # -- issues ------------------------------------------------------------
    def create_issue(self, project: str, summary: str, description: str,
                     issue_type: str, labels: list[str] | None = None,
                     priority: str | None = None,
                     extra_fields: dict | None = None) -> dict:
        assert_project_allowed(project)
        fields: dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        if labels:
            fields["labels"] = labels
        if priority:
            fields["priority"] = {"name": priority}
        # Project-mandated fields (PRDT requires Department, for example).
        for key, value in (extra_fields or {}).items():
            fields[key] = value
        return self._do("create_issue", {"project": project, "issue_type": issue_type,
                                         "summary": summary[:120]},
                        lambda: self._request("POST", "/rest/api/2/issue",
                                              body={"fields": fields}))

    def delete_issue(self, key: str) -> dict:
        """Undo of create_issue. NOT USED: by the board owner's instruction this
        system never deletes a ticket — core.execute records a "Close as Won't
        Do" recommendation instead. Kept so the write/undo pairing stays honest."""
        assert_key_allowed(key)
        return self._do("delete_issue", {"key": key},
                        lambda: self._request("DELETE", f"/rest/api/2/issue/{key}"))

    def set_description(self, key: str, description: str) -> dict:
        assert_key_allowed(key)
        return self._do("set_description", {"key": key, "chars": len(description)},
                        lambda: self._request("PUT", f"/rest/api/2/issue/{key}",
                                              body={"fields": {"description": description}}))

    def restore_description(self, key: str, previous: str) -> dict:  # undo
        return self.set_description(key, previous)

    def set_priority(self, key: str, priority: str) -> dict:
        assert_key_allowed(key)
        return self._do("set_priority", {"key": key, "priority": priority},
                        lambda: self._request("PUT", f"/rest/api/2/issue/{key}",
                                              body={"fields":
                                                    {"priority": {"name": priority}}}))

    def restore_priority(self, key: str, previous: str) -> dict:  # undo
        return self.set_priority(key, previous)

    # -- links -------------------------------------------------------------
    def link_issues(self, inward_key: str, outward_key: str, link_type: str) -> dict:
        assert_key_allowed(inward_key)
        assert_key_allowed(outward_key)
        return self._do(
            "link_issues",
            {"inward": inward_key, "outward": outward_key, "type": link_type},
            lambda: self._request("POST", "/rest/api/2/issueLink", body={
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            }),
        )

    def find_link_id(self, key: str, other_key: str) -> str | None:
        reader = JiraReadClient(self.base_url, self.email, self.token)
        issue = reader.issue(key, fields="issuelinks")
        for link in issue.get("fields", {}).get("issuelinks", []):
            target = (link.get("inwardIssue") or link.get("outwardIssue") or {})
            if target.get("key") == other_key:
                return link.get("id")
        return None

    def delete_link(self, link_id: str) -> dict:  # undo of link_issues
        return self._do("delete_link", {"link_id": link_id},
                        lambda: self._request("DELETE", f"/rest/api/2/issueLink/{link_id}"))

    # -- assignment --------------------------------------------------------
    def assign(self, key: str, account_id: str | None) -> dict:
        assert_key_allowed(key)
        return self._do("assign", {"key": key, "account_id": account_id},
                        lambda: self._request("PUT", f"/rest/api/2/issue/{key}/assignee",
                                              body={"accountId": account_id}))

    def unassign(self, key: str) -> dict:  # undo of assign
        return self.assign(key, None)

    # -- transitions -------------------------------------------------------
    def transition(self, key: str, status_name: str,
                   fields: dict | None = None) -> dict:
        """Move an issue. Some transitions demand fields — PRDT's
        "Closed - Won't Do" requires a Resolution — so they can be passed here."""
        assert_key_allowed(key)
        reader = JiraReadClient(self.base_url, self.email, self.token)

        def go() -> dict:
            for tr in reader.transitions(key):
                if tr["to"]["name"].lower() == status_name.lower():
                    body: dict = {"transition": {"id": tr["id"]}}
                    if fields:
                        body["fields"] = fields
                    return self._request("POST", f"/rest/api/2/issue/{key}/transitions",
                                         body=body)
            raise JiraError(409, key, f"no transition to {status_name!r} available")

        return self._do("transition",
                        {"key": key, "to": status_name,
                         "fields": sorted(fields) if fields else None}, go)

    def transition_fields(self, key: str, status_name: str) -> dict:
        """Fields a transition requires, with their allowed values."""
        reader = JiraReadClient(self.base_url, self.email, self.token)
        data = reader._request("GET", f"/rest/api/2/issue/{key}/transitions",
                               params={"expand": "transitions.fields"})
        for tr in data.get("transitions", []):
            if tr["to"]["name"].lower() == status_name.lower():
                return {fid: f for fid, f in (tr.get("fields") or {}).items()
                        if f.get("required")}
        return {}

    def current_status(self, key: str) -> str:
        reader = JiraReadClient(self.base_url, self.email, self.token)
        return reader.issue(key, fields="status")["fields"]["status"]["name"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.jira_client",
                                 description="read-only CLI for poking at Jira")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    p_f = sub.add_parser("fields", help="list custom fields (find the requester email one)")
    p_f.add_argument("--grep", default="")
    p_i = sub.add_parser("issue")
    p_i.add_argument("key")
    p_s = sub.add_parser("search")
    p_s.add_argument("jql")
    p_s.add_argument("--limit", type=int, default=20)
    p_t = sub.add_parser("transitions")
    p_t.add_argument("key")
    p_it = sub.add_parser("issue-types", help="issue types valid for a project")
    p_it.add_argument("--project", default=config.dev_project())
    p_it.add_argument("--required", action="store_true",
                      help="list the fields the project demands on create")
    args = ap.parse_args(argv)

    client = JiraReadClient()
    if args.cmd == "whoami":
        me = client.myself()
        print(json.dumps({k: me.get(k) for k in ("accountId", "displayName", "emailAddress")},
                         indent=2))
    elif args.cmd == "fields":
        for f in client.fields():
            if args.grep.lower() in f["name"].lower():
                print(f"{f['id']:<24} {f['name']}")
    elif args.cmd == "issue":
        print(json.dumps(client.issue(args.key), indent=2)[:8000])
    elif args.cmd == "search":
        for issue in client.search(args.jql, limit=args.limit):
            f = issue["fields"]
            print(f"{issue['key']:<14} {f['status']['name']:<22} {f['summary'][:70]}")
    elif args.cmd == "transitions":
        for tr in client.transitions(args.key):
            print(f"{tr['id']:<6} -> {tr['to']['name']}")
    elif args.cmd == "issue-types":
        configured = config.dev_issue_type()
        if args.required:
            for fid, field in client.required_fields(args.project, configured).items():
                allowed = [a.get("value") or a.get("name")
                           for a in (field.get("allowedValues") or [])][:10]
                print(f"{fid:<22} {field['name']:<22}"
                      + (f" allowed: {allowed}" if allowed else ""))
        else:
            for it in client.issue_types(args.project):
                mark = "  <- boards.yml" if it["name"] == configured else ""
                print(f"{it['name']:<24} subtask={it.get('subtask')}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
