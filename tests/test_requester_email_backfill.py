import unittest
from core import config, execute as E, ledger as L
from tests.helpers import sandbox
from tests.test_requester_email_execute import EmailReader, EmailWriter
from tests.test_execute import approved_proposal


class EmailBackfillTests(unittest.TestCase):
    def test_description_fallback_preserves_content_and_rejects_concurrent_edit(self):
        class DescriptionWriter(EmailWriter):
            def set_description(self,key,value):
                self._record('description',key=key,value=value)
        with sandbox(), L.Ledger() as led:
            original='https://drive.google.com/file/d/example/view\nExisting details'
            change={**self.change(),'field_id':'description','previous':original}
            writer=DescriptionWriter()
            E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader(original),writer=writer)
            expected='Requester email: submitter@example.com\n\n'+original
            self.assertEqual(writer.calls,[('description',{'key':change['ticket'],'value':expected})])
            E.undo_requester_email_backfill(change,execute=True,ledger=led,reader=EmailReader(expected),writer=writer)
            self.assertEqual(writer.calls[-1][1]['value'],original)
            with self.assertRaises(E.ExecutionRefused):
                E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader('Someone edited the description'),writer=writer)

    def change(self):
        return {'ticket':'PESD1-11274','field_id':config.boards()['intake']['requester_email_field'],
                'value':'submitter@example.com','previous':None,'decision':'approve',
                'approval_reason':'User requested requester email repair',
                'source':'https://docs.google.com/spreadsheets/d/example/edit','source_row':10}

    def test_field_only_repair_preserves_executed_triage_and_supports_undo(self):
        with sandbox(), L.Ledger() as led:
            p = approved_proposal(led, 'ANSWERABLE')
            led.transition(p.ticket, L.EXECUTED)
            change = self.change(); change['ticket'] = p.ticket
            before = led.get(p.ticket)
            writer = EmailWriter()
            self.assertTrue(E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader(),writer=writer)['ok'])
            self.assertEqual(led.get(p.ticket),before)
            self.assertEqual([c[0] for c in writer.calls],['requester_email'])
            self.assertTrue(E.undo_requester_email_backfill(change,execute=True,ledger=led,reader=EmailReader(change['value']),writer=writer)['ok'])
            self.assertIsNone(writer.calls[-1][1]['value'])

    def test_refuses_unapproved_conflicting_and_out_of_scope_changes(self):
        with sandbox(), L.Ledger() as led:
            for overrides,current in [({'decision':'pending'},None),({},'other@example.com'),({'ticket':'PRDT-11274'},None),({'previous':'old@example.com'},None)]:
                writer=EmailWriter(); change={**self.change(),**overrides}
                with self.assertRaises(E.ExecutionRefused):
                    E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader(current),writer=writer)
                self.assertEqual(writer.calls,[])

    def test_idempotent_and_undo_preserves_later_edit(self):
        with sandbox(), L.Ledger() as led:
            change=self.change(); writer=EmailWriter()
            r=E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader(change['value']),writer=writer)
            self.assertEqual(r['skipped'],'already set'); self.assertEqual(writer.calls,[])
            E.backfill_requester_email(change,execute=True,ledger=led,reader=EmailReader(),writer=writer)
            with self.assertRaises(E.ExecutionRefused):
                E.undo_requester_email_backfill(change,execute=True,ledger=led,reader=EmailReader('new@example.com'),writer=writer)
            self.assertEqual(len(writer.calls),1)

    def test_dry_run_does_not_authorize_undo(self):
        with sandbox(), L.Ledger() as led:
            change=self.change(); writer=EmailWriter(execute=False)
            r=E.backfill_requester_email(change,ledger=led,reader=EmailReader(),writer=writer)
            self.assertTrue(r['dry_run'])
            with self.assertRaises(E.ExecutionRefused):
                E.undo_requester_email_backfill(change,execute=True,ledger=led,reader=EmailReader(change['value']),writer=EmailWriter())
