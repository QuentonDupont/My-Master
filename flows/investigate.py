"""Investigate — root cause and a step-by-step fix for PESD1 tickets. Human-triggered.

    load_investigation_tickets -> extract_ids -> match_playbooks -> check_database ->
    search_code -> write_investigations

For each ticket: the ids it names (returns, orders, POs, products), the playbook it
matches (agents/investigator/playbooks.py), the evidence from read-only queries on
spanky / henry (templates from the Queries repo `catalog/`), the code paths involved
(greps of the local repo clones), and a packet under review/investigations/<KEY>.md
with the root cause, the fix steps, any fix SQL for a person to run, and the questions
still open.

Read-only everywhere except the packet itself: no Jira write, no database write, no
repo write. Added 12 Nov 2026 by the board owner — see CLAUDE.md invariant 2.

    python -m flows run investigate --data '{"tickets": ["PESD1-11391"]}'
    python -m flows run investigate            # every open intake ticket

Context in (optional): tickets (keys; default every ticket in the intake open status),
ids ({"PESD1-11388": {"pos": [18387, ...]}} — ids a person read from a linked sheet),
issues (skip the Jira fetch, for tests), runner (stand-in for db.query, for tests).
"""
from __future__ import annotations

from core import config
from core.flow import LOCAL, READ, Flow, Human, agent

FOLDER = config.REVIEW_DIR / "investigations"


def _text(issue: dict, comments: list[str]) -> str:
    from agents.jira_leader.gates import request_text
    f = issue.get("fields") or {}
    return "\n".join([f.get("summary") or "", request_text(f), *comments])


@agent("load_investigation_tickets", READ)
def load_investigation_tickets(ctx):
    """The tickets to investigate, with their request text and comments."""
    if ctx.get("issues") is not None:
        ctx["items"] = [{"issue": i, "text": _text(i, i.get("_comments", []))} for i in ctx["issues"]]
        return
    from core.jira_client import JiraReadClient
    client = JiraReadClient()
    intake = config.boards()["intake"]
    keys = ctx.get("tickets")
    if not keys:
        jql = f'project = {intake["project"]} AND status = "{intake["open_status"]}" ORDER BY created ASC'
        keys = [i["key"] for i in client.search(jql, fields=["summary"])]
    items, skipped = [], []
    for key in keys:
        for attempt in (1, 2):   # one retry: a single Jira timeout must not sink the batch
            try:
                issue = client.issue(key)
                status = ((issue.get("fields") or {}).get("status") or {})
                if (status.get("statusCategory") or {}).get("key") == "done":
                    skipped.append(f"{key}: already {status.get('name')} — nothing to investigate")
                    break
                comments = [c.get("body") or "" for c in client.comments(key)][-10:]
                items.append({"issue": issue, "text": _text(issue, comments)})
                break
            except Exception as exc:  # noqa: BLE001 — record and move on to the next ticket
                if attempt == 2:
                    skipped.append(f"{key}: {str(exc)[:160]}")
    ctx["items"], ctx["skipped"] = items, skipped


@agent("extract_ids", READ)
def extract_ids(ctx):
    """Order, return, PO, product and SKU ids each ticket names."""
    from agents.investigator import entities
    extra = ctx.get("ids") or {}
    for it in ctx["items"]:
        it["entities"] = entities.extract(it["text"]).merge(extra.get(it["issue"]["key"]))


@agent("match_playbooks", READ)
def match_playbooks(ctx):
    """The known issue type each ticket is, if any."""
    from agents.investigator import playbooks
    for it in ctx["items"]:
        it["playbooks"] = playbooks.choose(it["text"], it["entities"])


