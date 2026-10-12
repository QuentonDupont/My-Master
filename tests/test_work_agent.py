import json
import unittest
from unittest.mock import patch

from agents.historian.retrieval import Historian
from agents.jira_leader.analysis import HeuristicAnalyst
from agents.jira_leader.work_agent import run
from core import config, requester
from tests.helpers import sandbox, build_corpus, issue, FIXTURES


class WorkAgentTests(unittest.TestCase):
    def test_sensitive_ticket_never_produces_email_update_query_or_message(self):
        with sandbox() as root:
            build_corpus()
            ticket = issue("PESD1-11276")
            ticket["fields"]["summary"] = "Please refund the duplicate payment for this order"
            sheet = [{"jira_ticket": ticket["key"], "email": "person@example.com"}]
            with Historian() as hist:
                summary = run([ticket], sheet=sheet, output=root / "work", historian=hist,
                              analyst=HeuristicAnalyst())
            packet = json.loads((root / "work/tickets/PESD1-11276.json").read_text())
            self.assertEqual(summary["escalations"], 1)
            self.assertIsNone(packet["email_update"])
            self.assertIsNone(packet["slack_draft"])
            self.assertEqual(packet["queries"], [])

    def test_unchanged_input_is_cached_and_drafts_group_by_verified_email(self):
        with sandbox() as root:
            build_corpus()
            tickets = [issue("PESD1-11279"), issue("PESD1-11274")]
            for ticket in tickets:
                ticket["fields"].update(summary="How can I upload product images?", description="Please explain the supported image format.", labels=[], components=[])
            sheet = [{"jira_ticket": t["key"], "requester_email": "one@example.com"} for t in tickets]
            output = root / "work"
            with Historian() as hist:
                first = run(tickets, sheet=sheet, output=output, historian=hist, analyst=HeuristicAnalyst())
                with patch("agents.jira_leader.work_agent.plan_ticket", side_effect=AssertionError("cache missed")):
                    second = run(tickets, sheet=sheet, output=output, historian=hist, analyst=HeuristicAnalyst())
            self.assertEqual(first["errors"], [])
            self.assertEqual(second["errors"], [])
            outbox = json.loads((output / "outbox.json").read_text())
            self.assertEqual(len(outbox), 1)
            self.assertEqual(set(outbox[0]["tickets"]), {t["key"] for t in tickets})
            self.assertFalse(outbox[0]["sent"])

    def test_sheet_change_invalidates_cached_identity(self):
        with sandbox() as root:
            build_corpus()
            ticket = issue("PESD1-11279")
            ticket["fields"].update(summary="How can I upload product images?", description="Please explain the supported image format.", labels=[], components=[])
            output = root / "work"
            with Historian() as hist:
                for email in ("one@example.com", "two@example.com"):
                    summary = run([ticket], sheet=[{"jira_ticket": ticket["key"], "email": email}],
                                  output=output, historian=hist, analyst=HeuristicAnalyst())
                    self.assertEqual(summary["errors"], [])
                    self.assertEqual(json.loads((output / "outbox.json").read_text())[0]["email"], email)


class MatchingTests(unittest.TestCase):
    def test_email_in_body_cannot_replace_requester_email(self):
        with sandbox():
            row = {"jira_ticket": "PESD1-11274", "email": "owner@example.com", "request": "contact other@example.com"}
            self.assertEqual(requester.resolve(issue("PESD1-11274"), [row]).email, "owner@example.com")

    def test_conflicting_exact_keys_do_not_pick_first_row(self):
        with sandbox():
            rows = [{"jira_ticket": "PESD1-11274", "email": e} for e in ("one@example.com", "two@example.com")]
            match = requester.resolve(issue("PESD1-11274"), rows)
            self.assertIsNone(match.email)
            self.assertIn("requester_ambiguous", match.flags)

    def test_ticket_mentioned_in_request_body_is_not_an_identity_join(self):
        with sandbox():
            match = requester.resolve(issue("PESD1-11274"), [{"jira_ticket": "PESD1-1", "email": "wrong@example.com",
                                                           "request": "similar to PESD1-11274"}])
            self.assertIsNone(match.email)


if __name__ == "__main__":
    unittest.main()
