"""Re-entry: a human's answer to an escalated ticket rejoins the normal flow.

ESCALATED is terminal by design, and these tests are mostly about keeping it
that way for the cases where it should be. The one door this opens is narrow:
a person supplies the text, it is recorded as a correction, and it leaves through
`batch apply` / `execute_proposal` like anything else. No new write path.
"""
import unittest

from agents.jira_leader import reentry
from core import corrections, ledger as L, proposals as P
from tests.helpers import sandbox

ANSWER = ("The COGS GL was not recorded because the SKUs had zero inventory on "
          "hand at TH Bangna at the time of fulfilment, so NetSuite could not "
          "determine the item cost. Working as designed.")


def make(led, ticket="PESD1-10390", flags=("sanity_gate", "requester_unknown"),
         state=L.ESCALATED):
    """An escalated ticket with a bare ESCALATE proposal, as the worker leaves it."""
    proposal = P.Proposal(
        proposal_id=P.next_id(), ticket=ticket,
        ticket_url=f"https://pomelofashion.atlassian.net/browse/{ticket}",
        requirement_restated="Unclear — no request and no reported problem.",
        requester=P.Requester(), classification=P.ESCALATE, confidence=0.0,
        flags=list(flags))
    P.save(proposal)
    led.claim(ticket, "hash")
    if state != L.CLAIMED:
        led.transition(ticket, state, proposal_id=proposal.proposal_id)
    return proposal


class AnswerTests(unittest.TestCase):
    def test_an_answered_ticket_becomes_reviewable(self):
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                out = reentry.answer(p.proposal_id, ANSWER, led=led)
                self.assertEqual(out["state"], L.PROPOSED)
                self.assertEqual(led.get(p.ticket)["state"], L.PROPOSED)

    def test_the_answer_becomes_the_comment(self):
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                reentry.answer(p.proposal_id, ANSWER, led=led)
            saved = P.load(p.proposal_id)
            self.assertEqual(saved.proposed_comment, ANSWER)
            self.assertEqual(saved.classification, P.ANSWERABLE)

    def test_it_is_marked_as_written_by_a_person(self):
        """Provenance matters downstream: this is not the system's own answer."""
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                reentry.answer(p.proposal_id, ANSWER, led=led)
            saved = P.load(p.proposal_id)
            self.assertIn(reentry.HUMAN_FLAG, saved.flags)
            self.assertNotIn("sanity_gate", saved.flags)
            self.assertEqual(saved.confidence, 1.0)

    def test_the_answer_reaches_corrections(self):
        """The whole point: the Chief of Staff must see where a human stepped in."""
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                reentry.answer(p.proposal_id, ANSWER, led=led,
                               reason="netsuite dev had already answered it")
            logged = [c for c in corrections.load()
                      if c["proposal_id"] == p.proposal_id]
            self.assertTrue(logged)
            fields = {c["field"] for c in logged}
            self.assertIn("proposed_comment", fields)
            self.assertIn("classification", fields)

    def test_no_clone_is_created_by_answering(self):
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                reentry.answer(p.proposal_id, ANSWER, led=led)
            saved = P.load(p.proposal_id)
            self.assertIsNone(saved.clone)
            self.assertIsNone(saved.pesd1_transition)


class RefusalTests(unittest.TestCase):
    def test_a_never_touch_escalation_stays_escalated(self):
        """Invariant 3. A human routing their own answer back through the system
        is still the system posting it."""
        with sandbox():
            with L.Ledger() as led:
                p = make(led, ticket="PESD1-11276",
                         flags=("never_touch", "never_touch:pricing",
                                "requester_unknown"))
                with self.assertRaises(reentry.ReentryRefused) as caught:
                    reentry.answer(p.proposal_id, ANSWER, led=led)
                self.assertIn("never-touch", str(caught.exception))
                self.assertEqual(led.get(p.ticket)["state"], L.ESCALATED)

    def test_a_ticket_that_was_not_escalated_is_refused(self):
        with sandbox():
            with L.Ledger() as led:
                p = make(led, state=L.PROPOSED)
                with self.assertRaises(reentry.ReentryRefused) as caught:
                    reentry.answer(p.proposal_id, ANSWER, led=led)
                self.assertIn("PROPOSED", str(caught.exception))

    def test_an_empty_answer_is_refused(self):
        with sandbox():
            with L.Ledger() as led:
                p = make(led)
                for blank in ("", "   ", "\n"):
                    with self.assertRaises(reentry.ReentryRefused):
                        reentry.answer(p.proposal_id, blank, led=led)
                self.assertEqual(led.get(p.ticket)["state"], L.ESCALATED)

    def test_a_refusal_leaves_no_trace(self):
        """A refused re-entry must not half-edit the proposal."""
        with sandbox():
            with L.Ledger() as led:
                p = make(led, ticket="PESD1-11276",
                         flags=("never_touch:pricing", "requester_unknown"))
                with self.assertRaises(reentry.ReentryRefused):
                    reentry.answer(p.proposal_id, ANSWER, led=led)
            saved = P.load(p.proposal_id)
            self.assertEqual(saved.classification, P.ESCALATE)
            self.assertEqual(saved.proposed_comment, "")
            self.assertEqual(corrections.load(), [])


class ListingTests(unittest.TestCase):
    def test_listing_says_which_may_be_answered(self):
        with sandbox():
            with L.Ledger() as led:
                make(led, ticket="PESD1-10390",
                     flags=("sanity_gate", "requester_unknown"))
                make(led, ticket="PESD1-11276",
                     flags=("never_touch", "never_touch:pricing",
                            "requester_unknown"))
                rows = {r["ticket"]: r for r in reentry.escalated(led)}
            self.assertTrue(rows["PESD1-10390"]["answerable"])
            self.assertFalse(rows["PESD1-11276"]["answerable"])
            self.assertIn("never_touch", rows["PESD1-11276"]["why_not"])


class NoNewWritePathTests(unittest.TestCase):
    def test_reentry_never_touches_jira(self):
        """Invariant 1: execute_proposal stays the only write path."""
        import pathlib
        text = pathlib.Path(reentry.__file__).read_text(encoding="utf-8")
        for forbidden in ("JiraWriteClient", "add_comment", "create_issue",
                          "execute_proposal"):
            self.assertNotIn(forbidden, text.split('"""', 2)[-1], forbidden)


if __name__ == "__main__":
    unittest.main()
