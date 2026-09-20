import json
import unittest

from agents.jira_leader import mobile
from core import corrections, ledger as L, proposals as P
from tests.helpers import sandbox


class MobileBridgeTests(unittest.TestCase):
    def _proposed(self, led):
        """A proposal sitting in PROPOSED, as the phone would see it."""
        from tests.test_proposals import base, clone
        data = base(classification="NEEDS_CODE", proposed_comment="hello",
                    clone=clone(), pesd1_transition="In Development")
        data["proposal_id"] = P.next_id()
        proposal = P.Proposal.from_dict(data)
        P.save(proposal)
        led.claim(proposal.ticket, "h1")
        led.transition(proposal.ticket, L.PROPOSED, proposal_id=proposal.proposal_id)
        return proposal

    def test_export_shapes_a_document_the_page_can_render(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                doc = mobile.to_document(proposal, "18 Sep 12:00")
            for key in ("ticket", "url", "classification", "confidence", "requirement",
                        "comment", "clone", "decision", "generated"):
                self.assertIn(key, doc)
            self.assertEqual(doc["decision"], "pending")
            self.assertEqual(doc["clone"]["alternates"],
                             proposal.clone.assignee_alternates)

    def test_approve_moves_to_approved(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                out = mobile.apply([{"proposal_id": proposal.proposal_id,
                                     "decision": "approve"}], led)
                self.assertEqual(out["approved"], [proposal.proposal_id])
                self.assertEqual(led.get(proposal.ticket)["state"], L.APPROVED)

    def test_reassignment_is_a_correction_not_a_silent_edit(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                other = proposal.clone.assignee_alternates[0]
                out = mobile.apply([{"proposal_id": proposal.proposal_id,
                                     "decision": "approve",
                                     "assignee_override": other,
                                     "note": "on leave"}], led)
                self.assertEqual(out["corrected"][0]["assignee"], other)
                self.assertEqual(led.get(proposal.ticket)["state"], L.CORRECTED)
                self.assertEqual(P.load(proposal.proposal_id).clone.assignee, other)
                logged = corrections.load()
                self.assertTrue(any(c["field"] == "clone.assignee"
                                    and c["became"] == other for c in logged))

    def test_reject_is_logged_with_its_reason(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                mobile.apply([{"proposal_id": proposal.proposal_id,
                               "decision": "reject", "note": "wrong team"}], led)
                self.assertEqual(led.get(proposal.ticket)["state"], L.REJECTED)
                self.assertTrue(any(c["field"] == "__rejected__"
                                    and "wrong team" in c["reason"]
                                    for c in corrections.load()))

    def test_pending_is_left_alone(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                out = mobile.apply([{"proposal_id": proposal.proposal_id,
                                     "decision": "pending"}], led)
                self.assertEqual(out["skipped"], [proposal.proposal_id])
                self.assertEqual(led.get(proposal.ticket)["state"], L.PROPOSED)

    def test_a_decision_on_an_already_executed_proposal_is_refused(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = self._proposed(led)
                led.transition(proposal.ticket, L.APPROVED)
                led.transition(proposal.ticket, L.EXECUTED)
                out = mobile.apply([{"proposal_id": proposal.proposal_id,
                                     "decision": "approve"}], led)
                self.assertTrue(out["errors"])
                self.assertEqual(led.get(proposal.ticket)["state"], L.EXECUTED)

    def test_the_bridge_cannot_write_to_jira(self):
        import inspect
        source = inspect.getsource(mobile)
        self.assertNotIn("JiraWriteClient", source)
        self.assertNotIn("execute_proposal", source)


if __name__ == "__main__":
    unittest.main()


class ExportedDecisionTests(unittest.TestCase):
    """The exported document must say what the ledger says.

    It used to hardcode "pending". Harmless while the review page kept its own
    copy of decisions; wrong the moment anything re-exports on a poll, because
    every refresh reported an already decided proposal as still waiting — so
    approving in the panel looked like it did nothing at all.
    """

    def test_each_ledger_state_maps_to_what_a_reviewer_should_see(self):
        from agents.jira_leader.mobile import _decision_of
        from core import ledger as L
        self.assertEqual(_decision_of({"state": L.PROPOSED}), "pending")
        self.assertEqual(_decision_of({"state": L.ESCALATED}), "pending")
        self.assertEqual(_decision_of({"state": L.APPROVED}), "approve")
        self.assertEqual(_decision_of({"state": L.CORRECTED}), "approve")
        self.assertEqual(_decision_of({"state": L.EXECUTED}), "approve")
        self.assertEqual(_decision_of({"state": L.REJECTED}), "reject")

    def test_an_unknown_or_missing_row_is_pending(self):
        from agents.jira_leader.mobile import _decision_of
        self.assertEqual(_decision_of(None), "pending")
        self.assertEqual(_decision_of({}), "pending")
        self.assertEqual(_decision_of({"state": "SOMETHING_NEW"}), "pending")

    def test_an_approved_proposal_exports_as_approved(self):
        from agents.jira_leader import mobile
        from core import ledger as L, proposals as P
        from tests.helpers import sandbox
        with sandbox():
            with L.Ledger() as led:
                p = P.Proposal(
                    proposal_id=P.next_id(), ticket="PESD1-10485",
                    ticket_url="https://example.invalid/PESD1-10485",
                    requirement_restated="Requester reports: something.",
                    requester=P.Requester(), classification=P.ANSWERABLE,
                    confidence=0.9, proposed_comment="An answer.",
                    flags=["requester_unknown"])
                P.save(p)
                led.claim("PESD1-10485", "hash")
                led.transition("PESD1-10485", L.PROPOSED,
                               proposal_id=p.proposal_id)
                row = led.get("PESD1-10485")
                self.assertEqual(
                    mobile.to_document(p, "now", row)["decision"], "pending")
                led.transition("PESD1-10485", L.APPROVED)
                row = led.get("PESD1-10485")
                self.assertEqual(
                    mobile.to_document(p, "now", row)["decision"], "approve")
