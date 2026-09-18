import unittest

from core import proposals as P
from tests.helpers import sandbox


def base(**kw) -> dict:
    data = {
        "proposal_id": "p_0001",
        "ticket": "PESD1-11274",
        "ticket_url": "https://pomelofashion.atlassian.net/browse/PESD1-11274",
        "requirement_restated": "Requester reports: stock is stale after the import.",
        "requester": {"email": "a@pomelofashion.com", "source": "sheet",
                      "confidence": "high", "matched_row": {"x": 1}},
        "classification": "ANSWERABLE",
        "confidence": 0.8,
        "proposed_comment": "Here is the answer.",
        "evidence": [{"type": "jira", "ref": "PESD1-1", "why": "same symptom"}],
        "clone": None,
        "pesd1_transition": None,
        "flags": [],
    }
    data.update(kw)
    return data


def clone(**kw) -> dict:
    c = {"target_project": "PRDT", "summary": "[stock-sync] stale stock",
         "description": ("h2. Ask\n\nStock is stale after the import.\n\n"
                         "h2. Original request\n\n{quote}\nStock levels are not "
                         "updating after the nightly bulk import on the THA "
                         "warehouse.\n{quote}\n"),
         "assignee": "dev.one", "assignee_reason": "closed 7 of last 10",
         "assignee_alternates": ["dev.two"], "labels": [], "priority": "Medium",
         "link_type": "is cloned by"}
    c.update(kw)
    return c


class ProposalValidationTests(unittest.TestCase):
    def test_valid_proposal(self):
        self.assertEqual(P.validate(base()), [])

    def test_escalate_carries_no_comment_and_no_clone(self):
        problems = P.validate(base(classification="ESCALATE",
                                   proposed_comment="something",
                                   clone=clone(), pesd1_transition="In Development"))
        self.assertTrue(any("no proposed_comment" in p for p in problems))
        self.assertTrue(any("no clone" in p for p in problems))

    def test_needs_code_requires_clone(self):
        problems = P.validate(base(classification="NEEDS_CODE"))
        self.assertTrue(any("requires a clone" in p for p in problems))

    def test_duplicate_is_never_cloned(self):
        problems = P.validate(base(classification="DUPLICATE", clone=clone(),
                                   pesd1_transition="In Development"))
        self.assertTrue(any("duplicate is never cloned" in p for p in problems))

    def test_alternates_are_required(self):
        problems = P.validate(base(classification="NEEDS_CODE",
                                   clone=clone(assignee_alternates=[]),
                                   pesd1_transition="In Development"))
        self.assertTrue(any("assignee_alternates" in p for p in problems))

    def test_clone_description_keeps_the_request_verbatim(self):
        """The requester's own words must reach the developer unedited."""
        problems = P.validate(base(
            classification="NEEDS_CODE",
            clone=clone(description="A summary written by the system, long enough "
                                    "to pass the length floor but carrying none of "
                                    "the requester's own words anywhere in it."),
            pesd1_transition="In Development"))
        self.assertTrue(any("verbatim" in p for p in problems), problems)

    def test_clone_description_must_be_worth_reading(self):
        problems = P.validate(base(classification="NEEDS_CODE",
                                   clone=clone(description="{quote}fix it{quote}"),
                                   pesd1_transition="In Development"))
        self.assertTrue(any("too thin" in p for p in problems), problems)

    def test_unknown_requester_needs_a_flag_and_no_email(self):
        problems = P.validate(base(requester={"email": "guessed@x.com",
                                              "source": "unknown",
                                              "confidence": "unknown",
                                              "matched_row": {}}))
        self.assertTrue(any("never guess" in p for p in problems))
        self.assertTrue(any("requester_unknown" in p for p in problems))

    def test_medium_confidence_requires_matched_row(self):
        problems = P.validate(base(requester={"email": "a@b.com", "source": "sheet",
                                              "confidence": "medium",
                                              "matched_row": {}}))
        self.assertTrue(any("matched_row" in p for p in problems))

    def test_only_intake_board_tickets(self):
        problems = P.validate(base(ticket="PRDT-1",
                                   ticket_url="https://x/browse/PRDT-1"))
        self.assertTrue(any("intake board" in p for p in problems))

    def test_clone_must_target_dev_project(self):
        problems = P.validate(base(classification="NEEDS_CODE",
                                   clone=clone(target_project="APOLLO"),
                                   pesd1_transition="In Development"))
        self.assertTrue(any("must be PRDT" in p for p in problems))

    def test_transition_required_when_cloning(self):
        problems = P.validate(base(classification="NEEDS_CODE", clone=clone(),
                                   pesd1_transition=None))
        self.assertTrue(any("pesd1_transition must be" in p for p in problems))

    def test_credentials_never_reach_a_proposal(self):
        from core import config
        config._secrets.add("super-secret-token-value")
        try:
            problems = P.validate(base(
                proposed_comment="the token is super-secret-token-value"))
            self.assertTrue(any("credential" in p for p in problems))
        finally:
            config._secrets.discard("super-secret-token-value")

    def test_roundtrip_and_ids(self):
        with sandbox():
            p = P.Proposal.from_dict(base())
            p.proposal_id = P.next_id()
            P.save(p)
            again = P.load(p.proposal_id)
            self.assertEqual(again.to_dict()["ticket"], p.ticket)
            self.assertNotEqual(P.next_id(), p.proposal_id)

    def test_diff_reports_nested_fields(self):
        before, after = base(), base()
        after["clone"] = clone()
        after["proposed_comment"] = "edited"
        fields = [f for f, _, _ in P.diff(before, after)]
        self.assertIn("proposed_comment", fields)
        self.assertIn("clone", fields)


if __name__ == "__main__":
    unittest.main()
