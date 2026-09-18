import unittest

from core import execute as E, ledger as L, proposals as P
from tests.helpers import sandbox
from tests.test_proposals import base, clone


class FakeWriter:
    """Stands in for JiraWriteClient. Records calls; can be told to fail one."""

    def __init__(self, execute=True, fail_on=None):
        self.execute = execute
        self.fail_on = fail_on
        self.calls = []
        self.status = "Waiting for Support"

    def _record(self, name, **kw):
        self.calls.append((name, kw))
        if self.fail_on == name:
            raise RuntimeError(f"boom in {name}")

    def current_status(self, key):
        return self.status

    def add_comment(self, key, body):
        self._record("comment", key=key)
        return {"id": "c-100"}

    def create_issue(self, project, summary, description, issue_type,
                     labels=None, priority=None, extra_fields=None):
        self._record("clone", project=project, issue_type=issue_type,
                     extra_fields=extra_fields or {})
        return {"key": "PRDT-999"}

    def link_issues(self, inward, outward, link_type):
        self._record("link", inward=inward, outward=outward)
        return {"id": "l-1"}

    def find_link_id(self, a, b):
        return "l-1"

    def delete_link(self, link_id):
        self._record("delete_link", link_id=link_id)
        return {}

    def assign(self, key, account_id):
        self._record("assign", key=key, account_id=account_id)
        return {}

    def unassign(self, key):
        self._record("unassign", key=key)
        return {}

    def transition(self, key, status):
        self._record("transition", key=key, to=status)
        self.status = status
        return {}

    def delete_comment(self, key, comment_id):
        self._record("delete_comment", comment_id=comment_id)
        return {}

    def delete_issue(self, key):
        self._record("delete_issue", key=key)
        return {}


class FakeReader:
    def find_users(self, query):
        return [{"accountId": "acc-1", "displayName": query,
                 "emailAddress": "dev@pomelofashion.com"}]


def approved_proposal(led, classification="NEEDS_CODE"):
    data = base(classification=classification, proposed_comment="hello",
                clone=clone() if classification == "NEEDS_CODE" else None,
                pesd1_transition="In Development"
                if classification == "NEEDS_CODE" else None)
    data["proposal_id"] = P.next_id()
    proposal = P.Proposal.from_dict(data)
    P.save(proposal)
    led.claim(proposal.ticket, "h1")
    led.transition(proposal.ticket, L.PROPOSED, proposal_id=proposal.proposal_id)
    led.transition(proposal.ticket, L.APPROVED)
    return proposal


