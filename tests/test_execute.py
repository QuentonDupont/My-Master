import contextlib
import unittest

from core import execute as E, ledger as L, proposals as P
from tests.helpers import sandbox
from tests.test_proposals import base, clone


@contextlib.contextmanager
def _patched_boards(data: dict):
    """Temporarily replace config.boards() with a fixed dict, restoring
    whatever it was (sandbox()'s fixture loader, typically) on exit."""
    from core import config

    saved = config.boards
    config.boards = lambda: data
    try:
        yield
    finally:
        config.boards = saved


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

    def creatable_fields(self, project, issue_type):
        # Empty means "no screen metadata available" to screen_fields(), which
        # then keeps every field rather than filtering any of them out.
        return {}


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

    def test_undo_never_deletes_a_ticket(self):
        """The board owner's rule: the system does not delete tickets. A clone
        that should not exist is unassigned, unlinked, and recommended for
        closing — the human decides."""
        from core import recommendations
        with sandbox():
            with L.Ledger() as led:
                proposal = approved_proposal(led)
                E.execute_proposal(proposal.proposal_id, execute=True, ledger=led,
                                   writer=FakeWriter(), reader=FakeReader())
                undo_writer = FakeWriter()
                E.undo(proposal.ticket, execute=True, ledger=led, writer=undo_writer)
                self.assertNotIn("delete_issue", [c[0] for c in undo_writer.calls])
                open_items = recommendations.open_items()
                self.assertEqual(len(open_items), 1)
                self.assertEqual(open_items[0]["ticket"], "PRDT-999")
                self.assertEqual(open_items[0]["action"], recommendations.CLOSE)

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
                                         "delete_comment"])
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


class CreateDevTicketTests(unittest.TestCase):
    """core.execute.create_dev_ticket — a standalone, explicitly
    human-directed ticket, not a triage clone. Real use: PRDT-11591.

    tests/fixtures/boards.yml deliberately disables the whole epic mechanism
    (epic_link_field: "") so other tests aren't tripped by it — these tests
    need it ON to test anything meaningful about it, so they patch
    config.boards() directly rather than relying on the ambient fixture.
    """

    def _with_epic_config(self, default_epic: str = "PRDT-11563"):
        from core import config

        base = config.boards()
        patched = dict(base)
        patched["development"] = dict(base["development"],
                                      epic_link_field="customfield_10008",
                                      default_epic=default_epic)
        return _patched_boards(patched)

    def test_dry_run_carries_labels_priority_and_the_configured_epic(self):
        with sandbox(), self._with_epic_config():
            writer = FakeWriter()
            out = E.create_dev_ticket(
                "a real ask", "a real description", assignee="Wallop",
                labels=["business_continuity", "tech_ops"], priority="Medium",
                execute=False, writer=writer, reader=FakeReader())
            self.assertEqual(out["epic"], "PRDT-11563")
            # dry run never resolves or assigns anyone, even with assignee set
            self.assertIsNone(out["account_id"])
            self.assertEqual([c[0] for c in writer.calls], ["clone"])
            # dev.issue_type from the fixture, not hardcoded — this is
            # exercising create_dev_ticket's mechanics, not asserting a
            # specific board's configured issue type.
            from core import config

            self.assertEqual(writer.calls[0][1]["issue_type"],
                             config.dev_issue_type())

    def test_an_explicit_epic_overrides_the_default(self):
        with sandbox(), self._with_epic_config(default_epic="PRDT-1"):
            out = E.create_dev_ticket(
                "ask", "desc", epic="PRDT-9999", execute=False,
                writer=FakeWriter(), reader=FakeReader())
            self.assertEqual(out["epic"], "PRDT-9999")

    def test_no_epic_and_no_default_is_refused(self):
        with sandbox(), self._with_epic_config(default_epic=""):
            with self.assertRaises(E.ExecutionRefused):
                E.create_dev_ticket("ask", "desc", execute=False,
                                    writer=FakeWriter(), reader=FakeReader())

    def test_execute_creates_and_assigns(self):
        with sandbox(), self._with_epic_config():
            writer = FakeWriter()
            out = E.create_dev_ticket(
                "ask", "desc", assignee="Wallop", labels=["tech_ops"],
                execute=True, writer=writer, reader=FakeReader())
            self.assertEqual(out["key"], "PRDT-999")  # FakeWriter's fixed key
            self.assertEqual(out["account_id"], "acc-1")
            names = [c[0] for c in writer.calls]
            self.assertEqual(names, ["clone", "assign"])

    def test_no_epic_mechanism_configured_creates_without_one(self):
        """The fixture's default state: epic_link_field is empty, so no epic
        is required at all — this must succeed cleanly, not refuse."""
        with sandbox():
            writer = FakeWriter()
            out = E.create_dev_ticket("ask", "desc", execute=False,
                                      writer=writer, reader=FakeReader())
            self.assertEqual([c[0] for c in writer.calls], ["clone"])
            self.assertEqual(out["epic"], "")


if __name__ == "__main__":
    unittest.main()
