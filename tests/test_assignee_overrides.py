"""Assignee overrides: a known-correct developer for a ticket family, without
skipping the research or comment the way fixed_routes does. NCG item
creation -> Vishal is the one in production, built 12 Oct 2026 after the
heuristic kept assigning two live NCG tickets (PESD1-11333/11334) to the
board owner instead."""
import unittest

from agents.historian.retrieval import Retrieval
from agents.jira_leader import analysis as analysis_mod, worker
from core import config, proposals as P, requester as requester_mod
from core.flow import Context
from flows.triage import plan_clone
from tests.helpers import fixture_boards, sandbox

OVERRIDE = {"name": "ncg_item_creation",
           "summary_prefix": "Request to create new NCG item",
           "assignee": "Vishal Gaikwad", "labels": ["netsuite", "ncg"]}

NCG_ISSUE = {"key": "PESD1-11333", "fields": {
    "summary": "Request to create new NCG item: Paper Shopping Bag - Brown (S_NCG0147)",
    "description": "Please create S_NCG0147 in NetSuite.", "labels": [], "components": []}}


class StubHistorian:
    """Raises if asked to rank developers -- an override must skip this."""

    def assignee_candidates(self, *a, **kw):
        raise AssertionError("assignee_candidates called despite a matching override")


def plan_clone_ctx(issue, classification=P.NEEDS_CODE, historian=None):
    ctx = Context("triage", data={
        "issue": issue, "key": issue["key"],
        "summary": issue["fields"]["summary"],
        "description": issue["fields"]["description"],
        "requester": requester_mod.UNKNOWN, "research": Retrieval(),
        "historian": historian or StubHistorian(),
        "flags": [],
        "analysis": analysis_mod.Analysis(
            classification=classification, confidence=0.6,
            requirement_restated="create an NCG item", proposed_comment="draft",
            labels=[]),
    })
    return ctx


class AssigneeOverrideTests(unittest.TestCase):
    def test_matching_prefix_is_found(self):
        with sandbox():
            config.boards()["assignee_overrides"] = [OVERRIDE]
            self.assertEqual(worker.assignee_override(NCG_ISSUE), OVERRIDE)

    def test_non_matching_summary_is_not_overridden(self):
        with sandbox():
            config.boards()["assignee_overrides"] = [OVERRIDE]
            self.assertIsNone(worker.assignee_override(
                {"fields": {"summary": "Request to Create New SKU - S_NCG0140"}}))
            # the real gap this prefix deliberately does not widen into:
            self.assertIsNone(worker.assignee_override(
                {"fields": {"summary": "Unable to Process Receipts for NCG Transfer Orders"}}))

    def test_no_overrides_configured_means_no_override(self):
        with sandbox():
            config.boards().pop("assignee_overrides", None)
            self.assertIsNone(worker.assignee_override(NCG_ISSUE))

    def test_plan_clone_applies_the_override_without_ranking_developers(self):
        with sandbox(), fixture_boards():
            config.boards()["assignee_overrides"] = [OVERRIDE]
            ctx = plan_clone_ctx(NCG_ISSUE)
            plan_clone.run(ctx)  # StubHistorian raises if candidates are ranked
            clone = ctx["clone"]
            self.assertEqual(clone.assignee, "Vishal Gaikwad")
            self.assertEqual(clone.assignee_alternates, ["Vishal Gaikwad"])
            self.assertEqual(clone.labels, ["netsuite", "ncg"])
            self.assertIn("assignee_override:ncg_item_creation", ctx["flags"])

    def test_answerable_tickets_are_untouched_by_the_override_table(self):
        with sandbox(), fixture_boards():
            config.boards()["assignee_overrides"] = [OVERRIDE]
            ctx = plan_clone_ctx(NCG_ISSUE, classification=P.ANSWERABLE)
            plan_clone.run(ctx)
            self.assertIsNone(ctx["clone"])

    def test_live_config_resolves_an_epic_via_its_first_label(self):
        live = config.load_yaml(__import__("pathlib").Path(config.__file__)
                                .resolve().parent.parent / "config" / "boards.yml")
        override = next(o for o in live["assignee_overrides"]
                        if o["name"] == "ncg_item_creation")
        self.assertEqual(override["assignee"], "Vishal Gaikwad")
        epics = live["development"]["epic_by_component"]
        self.assertIn(override["labels"][0], epics)


if __name__ == "__main__":
    unittest.main()
