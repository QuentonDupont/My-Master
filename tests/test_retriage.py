"""Retriage: re-run triage on one ticket by hand, because a rule changed --
built 12 Oct 2026 after three one-off scripts corrected stored proposals by
hand that day (Crashlytics x2, six return tickets)."""
from __future__ import annotations

import unittest

from agents.jira_leader.sources import FileTicketSource
from core import config, ledger as L, proposals as P, scheduler
from core.flow import Context, FlowError
from flows.intake import INTAKE
from flows.retriage import RETRIAGE
from tests.helpers import FIXTURES, build_corpus, sandbox


def sweep():
    return INTAKE.run(source=FileTicketSource(FIXTURES / "inbox.jsonl"),
                      analyst_kind="heuristic",
                      sheet_path=str(FIXTURES / "intake_sheet.csv"))


def issue(key: str) -> dict:
    return FileTicketSource(FIXTURES / "inbox.jsonl").get(key)


class RetriageTests(unittest.TestCase):
    def test_refuses_a_ticket_not_in_the_ledger(self):
        with sandbox():
            res = RETRIAGE.run(ticket="PESD1-99999", reason="x", issue=issue("PESD1-11274"))
            self.assertFalse(res.ok)
            self.assertIn("not in the ledger", res.error)

    def test_refuses_without_a_reason(self):
        with sandbox():
            build_corpus()
            sweep()
            res = RETRIAGE.run(ticket="PESD1-11274", issue=issue("PESD1-11274"))
            self.assertFalse(res.ok)
            self.assertIn("needs a reason", res.error)

    def test_refuses_a_ticket_not_yet_triaged_or_already_decided(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                led.upsert_new("PESD1-1", "h")  # NEW -- normal sweep's job, not this one
            res = RETRIAGE.run(ticket="PESD1-1", reason="x", issue=issue("PESD1-11274"))
            self.assertFalse(res.ok)
            self.assertIn("not one of", res.error)

    def test_cannot_be_scheduled(self):
        with sandbox():
            with self.assertRaises(FlowError):
                scheduler.run_flow(RETRIAGE, trigger="schedule", confirm=True)

    def test_corrects_a_duplicate_in_place_keeping_the_same_proposal_id(self):
        """PESD1-11274 comes out of the fixture sweep as DUPLICATE. Retriage it
        with a fixed route that did not exist at sweep time and it should land
        PROPOSED under the SAME proposal_id -- a correction, not an orphan."""
        with sandbox():
            build_corpus()
            before_sweep = sweep()
            original_pid = next(o["proposal_id"] for o in before_sweep.data["report"]["outcomes"]
                                if o["ticket"] == "PESD1-11274")
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11274")["state"], L.DUPLICATE)

            config.boards()["fixed_routes"] = [{
                "name": "test_route", "summary_prefix": "Stock levels",
                "labels": ["tech_ops"], "assignee": "Someone Else",
                "comment": "routed by the new rule"}]
            res = RETRIAGE.run(ticket="PESD1-11274", reason="taught a new fixed route",
                               issue=issue("PESD1-11274"))
            self.assertTrue(res.ok, res.error)

            with L.Ledger() as led:
                row = led.get("PESD1-11274")
            self.assertEqual(row["state"], L.PROPOSED)
            self.assertEqual(row["proposal_id"], original_pid)

            after = P.load(original_pid)
            self.assertEqual(after.clone.assignee, "Someone Else")
            self.assertEqual(P.validate(after.to_dict()), [])
            self.assertEqual(len(list((config.REVIEW_DIR / "proposals").glob("*.json"))),
                             len(before_sweep.data["report"]["outcomes"]))  # no orphan file

    def test_records_a_correction_with_the_given_reason(self):
        with sandbox():
            build_corpus()
            sweep()
            config.boards()["fixed_routes"] = [{
                "name": "test_route", "summary_prefix": "Stock levels",
                "labels": ["tech_ops"], "assignee": "Someone Else",
                "comment": "routed by the new rule"}]
            RETRIAGE.run(ticket="PESD1-11274", reason="taught a new fixed route",
                        issue=issue("PESD1-11274"))
            from core import corrections
            logged = corrections.load()
            self.assertTrue(any(c["reason"] == "taught a new fixed route" for c in logged))

    def test_a_proposed_ticket_can_also_be_retriaged(self):
        """PESD1-11275 comes out PROPOSED already -- confirms the PROPOSED ->
        CLAIMED edge this flow needed added to core/ledger.py."""
        with sandbox():
            build_corpus()
            before = sweep()
            states = {o["ticket"]: o["state"] for o in before.data["report"]["outcomes"]}
            self.assertEqual(states["PESD1-11275"], L.PROPOSED)
            res = RETRIAGE.run(ticket="PESD1-11275", reason="re-checking",
                               issue=issue("PESD1-11275"))
            self.assertTrue(res.ok, res.error)
            with L.Ledger() as led:
                self.assertIn(led.get("PESD1-11275")["state"],
                              (L.PROPOSED, L.ESCALATED, L.DUPLICATE))

    def test_a_failed_retriage_still_releases_the_ticket(self):
        """TRIAGE's own crash handling (CLAIMED -> NEW) covers a failure inside
        the sub-flow; retriage must not leave the ticket stuck CLAIMED."""
        from flows import triage

        with sandbox():
            build_corpus()
            sweep()
            saved = triage.classify.run
            boom = lambda ctx: (_ for _ in ()).throw(RuntimeError("down"))  # noqa: E731
            object.__setattr__(triage.classify, "run", boom)
            try:
                res = RETRIAGE.run(ticket="PESD1-11275", reason="x",
                                   issue=issue("PESD1-11275"))
            finally:
                object.__setattr__(triage.classify, "run", saved)
            self.assertFalse(res.ok)
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11275")["state"], L.NEW)


if __name__ == "__main__":
    unittest.main()