@agent("check_database", READ)
def check_database(ctx):
    """Run the matched playbook's read-only queries and read them into a root cause."""
    from agents.investigator import catalog, db
    from agents.investigator.playbooks import Finding
    try:
        templates = catalog.load()
    except catalog.RenderError as exc:
        templates, ctx["catalog_error"] = {}, str(exc)
    runner = ctx.get("runner") or db.query
    ok, why = (True, "test runner") if ctx.get("runner") else db.available()
    ctx["db_label"] = why if ok else f"not checked — {why}"

    def run(tid, **params):
        t = templates.get(tid)
        if not t or t.kind != "read":
            raise db.DatabaseUnavailable(f"read template '{tid}' is missing from {catalog.repo_path()}/catalog")
        return runner(t.db, t.render(**params))

    def render(tid, **params):
        t = templates.get(tid)
        if not t or t.kind != "write":
            return f"-- write template '{tid}' is missing from the Queries repo catalog"
        return f"-- {t.title} ({t.db})\n" + t.render(**params)

    for it in ctx["items"]:
        it["finding"], it["errors"], it["playbook"] = Finding(), [], None
        ents = it["entities"]
        for pb in it["playbooks"]:
            if not getattr(ents, pb.needs):
                where = (f"they are probably in the linked file(s): {', '.join(ents.links)} — read them and re-run with "
                         f"--data '{{\"tickets\": [\"{it['issue']['key']}\"], \"ids\": {{\"{it['issue']['key']}\": {{\"{pb.needs}\": [...]}}}}}}'"
                         if ents.links else "ask the requester for them")
                it["finding"].info_needed.append(f"Looks like '{pb.title}' but the ticket text gives no {pb.needs} ids — {where}.")
                continue
            it["playbook"] = pb
            try:
                it["finding"] = pb.investigate(ents, run, render)
            except db.DatabaseUnavailable as exc:
                it["errors"].append(f"{pb.title}: {exc}")
                it["finding"].info_needed.append("Database checks did not run — run the playbook's queries by hand (see Queries repo).")
            except (catalog.RenderError, db.NotReadOnly) as exc:
                it["errors"].append(f"{pb.title}: {exc}")
            break
        if ctx.get("catalog_error"):
            it["errors"].append(f"Queries catalog: {ctx['catalog_error']}")


@agent("search_code", READ)
def search_code(ctx):
    """Where in the Pomelo repos the matched playbook's behaviour lives."""
    from agents.investigator import code
    for it in ctx["items"]:
        pb = it.get("playbook") or (it["playbooks"][0] if it["playbooks"] else None)
        hits = []
        for term in (pb.code_terms if pb else []):
            hits += code.search(term, list(pb.code_repos) or None, max_hits=3)
        it["code"] = hits[:12]


@agent("write_investigations", LOCAL)
def write_investigations(ctx):
    """One packet per ticket under review/investigations/."""
    from agents.investigator import report
    folder = ctx.get("output") or FOLDER
    from agents.investigator.playbooks import is_feature_request
    written = []
    for it in ctx["items"]:
        issue, f, pb = it["issue"], it["finding"], it.get("playbook")
        if is_feature_request(it["text"]) and not f.info_needed:
            f.info_needed = ["Feature request — new work, not a fault, so there is no root cause to find. "
                             "Route it to the platform's developer as a PRDT story."]
        inv = {
            "ticket": issue["key"],
            "summary": (issue.get("fields") or {}).get("summary") or "",
            "url": f"{config.env('JIRA_BASE_URL', 'https://pomelofashion.atlassian.net')}/browse/{issue['key']}",
            "db_label": ctx.get("db_label", "not checked"),
            "entities": it["entities"].as_dict(),
            "playbook": pb.title if pb else "",
            "precedent": pb.precedent if pb else "",
            "never_touch": bool(pb and pb.never_touch),
            "confidence": f.confidence,
            "root_cause": f.root_cause,
            "evidence": f.evidence,
            "steps": f.steps,
            "fix_sql": f.fix_sql,
            "info_needed": f.info_needed or ([] if pb else ["No known playbook matches — read the ticket, solve it by hand, then add a playbook and its queries."]),
            "code": it.get("code", []),
            "queries_run": f.queries_run,
            "errors": it["errors"],
        }
        written.append(str(report.write(inv, folder)))
    ctx["report"] = {"written": written, "db": ctx.get("db_label"), "skipped": ctx.get("skipped", [])}


INVESTIGATE = Flow(
    "investigate",
    [load_investigation_tickets, extract_ids, match_playbooks, check_database, search_code,
     write_investigations],
    triggers=[Human()],
    doc="Root cause + step-by-step fix per PESD1 ticket from read-only DB checks, the Queries catalog and the code.",
    timeout_seconds=1800,
)

FLOWS = (INVESTIGATE,)
