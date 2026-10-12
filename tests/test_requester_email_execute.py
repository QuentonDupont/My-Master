import unittest
from unittest.mock import patch

from core import config, execute as E, ledger as L, proposals as P
from tests.helpers import sandbox
from tests.test_execute import FakeReader, FakeWriter, approved_proposal


class EmailReader(FakeReader):
    def __init__(self, value=None):
        self.value = value

    def issue(self, key, fields):
        return {"fields": {fields: self.value}}


class MissingEmailFieldReader(EmailReader):
    def issue(self, key, fields):
        return {"key": key}


class EmailWriter(FakeWriter):
    def set_requester_email(self, key, field_id, value):
        self._record("requester_email", key=key, field_id=field_id, value=value)


class EmailExecuteTests(unittest.TestCase):
    def prepare(self, led):
        p = approved_proposal(led, "ANSWERABLE")
        field_id = config.boards()["intake"]["requester_email_field"]
        p.requester = P.Requester("owner@example.com", "sheet", "high", {"jira_ticket": p.ticket, "email": "owner@example.com"})
        p.requester_email_update = P.RequesterEmailUpdate(field_id, None, "owner@example.com")
        P.save(p)
        return p

    def test_approved_email_update_and_undo(self):
        with sandbox(), L.Ledger() as led:
            p = self.prepare(led)
            writer = EmailWriter()
            result = E.execute_proposal(p.proposal_id, execute=True, ledger=led, reader=EmailReader(), writer=writer)
            self.assertTrue(result.ok)
            self.assertEqual([c[0] for c in writer.calls], ["comment", "requester_email"])
            with patch("core.execute.JiraReadClient", return_value=EmailReader("owner@example.com")):
                undone = E.undo(p.ticket, execute=True, ledger=led, writer=writer)
            self.assertTrue(undone.ok)
            self.assertIn(("requester_email", {"key": p.ticket, "field_id": p.requester_email_update.field_id, "value": None}), writer.calls)

    def test_changed_email_refuses_before_comment(self):
        with sandbox(), L.Ledger() as led:
            p = self.prepare(led)
            writer = EmailWriter()
            with self.assertRaises(E.ExecutionRefused):
                E.execute_proposal(p.proposal_id, execute=True, ledger=led, reader=EmailReader("other@example.com"), writer=writer)
            self.assertEqual(writer.calls, [])

    def test_omitted_empty_email_field_is_treated_as_unset(self):
        with sandbox(), L.Ledger() as led:
            p = self.prepare(led)
            writer = EmailWriter()
            result = E.execute_proposal(p.proposal_id, execute=True, ledger=led,
                                        reader=MissingEmailFieldReader(), writer=writer)
            self.assertTrue(result.ok)
            self.assertEqual([c[0] for c in writer.calls], ["comment", "requester_email"])

    def test_medium_match_and_overwrite_are_invalid(self):
        with sandbox(), L.Ledger() as led:
            p = self.prepare(led)
            p.requester.confidence = "medium"
            p.requester_email_update.previous = "other@example.com"
            problems = P.validate(p.to_dict())
            self.assertTrue(any("exact sheet" in s for s in problems))
            self.assertTrue(any("overwrite" in s for s in problems))


if __name__ == "__main__":
    unittest.main()