class ExecuteTests(unittest.TestCase):
    def test_execution_order_and_ledger_update(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                writer = FakeWriter()
                result = E.execute_proposal(proposal.proposal_id, execute=True,
                                            ledger=led, writer=writer,
                                            reader=FakeReader())
                self.assertTrue(result.ok)
                self.assertEqual([c[0] for c in writer.calls],
                                 ["comment", "clone", "link", "assign", "transition"])
                row = led.get(proposal.ticket)
                self.assertEqual(row["state"], L.EXECUTED)
                self.assertEqual(row["clone_key"], "PRDT-999")
                self.assertEqual(row["comment_id"], "c-100")

    def test_clone_uses_the_issue_type_from_boards_yml(self):
        from core import config
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                writer = FakeWriter()
                E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                   writer=writer, reader=FakeReader())
                clone_call = next(c[1] for c in writer.calls if c[0] == "clone")
                self.assertEqual(clone_call["issue_type"], config.dev_issue_type())

    def test_dry_run_writes_nothing_and_leaves_the_ledger_alone(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                writer = FakeWriter(execute=False)
                result = E.execute_proposal(proposal.proposal_id, execute=False,
                                            ledger=led, writer=writer,
                                            reader=FakeReader())
                self.assertTrue(result.dry_run)
                self.assertEqual(led.get(proposal.ticket)["state"], L.APPROVED)

    def test_never_executes_twice(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                   writer=FakeWriter(), reader=FakeReader())
                with self.assertRaises(E.ExecutionRefused) as ctx:
                    E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                       writer=FakeWriter(), reader=FakeReader())
                self.assertIn("already EXECUTED", str(ctx.exception))

    def test_refuses_a_proposal_the_human_has_not_approved(self):
        with sandbox():
            with L.Ledger() as led:
                data = base(classification="ANSWERABLE")
                data["proposal_id"] = P.next_id()
                proposal = P.Proposal.from_dict(data)
                P.save(proposal)
                led.claim(proposal.ticket, "h")
                led.transition(proposal.ticket, L.PROPOSED,
                               proposal_id=proposal.proposal_id)
                with self.assertRaises(E.ExecutionRefused):
                    E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                       writer=FakeWriter(), reader=FakeReader())

    def test_refuses_an_escalation(self):
        with sandbox():
            with L.Ledger() as led:
                data = base(classification="ESCALATE", proposed_comment="",
                            clone=None, pesd1_transition=None,
                            flags=["never_touch"])
                data["proposal_id"] = P.next_id()
                proposal = P.Proposal.from_dict(data)
                P.save(proposal)
                led.claim(proposal.ticket, "h")
                led.transition(proposal.ticket, L.PROPOSED,
                               proposal_id=proposal.proposal_id)
                led.transition(proposal.ticket, L.APPROVED)
                with self.assertRaises(E.ExecutionRefused):
                    E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                       writer=FakeWriter(), reader=FakeReader())

    def test_failure_mid_sequence_rolls_back_that_item_only(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                writer = FakeWriter(fail_on="assign")
                result = E.execute_proposal(proposal.proposal_id, execute=True,
                                            ledger=led, writer=writer,
                                            reader=FakeReader())
                self.assertFalse(result.ok)
                names = [c[0] for c in writer.calls]
                self.assertIn("unassign", names)          # this item rolled back
                self.assertNotIn("transition", names)     # sequence stopped
                row = led.get(proposal.ticket)
                self.assertEqual(row["state"], L.APPROVED)   # NOT executed
                self.assertIn("assign", row["last_error"])
                journal = [j["step"] for j in led.journal_for(proposal.ticket)]
                self.assertIn("assign", journal)

    def test_undo_reverses_every_write(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                writer = FakeWriter()
                E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                   writer=writer, reader=FakeReader())
                undo_writer = FakeWriter()
                result = E.undo(proposal.ticket, execute=True, ledger=led,
                                writer=undo_writer)
                self.assertTrue(result.ok)
                names = [c[0] for c in undo_writer.calls]
                self.assertEqual(names, ["transition", "unassign", "delete_link",
                                         "delete_issue", "delete_comment"])
                row = led.get(proposal.ticket)
                self.assertEqual(row["state"], L.ROLLED_BACK)
                self.assertIsNone(row["clone_key"])
                self.assertIsNone(row["comment_id"])

    def test_retry_after_a_failure_does_not_repeat_completed_steps(self):
        """The live run posted a comment, then the clone failed. A retry must
        resume, not comment twice."""
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                first = FakeWriter(fail_on="clone")
                E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                   writer=first, reader=FakeReader())
                self.assertEqual([c[0] for c in first.calls], ["comment", "clone"])
                self.assertEqual(led.get(proposal.ticket)["comment_id"], "c-100")

                second = FakeWriter()
                result = E.execute_proposal(proposal.proposal_id, execute=True,
                                            ledger=led, writer=second,
                                            reader=FakeReader())
                self.assertTrue(result.ok)
                self.assertNotIn("comment", [c[0] for c in second.calls])
                self.assertEqual(result.comment_id, "c-100")
                self.assertEqual(led.get(proposal.ticket)["state"], L.EXECUTED)

    def test_undo_refuses_when_nothing_was_executed(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                with self.assertRaises(E.ExecutionRefused):
                    E.undo(proposal.ticket, execute=True, ledger=led,
                           writer=FakeWriter())

    def test_writer_flag_must_agree_with_execute_flag(self):
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                with self.assertRaises(E.ExecutionRefused):
                    E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                       writer=FakeWriter(execute=False),
                                       reader=FakeReader())


if __name__ == "__main__":
    unittest.main()
