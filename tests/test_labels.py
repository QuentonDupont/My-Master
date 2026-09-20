"""Approving from Jira with a label.

The label is the only approval route that works without the laptop, so these
tests care most about it not approving something nobody labelled, and about a
rejection carrying a reason the Chief of Staff can learn from.
"""
import unittest

from agents.jira_leader import labels as LB
from core import corrections, ledger as L, proposals as P
from tests.helpers import sandbox


class FakeJira:
    def __init__(self, labels, comments=None):
        self.labels = labels
        self._comments = comments or {}

    def issue(self, key, fields="*all"):
        return {"fields": {"labels": list(self.labels.get(key, []))}}

    def comments(self, key):
        return [{"body": b} for b in self._comments.get(key, [])]


def proposed(led, ticket="PESD1-11280", pid=None):
    """A proposal sitting at PROPOSED, the only state a label may decide."""
    proposal = P.Proposal(
        proposal_id=pid or P.next_id(), ticket=ticket,
        ticket_url=f"https://pomelofashion.atlassian.net/browse/{ticket}",
        requirement_restated="Requester reports: something is wrong.",
        requester=P.Requester(), classification=P.ANSWERABLE, confidence=0.9,
        proposed_comment="Thanks for raising this, here is the answer.",
        flags=["requester_unknown"])
    P.save(proposal)
    led.claim(ticket, "hash")
    led.transition(ticket, L.PROPOSED, proposal_id=proposal.proposal_id)
    return proposal


class ReadingTests(unittest.TestCase):
    def test_an_approve_label_is_picked_up(self):
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL]})
                found = LB.pending(j, led)
            self.assertEqual(found[0]["decision"], "approve")
            self.assertEqual(found[0]["proposal_id"], p.proposal_id)

    def test_an_unlabelled_ticket_is_left_alone(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": ["henry", "urgent"]})
                self.assertEqual(LB.pending(j, led), [])

    def test_a_rejection_takes_its_reason_from_the_newest_comment(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.REJECT_LABEL]},
                             {"PESD1-11280": ["old note",
                                              "wrong SOP, this is about returns"]})
                found = LB.pending(j, led)
            self.assertEqual(found[0]["decision"], "reject")
            self.assertIn("about returns", found[0]["note"])

    def test_a_rejection_without_a_comment_still_says_so(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.REJECT_LABEL]})
                self.assertIn("no reason given", LB.pending(j, led)[0]["note"])

    def test_both_labels_at_once_is_a_conflict_not_a_guess(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL, LB.REJECT_LABEL]})
                found = LB.pending(j, led)
            self.assertEqual(found[0]["decision"], "conflict")

    def test_only_proposals_awaiting_review_are_considered(self):
        """A decided or escalated ticket must not be re-decided by an old label."""
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
                led.transition("PESD1-11280", L.ESCALATED)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL]})
                self.assertEqual(LB.pending(j, led), [])

    def test_one_unreadable_ticket_does_not_stop_the_poll(self):
        class Flaky(FakeJira):
            def issue(self, key, fields="*all"):
                if key == "PESD1-11280":
                    raise RuntimeError("permission denied")
                return super().issue(key, fields)

        with sandbox():
            with L.Ledger() as led:
                proposed(led, ticket="PESD1-11280")
                proposed(led, ticket="PESD1-11281")
                j = Flaky({"PESD1-11281": [LB.APPROVE_LABEL]})
                found = LB.pending(j, led)
            self.assertEqual([f["ticket"] for f in found], ["PESD1-11281"])


class PollTests(unittest.TestCase):
    def test_reporting_does_not_change_anything(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL]})
                out = LB.poll(j, led, apply=False)
                self.assertIsNone(out["applied"])
                self.assertEqual(led.get("PESD1-11280")["state"], L.PROPOSED)

    def test_applying_marks_the_proposal_approved(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL]})
                out = LB.poll(j, led, apply=True)
                self.assertEqual(led.get("PESD1-11280")["state"], L.APPROVED)
            self.assertEqual(len(out["applied"]["approved"]), 1)

    def test_a_rejection_reaches_the_corrections_log(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.REJECT_LABEL]},
                             {"PESD1-11280": ["not what was asked for"]})
                LB.poll(j, led, apply=True)
                self.assertEqual(led.get("PESD1-11280")["state"], L.REJECTED)
            logged = corrections.load()
            self.assertTrue(any("not what was asked for" in str(c) for c in logged))

    def test_a_conflict_is_never_applied(self):
        with sandbox():
            with L.Ledger() as led:
                proposed(led)
                j = FakeJira({"PESD1-11280": [LB.APPROVE_LABEL, LB.REJECT_LABEL]})
                out = LB.poll(j, led, apply=True)
                self.assertEqual(led.get("PESD1-11280")["state"], L.PROPOSED)
            self.assertEqual(len(out["conflicts"]), 1)


class NoWriteTests(unittest.TestCase):
    def test_labels_are_read_never_written(self):
        """Approving in Jira must not itself write to Jira."""
        import pathlib
        import re
        text = pathlib.Path(LB.__file__).read_text(encoding="utf-8")
        body = text.split('"""', 2)[-1]
        self.assertEqual(set(re.findall(r"writer\.(\w+)\(", body)), set())
        for forbidden in ("JiraWriteClient", "add_comment", "transition("):
            self.assertNotIn(forbidden, body, forbidden)


if __name__ == "__main__":
    unittest.main()
