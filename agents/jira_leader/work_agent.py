"""Continuous research, fix plans and requester draft outbox; no external writes.

Uses the existing corpus and analyst. The execution ledger stays authoritative:
researching a ticket here never reclaims or re-executes its Jira proposal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

from agents.historian.retrieval import Historian
from agents.jira_leader import analysis
from agents.jira_leader.gates import never_touch, restate
from agents.jira_leader.sources import FileTicketSource, FIELDS
from core import config, log, requester
from core.jira_client import JiraReadClient, assert_key_allowed

VERSION = 2


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(log.redact(value), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def query_candidates(research) -> list[dict]:
    """Quote documented SQL, never fabricate schema or authorize execution."""
    out = []
    for doc in research.sops:
        for sql in re.findall(r"```(?:sql|mysql)\s*\n(.*?)```", doc.get("body", ""), re.S | re.I):
            out.append({"query": sql.strip(), "source": doc["ref"],
                        "status": "REVIEW_REQUIRED", "executed": False,
                        "checks": ["Confirm database and schema", "Replace example identifiers with verified ticket values",
                                   "Review permissions and impact before running"]})
    return out


def plan_ticket(issue: dict, sheet: list[dict], historian, analyst) -> dict:
    assert_key_allowed(issue["key"])
    if issue["key"].split("-")[0] != config.intake_project():
        raise ValueError("work agent plans intake tickets only")
    fields = issue.get("fields", {})
    match = requester.resolve(issue, sheet)
    requirement, unclear = restate(issue)
    hits = never_touch(issue)
    packet = {"ticket": issue["key"], "url": config.ticket_url(issue["key"]),
              "summary": fields.get("summary", ""),
              "status": (fields.get("status") or {}).get("name"),
              "updated": fields.get("updated"), "requirement": requirement,
              "requester": match.to_dict(), "flags": match.flags,
              "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "actions_taken": [], "queries": [], "slack_draft": None,
              "email_update": None, "linked_development": issue.get("_linked_development", [])}
    if hits or not requirement:
        packet.update(classification="ESCALATE", confidence=0.0,
                      escalation=sorted({h["category"] for h in hits}) or [unclear],
                      steps=["Owner reviews this request and decides the permitted next action."],
                      evidence=[], analyst="gates")
        return packet
    research = historian.research(issue["key"], fields.get("summary", ""), fields.get("description") or "")
    result = analyst.analyse(issue, research, requirement, analysis.load_rules())
    evidence = research.evidence()
    packet.update(classification=result.classification, confidence=result.confidence,
                  analyst=result.analyst, evidence=evidence, proposed_comment=result.proposed_comment,
                  developer_summary=result.developer_summary,
                  flags=list(dict.fromkeys(packet["flags"] + result.flags)))
    if research.duplicates:
        packet.update(classification="DUPLICATE", duplicate=research.duplicates[0]["ref"])
    relevant_sops = [d for d in research.sops if float(d.get("similarity", 0)) >= 0.6]
    relevant_resolved = [d for d in research.similar_resolved if float(d.get("similarity", 0)) >= 0.6]
    sources = relevant_sops or relevant_resolved
    # Source excerpts are suggestions, never proof that this ticket has the same cause.
    packet["fix_evidence"] = [{"ref": d["ref"], "title": d["title"],
                               "similarity": d.get("similarity"),
                               "excerpt": (d.get("body") or "")[:2400]} for d in sources[:3]]
    packet["resolution_summary"] = (
        f"Candidate procedure: {sources[0]['title']} ({sources[0]['ref']}). Confirm that its prerequisites match this ticket before using it."
        if sources else "No supported fix found in the indexed history. Investigation is required.")
    packet["fix_verified"] = False
    packet["steps"] = [
        "Confirm the affected identifiers, shop/environment and expected result from the ticket and intake row.",
        "Check the latest ticket and linked development comments for a completed fix or existing work.",
    ]
    if packet.get("duplicate"):
        packet["steps"].append(f"Verify the overlap with {packet['duplicate']} and propose linking; do not create another clone.")
    elif relevant_sops:
        packet["steps"].append(f"Check the prerequisites in {relevant_sops[0]['ref']} and follow its documented procedure only if they match this case.")
    elif relevant_resolved:
        packet["steps"].append(f"Compare this case with the resolution in {relevant_resolved[0]['ref']} before choosing the same fix.")
    else:
        packet["steps"].append("Collect a reproducible example and investigate the affected component; no confirmed fix is documented.")
    packet["steps"] += ["Prepare the scoped fix or operational instructions for review, including how to undo the change.",
                         "Verify the original symptom and expected result after any approved change; then draft the requester update."]
    packet["queries"] = query_candidates(research)
    packet["jira_query"] = f'project = {config.intake_project()} AND key = "{issue["key"]}"'
    field_id = config.boards()["intake"].get("requester_email_field")
    if field_id and not fields.get(field_id) and match.email and match.confidence == "high" and match.source == "sheet":
        packet["email_update"] = {"field_id": field_id, "previous": fields.get(field_id),
                                  "value": match.email, "status": "PROPOSAL_REQUIRED"}
    # Only exact identities enter the delivery queue. Medium matches remain visible for review.
    if match.email and match.confidence == "high":
        next_step = (f"I’m checking the existing work in {packet['duplicate']}." if packet.get("duplicate")
                     else "I’m checking the documented procedure against your case." if sources
                     else "Could you share an affected example and the expected result so we can confirm the next step?")
        packet["slack_draft"] = {"email": match.email, "text":
            f"Following up on {issue['key']}: {requirement}\n"
            f"The ticket is currently in {packet['status']}. {next_step}\n{packet['url']}",
            "status": "READY_FOR_SLACK_LOOKUP", "sent": False}
    return packet


def render_report(packets: list[dict], errors: list[dict]) -> str:
    cards = []
    for p in packets:
        cards.append(f'<article><h2><a href="{html.escape(p["url"], quote=True)}">{html.escape(p["ticket"])}</a> — {html.escape(p["summary"])}</h2>'
                     f'<p>{html.escape(str(p["classification"]))} · {html.escape(str(p["status"]))} · analyst: {html.escape(p["analyst"])}</p>'
                     '<pre>' + html.escape(json.dumps(p, ensure_ascii=False, indent=2)) + '</pre></article>')
    return ('<!doctype html><html><meta charset="utf-8"><title>PESD1 work review</title>'
            '<style>body{max-width:1100px;margin:32px auto;padding:0 24px;font:16px system-ui;background:#f6f7f9;color:#17202a}'
            'article{background:white;padding:20px;margin:20px 0;border-radius:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}</style>'
            '<h1>PESD1 work review</h1><p>Proposed actions and requester drafts. No fixes, SQL, Jira updates or Slack messages have been executed by this report.</p>'
            + '<p>' + html.escape(json.dumps(errors)) + '</p>' + ''.join(cards) + '</html>')


def run(issues: list[dict], *, sheet: list[dict], output: Path, historian=None, analyst=None) -> dict:
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    own_hist = historian is None
    historian = historian or Historian()
    analyst = analyst or analysis.get_analyst()
    packets, errors = [], []
    sheet_hash = digest(sheet)
    try:
        for issue in issues:
            key = assert_key_allowed(issue["key"])
            path = output / "tickets" / f"{key}.json"
            fingerprint = digest({"version": VERSION, "issue": issue, "sheet": sheet_hash,
                                  "rules": analysis.load_rules(), "analyst": analyst.name,
                                  "corpus_mtime": config.CORPUS_DB.stat().st_mtime_ns if config.CORPUS_DB.exists() else None})
            try:
                old = json.loads(path.read_text()) if path.exists() else {}
                if old.get("fingerprint") == fingerprint:
                    packet = old
                else:
                    packet = plan_ticket(issue, sheet, historian, analyst)
                    packet["fingerprint"] = fingerprint
                    write_json(path, packet)
                packets.append(packet)
            except Exception as exc:
                errors.append({"ticket": key, "error": str(exc)})
    finally:
        if own_hist:
            historian.close()
    grouped: dict[str, list[dict]] = {}
    for p in packets:
        if p.get("slack_draft"):
            grouped.setdefault(p["slack_draft"]["email"], []).append(p)
    outbox = [{"email": email, "tickets": [p["ticket"] for p in group],
               "message": "\n\n".join(p["slack_draft"]["text"] for p in group),
               "status": "READY_FOR_SLACK_LOOKUP", "sent": False} for email, group in sorted(grouped.items())]
    summary = {"tickets": len(packets), "escalations": sum(p["classification"] == "ESCALATE" for p in packets),
               "requesters_verified": sum(p["requester"]["confidence"] == "high" for p in packets),
               "email_updates_proposed": sum(bool(p.get("email_update")) for p in packets),
               "slack_drafts_prepared": len(outbox), "sheet_rows": len(sheet),
               "analyst": analyst.name, "errors": errors, "output": str(output),
               "blockers": ([] if sheet else ["Requester sheet CSV is unavailable or empty"])
                           + (["LLM analyst is unavailable; research uses the heuristic"] if analyst.name == "heuristic" else [])}
    write_json(output / "outbox.json", outbox)
    write_json(output / "summary.json", summary)
    report = output / "index.html"
    report.write_text(render_report(packets, errors), encoding="utf-8")
    report.chmod(0o600)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", help="CSV exported from the authorized intake tab")
    parser.add_argument("--file", help="Offline Jira JSONL")
    parser.add_argument("--output", type=Path, default=config.REVIEW_DIR / "work_agent")
    parser.add_argument("--analyst", choices=["auto", "claude", "heuristic"], default="auto")
    args = parser.parse_args(argv)
    analyst = analysis.get_analyst(args.analyst)
    if args.file:
        issues = FileTicketSource(args.file).open_tickets()
    else:
        client = JiraReadClient()
        field_id = config.boards()["intake"].get("requester_email_field")
        issues = client.board_issues(381,
            jql=f'project = {config.intake_project()} AND status in ("Waiting Support", "Blocked", "Waiting for customer")',
            fields=FIELDS + ["issuelinks"] + ([field_id] if field_id else []))
        for issue in issues:
            issue["fields"]["comment"] = {"comments": client.comments(issue["key"])}
            linked = []
            for link in issue["fields"].get("issuelinks", []):
                target = link.get("inwardIssue") or link.get("outwardIssue") or {}
                key = target.get("key", "")
                if key.startswith(config.dev_project() + "-"):
                    dev = client.issue(key, fields="summary,status,comment,updated")
                    dev["fields"]["comment"] = {"comments": client.comments(key)}
                    linked.append(dev)
            issue["_linked_development"] = linked
    summary = run(issues, sheet=requester.load_sheet(args.sheet), output=args.output, analyst=analyst)
    print(json.dumps(summary, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
