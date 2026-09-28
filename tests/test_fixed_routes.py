"""Fixed routes: ticket families whose handling the board owner already decided."""
import json
import pathlib
import tempfile
import unittest

from agents.jira_leader import queue as queue_mod, worker
from agents.jira_leader.sources import FileTicketSource
from core import config, ledger as L, proposals as P
from tests.helpers import FIXTURES, build_corpus, sandbox

ROUTE = {"name": "crashlytics", "summary_prefix": "[Crashlytics]",
         "labels": ["business_continuity", "tech_ops"],
         "assignee": "Rahul Ravindra",
         "comment": "Automated crash alert, passed to the Android team."}


def crash(key: str, where: str, version: str) -> dict:
    return {"key": key, "fields": {
        "summary": f"[Crashlytics] [New Fatal Issue] {where}",
        "description": (f"Crashlytics detected a new fatal issue in Pomelo Fashion!\n\n"
                        f"* *Summary*: {where}\n* *Platform*: Android\n"
                        f"* *Version*: {version}\n"),
        "status": {"name": "Waiting for Support"}, "resolution": None,
        "created": "2026-09-26T05:54:24.000+0700",
        "updated": "2026-09-26T05:54:27.000+0700",
        "reporter": {"displayName": "Suresh Dhakal"}, "assignee": None,
        "labels": [], "components": [], "priority": {"name": "Low"},
        "issuetype": {"name": "Bug"}, "comment": {"comments": []}}}


class FixedRouteTests(unittest.TestCase):
    def run_inbox(self, issues):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = pathlib.Path(tmp) / "inbox.jsonl"
            inbox.write_text("".join(json.dumps(i) + "\n" for i in issues))
            return queue_mod.run(FileTicketSource(inbox), analyst_kind="heuristic",
                                 sheet_path=str(FIXTURES / "intake_sheet.csv"))

    def test_crashlytics_alerts_are_cloned_verbatim_to_the_route_owner(self):
        with sandbox():
            config.boards()["fixed_routes"] = [ROUTE]
            build_corpus()
            issues = [crash("PESD1-11298", "SignInHubActivity.onCreate", "4.88.2"),
                      # near-identical summary: must not be taken as a duplicate
                      crash("PESD1-11299", "SignInHubActivity.onResume", "4.88.12")]
            summary = self.run_inbox(issues)
            states = {o["ticket"]: o["state"] for o in summary["outcomes"]}
            self.assertEqual(states, {"PESD1-11298": L.PROPOSED,
                                      "PESD1-11299": L.PROPOSED})

            by_ticket = {p.ticket: p for p in P.load_all()}
            for issue in issues:
                p = by_ticket[issue["key"]]
                self.assertEqual(p.classification, P.NEEDS_CODE)
                self.assertEqual(p.clone.summary, issue["fields"]["summary"])
                self.assertTrue(p.clone.description.endswith(
                    "----\n\n" + issue["fields"]["description"]))
                self.assertEqual(p.clone.assignee, "Rahul Ravindra")
                self.assertEqual(p.clone.labels, ["business_continuity", "tech_ops"])
                self.assertEqual(p.clone.priority, "Low")
                self.assertIn("route:crashlytics", p.flags)
                self.assertEqual(P.validate(p.to_dict()), [])

    def test_other_tickets_are_not_routed(self):
        with sandbox():
            config.boards()["fixed_routes"] = [ROUTE]
            self.assertIsNone(worker.fixed_route(
                {"fields": {"summary": "Checkout fails on Crashlytics-enabled build"}}))
            self.assertEqual(worker.fixed_route(
                {"fields": {"summary": "[crashlytics] [New Fatal Issue] x"}}), ROUTE)

    def test_no_routes_configured_means_no_routing(self):
        with sandbox():
            config.boards().pop("fixed_routes", None)
            self.assertIsNone(worker.fixed_route(crash("PESD1-1", "x", "1")))

    def test_live_config_routes_crashlytics_to_its_epic(self):
        live = config.load_yaml(pathlib.Path(config.__file__).resolve().parent.parent
                                / "config" / "boards.yml")
        route = next(r for r in live["fixed_routes"] if r["name"] == "crashlytics")
        self.assertEqual(route["assignee"], "Rahul Ravindra")
        self.assertEqual(route["labels"], ["business_continuity", "tech_ops"])
        epics = live["development"]["epic_by_component"]
        self.assertEqual(epics[route["labels"][0]], "PRDT-11608")


if __name__ == "__main__":
    unittest.main()
