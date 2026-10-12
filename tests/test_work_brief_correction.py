import unittest

from core import execute as E, ledger as L
from tests.helpers import sandbox
from tests.test_execute import FakeWriter


class DescriptionReader:
    def __init__(self, writer): self.writer = writer
    def issue(self, key, fields): return {'fields': {'description': self.writer.description}}


class DescriptionWriter(FakeWriter):
    def __init__(self):
        super().__init__()
        self.description = 'Requester email: user@example.com'
    def set_description(self, key, description):
        self._record('description', key=key)
        self.description = description


class WorkBriefCorrectionTests(unittest.TestCase):
    def test_correction_and_undo_preserve_concurrent_changes(self):
        with sandbox(), L.Ledger() as led:
            writer = DescriptionWriter(); reader = DescriptionReader(writer)
            change = {'ticket':'PESD1-11305','decision':'approve',
                      'approval_reason':'Board owner requested identifier list',
                      'source':'form row 2257','previous':writer.description,
                      'new_description':'RMA1389971\nRequester email: user@example.com'}
            E.amend_work_brief(change,execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.description,change['new_description'])
            writer.description = 'Someone edited the description'
            with self.assertRaises(E.ExecutionRefused):
                E.undo_work_brief(change,execute=True,ledger=led,reader=reader,writer=writer)
            writer.description = change['new_description']
            E.undo_work_brief(change,execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.description,change['previous'])

    def test_unapproved_change_cannot_write(self):
        with sandbox(), L.Ledger() as led:
            writer = DescriptionWriter(); reader = DescriptionReader(writer)
            with self.assertRaises(E.ExecutionRefused):
                E.amend_work_brief({'ticket':'PESD1-11305','decision':'pending',
                                    'source':'form row 2257','previous':writer.description,
                                    'new_description':'RMA1389971'},
                                   execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.calls,[])
