"""Export the corpus sources to corpus/raw/*.jsonl. Read-only, always.

    python -m corpus.export jira --project PESD1 --months 18
    python -m corpus.export jira --project PRDT  --months 18
    python -m corpus.export github --keys PESD1-10233 PESD1-10990
    python -m corpus.export all --months 18

Then: python -m corpus.index build
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

from core import config, log
from core.jira_client import JiraReadClient

LOG = log.get("export")
RAW = config.CORPUS_DIR / "raw"

JIRA_FIELDS = ["summary", "description", "status", "resolution", "created", "updated",
               "reporter", "assignee", "labels", "components", "priority", "issuetype",
               "comment", "issuelinks"]


def export_jira(project: str, months: int = 18, limit: int | None = None) -> int:
    client = JiraReadClient()
    jql = (f"project = {project} AND created >= -{months * 30}d "
           f"ORDER BY created DESC")
    issues = client.search(jql, fields=JIRA_FIELDS, limit=limit)
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{project.lower()}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for issue in issues:
            fh.write(json.dumps(issue, ensure_ascii=False) + "\n")
    LOG.info("export.jira", project=project, issues=len(issues), path=str(out))
    return len(issues)


#: used only for a space with no `limit` in config/confluence.yml
DEFAULT_PAGE_LIMIT = 200


def space_specs(space_key: str | None = None) -> list[dict]:
    """The spaces to export, each carrying its configured limit.

    A named space keeps the limit set for it in config/confluence.yml. Before
    this, `--space OP` built its own spec with `limit: None`, so the configured
    600 was never consulted and OP was silently capped at the 200 default —
    exporting 125 of 554 pages while reporting success.
    """
    configured = {s.get("key"): s for s in
                  (config.confluence().get("spaces") or []) if s.get("key")}
    if not space_key:
        return list(configured.values())
    spec = dict(configured.get(space_key) or {})
    spec["key"] = space_key
    return [spec]


def export_confluence(space_key: str | None = None,
                      limit: int | None = None) -> int:
    """Export Confluence pages as corpus documents. Read-only."""
    from core.confluence_client import ConfluenceReadClient, to_text

    client = ConfluenceReadClient()
    wanted = space_specs(space_key)
    RAW.mkdir(parents=True, exist_ok=True)
    total = 0
    for spec in wanted:
        key = spec["key"]
        space = client.space(key)
        if not space:
            LOG.warn("confluence.space_missing", space=key)
            continue
        cap = limit or spec.get("limit") or DEFAULT_PAGE_LIMIT
        pages = client.pages(space["id"], limit=cap)
        out = RAW / f"confluence_{key.lower()}.jsonl"
        written = 0
        with out.open("w", encoding="utf-8") as fh:
            for page in pages:
                try:
                    full = client.page_body(page["id"])
                except Exception as exc:  # a single unreadable page is not fatal
                    LOG.warn("confluence.page_failed", page=page["id"],
                             error=str(exc)[:120])
                    continue
                body = ((full.get("body") or {}).get("storage") or {}).get("value", "")
                text = to_text(body)
                if len(text) < 40:
                    continue  # a stub page is noise in retrieval
                fh.write(json.dumps({
                    "doc_id": f"confluence:{page['id']}",
                    "source_type": "confluence",
                    "ref": f"{key}/{page['title'][:60]}",
                    "title": page["title"],
                    "body": text,
                    "url": client.page_url(full),
                    "updated": ((full.get("version") or {}).get("createdAt")),
                }, ensure_ascii=False) + "\n")
                written += 1
        LOG.info("export.confluence", space=key, pages=written, path=str(out))
        total += written
    return total


def _github_search(path: str, query: str, token: str) -> list[dict]:
    url = f"https://api.github.com{path}?" + urllib.parse.urlencode(
        {"q": query, "per_page": 30})
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("items", [])
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        LOG.warn("github.error", status=exc.code, query=query)
        return []


def export_github(keys: list[str]) -> int:
    """Commits and PRs that mention a ticket key. Skipped without GITHUB_TOKEN."""
    token = config.env("GITHUB_TOKEN")
    if not token:
        LOG.warn("github.skipped", reason="GITHUB_TOKEN not set")
        return 0
    cfg = config.repos()
    org = cfg["org"]
    repo_filter = " ".join(f"repo:{org}/{r['name']}" for r in cfg["repos"])
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "github.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for key in keys:
            for item in _github_search("/search/commits", f"{key} {repo_filter}", token):
                fh.write(json.dumps({
                    "doc_id": f"github:commit:{item['sha'][:12]}",
                    "source_type": "github", "ref": item["sha"][:12],
                    "title": (item["commit"]["message"].splitlines() or [""])[0][:200],
                    "body": f"{key}\n\n{item['commit']['message']}",
                    "assignee": (item.get("author") or {}).get("login"),
                    "url": item["html_url"], "created": item["commit"]["author"]["date"],
                }) + "\n")
                n += 1
            for item in _github_search("/search/issues", f"{key} is:pr {repo_filter}", token):
                fh.write(json.dumps({
                    "doc_id": f"github:pr:{item['id']}",
                    "source_type": "github", "ref": f"PR#{item['number']}",
                    "title": item["title"][:200],
                    "body": f"{key}\n\n{item.get('body') or ''}",
                    "assignee": (item.get("user") or {}).get("login"),
                    "url": item["html_url"], "created": item["created_at"],
                }) + "\n")
                n += 1
    LOG.info("export.github", documents=n, path=str(out))
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpus.export")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_j = sub.add_parser("jira")
    p_j.add_argument("--project", default=config.intake_project())
    p_j.add_argument("--months", type=int, default=18)
    p_j.add_argument("--limit", type=int)
    p_c = sub.add_parser("confluence")
    p_c.add_argument("--space")
    p_c.add_argument("--limit", type=int)
    p_g = sub.add_parser("github")
    p_g.add_argument("--keys", nargs="+", required=True)
    p_a = sub.add_parser("all")
    p_a.add_argument("--months", type=int, default=18)
    args = ap.parse_args(argv)

    if args.cmd == "jira":
        print(export_jira(args.project, args.months, args.limit))
    elif args.cmd == "confluence":
        print(export_confluence(args.space, args.limit))
    elif args.cmd == "github":
        print(export_github(args.keys))
    else:
        total = sum(export_jira(p, args.months) for p in config.allowed_projects())
        total += export_confluence()
        print(total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
