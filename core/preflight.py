"""Check every live assumption in one pass. Read-only — writes nothing, anywhere.

    python -m core.preflight
    python -m core.preflight --board 381

Each check prints PASS / WARN / FAIL, the value found, and the `boards.yml` key
it belongs to. Exit code is non-zero if anything FAILs, so this is safe to put in
front of the first live run.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

from core import config, log
from core.jira_client import JiraError, JiraReadClient, ScopeError

LOG = log.get("preflight")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

INTAKE_PERMISSIONS = ["BROWSE_PROJECTS", "ADD_COMMENTS", "TRANSITION_ISSUES",
                      "LINK_ISSUES"]
DEV_PERMISSIONS = ["BROWSE_PROJECTS", "CREATE_ISSUES", "ASSIGN_ISSUES",
                   "LINK_ISSUES", "DELETE_ISSUES"]


@dataclass
class Check:
    name: str
    status: str
    detail: str
    key: str = ""          # the config key this check is about
    fix: str = ""          # what to change if it is not PASS


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, *a, **kw) -> Check:
        check = Check(*a, **kw)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=10)
        lines = []
        for c in self.checks:
            lines.append(f"[{c.status}] {c.name.ljust(width)}  {c.detail}")
            if c.fix:
                lines.append(f"       {' ' * width}  -> {c.fix}")
        counts = {s: len([c for c in self.checks if c.status == s])
                  for s in (PASS, WARN, FAIL)}
        lines += ["", f"{counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail"]
        if counts[FAIL]:
            lines.append("Fix the failures before running with --execute.")
        return "\n".join(lines)


def run(board_id: str | None = None, sample_size: int = 25) -> Report:  # noqa: C901
    report = Report()
    intake_cfg = config.boards()["intake"]
    dev_cfg = config.boards()["development"]
    intake, dev = config.intake_project(), config.dev_project()

    # -- credentials -------------------------------------------------------
    missing = [k for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
               if not config.env(k)]
    if missing:
        report.add("credentials", FAIL, f"missing: {', '.join(missing)}",
                   fix="set them in .env (copy config/.env.example)")
        return report
    report.add("credentials", PASS, f"{config.env('JIRA_EMAIL')} @ {config.base_url()}")

    client = JiraReadClient()

    # -- auth --------------------------------------------------------------
    try:
        me = client.myself()
    except JiraError as exc:
        report.add("auth", FAIL, f"HTTP {exc.status} — {exc.body[:120]}",
                   fix="check JIRA_EMAIL matches the account that created the token")
        return report
    report.add("auth", PASS, f"{me.get('displayName')} ({me.get('accountId')})")
    report.add("comment identity", WARN,
               f"comments will be posted as {me.get('displayName')}",
               fix="use a service account if that is not what you want")

    # -- projects ----------------------------------------------------------
    statuses: dict[str, int] = {}
    sample: list[dict] = []
    for project in (intake, dev):
        try:
            issues = client.search(f"project = {project} ORDER BY created DESC",
                                   fields=["summary", "status", "created", "resolution"],
                                   limit=sample_size)
            report.add(f"project {project}", PASS,
                       f"readable, {len(issues)} recent issues sampled")
            if project == intake:
                sample = issues
                for issue in issues:
                    name = ((issue["fields"].get("status") or {}).get("name") or "?")
                    statuses[name] = statuses.get(name, 0) + 1
        except (JiraError, ScopeError) as exc:
            report.add(f"project {project}", FAIL, str(exc)[:160],
                       fix="grant the token browse access, or fix boards.yml")

    # -- intake status names ----------------------------------------------
    open_status = intake_cfg["open_status"]
    if statuses:
        found = ", ".join(f"{k} ({v})" for k, v in sorted(statuses.items(),
                                                          key=lambda kv: -kv[1]))
        if open_status in statuses:
            report.add("open_status", PASS, f"{open_status!r} seen in the sample",
                       key="intake.open_status")
        else:
            report.add("open_status", FAIL,
                       f"{open_status!r} not in the sample. Seen: {found}",
                       key="intake.open_status",
                       fix="set intake.open_status to the real name")
        unresolved = {((i["fields"].get("status") or {}).get("name"))
                      for i in sample if not i["fields"].get("resolution")}
        terminal = set(intake_cfg.get("resolved_statuses") or [])
        unknown = sorted(s for s in unresolved
                         if s and s not in intake_cfg["open_statuses"]
                         and s not in terminal)
        if unknown:
            report.add("open_statuses", WARN,
                       f"unresolved tickets sit in statuses not listed: "
                       f"{', '.join(unknown)}",
                       key="intake.open_statuses",
                       fix="add them, or duplicate detection will miss those tickets")
        else:
            report.add("open_statuses", PASS,
                       f"{len(intake_cfg['open_statuses'])} statuses cover every "
                       f"unresolved ticket in the sample",
                       key="intake.open_statuses")

    # -- requester email field --------------------------------------------
    field_id = intake_cfg.get("requester_email_field")
    try:
        fields = client.fields()
        by_id = {f["id"]: f["name"] for f in fields}
        candidates = [f"{f['id']} ({f['name']})" for f in fields
                      if "email" in f["name"].lower() and f["id"].startswith("custom")]
        if not field_id:
            report.add("requester_email_field", PASS,
                       "not configured — requester rule 1 is off, the intake sheet "
                       "does the work. Candidates if you want it on: "
                       + (", ".join(candidates[:4]) or "none"),
                       key="intake.requester_email_field")
        elif field_id in by_id:
            populated = 0
            for issue in sample[:10]:
                full = client.issue(issue["key"], fields=field_id)
                if (full.get("fields") or {}).get(field_id):
                    populated += 1
            status = PASS if populated else WARN
            report.add("requester_email_field", status,
                       f"{field_id} = {by_id[field_id]!r}, populated on "
                       f"{populated}/{min(10, len(sample))} sampled tickets",
                       key="intake.requester_email_field",
                       fix=("" if populated else
                            "rule 1 will never fire — the sheet join will do the work"))
        else:
            report.add("requester_email_field", FAIL,
                       f"{field_id} does not exist. Candidates: "
                       + (", ".join(candidates) or "none with 'email' in the name"),
                       key="intake.requester_email_field",
                       fix="set it to the right custom field, or '' to rely on the sheet")
    except JiraError as exc:
        report.add("requester_email_field", FAIL, str(exc)[:160])

    # -- transition --------------------------------------------------------
    target = intake_cfg["dev_transition"]
    probe = next((i for i in sample
                  if ((i["fields"].get("status") or {}).get("name")) == open_status),
                 sample[0] if sample else None)
    if probe:
        probe_status = ((probe["fields"].get("status") or {}).get("name"))
        on_open = probe_status == open_status
        try:
            available = [t["to"]["name"] for t in client.transitions(probe["key"])]
            if not target:
                report.add("dev_transition", PASS,
                           "not configured — PESD1 will not be transitioned on clone",
                           key="intake.dev_transition")
            elif target in available:
                report.add("dev_transition", PASS,
                           f"{target!r} reachable from {probe['key']} ({probe_status})",
                           key="intake.dev_transition")
            else:
                report.add("dev_transition", FAIL if on_open else WARN,
                           f"{target!r} not reachable from {probe['key']} "
                           f"(status {probe_status!r}). Available: "
                           f"{', '.join(available) or 'none'}",
                           key="intake.dev_transition",
                           fix=("pick one of the available names, or set it to '' to "
                                "leave PESD1 where it is"))
        except JiraError as exc:
            report.add("dev_transition", FAIL, str(exc)[:160])

    # -- clone issue type --------------------------------------------------
    want_type = dev_cfg.get("issue_type")
    try:
        types = [t["name"] for t in client.issue_types(dev)]
        if want_type in types:
            report.add("issue_type", PASS, f"{want_type!r} exists in {dev}",
                       key="development.issue_type")
        else:
            report.add("issue_type", FAIL,
                       f"{want_type!r} not in {dev}. Available: {', '.join(types) or 'none'}",
                       key="development.issue_type",
                       fix=f"set development.issue_type to one of {types[:5]}")
    except JiraError as exc:
        report.add("issue_type", FAIL, str(exc)[:160],
                   fix="the token may lack create permission on " + dev)

    # -- link type ---------------------------------------------------------
    want_link = dev_cfg.get("link_type")
    try:
        types = client.issue_link_types()
        names = {lt.get("name") for lt in types if lt.get("name")}
        match = next((lt for lt in types if lt.get("name") == want_link), None)
        if match:
            report.add("link_type", PASS,
                       f"{want_link!r} — reads as \"{config.dev_project()} "
                       f"{match.get('inward')} {config.intake_project()}\"",
                       key="development.link_type")
        else:
            # The API takes the type NAME; the inward/outward text is only a
            # description, and accepting it here would pass a config that fails
            # at execution.
            by_phrase = next((lt for lt in types
                              if want_link in (lt.get("inward"), lt.get("outward"))),
                             None)
            report.add("link_type", FAIL,
                       f"{want_link!r} is not a link type name."
                       + (f" It is the {'inward' if by_phrase and by_phrase.get('inward') == want_link else 'outward'}"
                          f" description of {by_phrase['name']!r}." if by_phrase else
                          f" Available: {', '.join(sorted(names)[:10])}"),
                       key="development.link_type",
                       fix=(f"set development.link_type to {by_phrase['name']!r}"
                            if by_phrase else "use one of the names above"))
    except JiraError as exc:
        report.add("link_type", FAIL, str(exc)[:160])

    # -- permissions -------------------------------------------------------
    for project, wanted in ((intake, INTAKE_PERMISSIONS), (dev, DEV_PERMISSIONS)):
        try:
            perms = client.my_permissions(project, wanted)
            missing_perms = [p for p in wanted if not perms.get(p)]
            optional = {"DELETE_ISSUES"}
            hard = [p for p in missing_perms if p not in optional]
            if not missing_perms:
                report.add(f"permissions {project}", PASS, "all present")
            elif hard:
                report.add(f"permissions {project}", FAIL,
                           f"missing: {', '.join(hard)}",
                           fix="grant them, or execution will fail mid-sequence")
            else:
                report.add(f"permissions {project}", WARN,
                           f"missing: {', '.join(missing_perms)}",
                           fix="undo() will not be able to delete a clone; it will "
                               "still unlink, unassign and revert the transition")
        except JiraError as exc:
            report.add(f"permissions {project}", WARN,
                       f"could not read permissions: {str(exc)[:100]}")

    # -- board -------------------------------------------------------------
    if board_id:
        try:
            cfg = client.board_configuration(board_id)
            report.add(f"board {board_id}", PASS,
                       f"{cfg.get('name')} — filter {(cfg.get('filter') or {}).get('id')}")
        except (JiraError, ScopeError) as exc:
            report.add(f"board {board_id}", WARN, str(exc)[:160])

    # -- volume ------------------------------------------------------------
    try:
        recent = client.search(f"project = {intake} AND created >= -180d "
                               f"ORDER BY created DESC",
                               fields=["summary"], limit=1000)
        report.add("corpus volume", PASS,
                   f"{len(recent)}{'+' if len(recent) >= 1000 else ''} {intake} tickets "
                   f"in the last 6 months",
                   fix=("start with --months 6" if len(recent) >= 1000 else ""))
        queue = client.search(f'project = {intake} AND status = "{open_status}"',
                              fields=["summary"], limit=500)
        report.add("current queue", PASS,
                   f"{len(queue)} tickets in {open_status!r} right now",
                   fix="run the first pass with --limit 1")
    except JiraError as exc:
        report.add("corpus volume", WARN, str(exc)[:160])

    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.preflight")
    ap.add_argument("--board", help="also check an agile board id, e.g. 381")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run(board_id=args.board)
    if args.json:
        print(json.dumps([c.__dict__ for c in report.checks], indent=2))
    else:
        print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
