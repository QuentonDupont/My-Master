import unittest
from core import config, execute as E, ledger as L
from tests.helpers import sandbox
from tests.test_execute import approved_proposal, FakeWriter


class StatusReader:
    def __init__(self, writer): self.writer = writer
    def issue(self, key, fields): return {'fields':{'status':{'name':self.writer.status}}}
    def transitions(self, key):
        options = {'Waiting Support':['To Do'],
                   'Waiting for customer':['Waiting Support'],
                   'To Do':['Waiting for customer']}
        return [{'to':{'name':x}} for x in options.get(self.writer.status,[])]


class ReviewTransitionTests(unittest.TestCase):
    def change(self,ticket):
        return {'ticket':ticket,'from':'Waiting Support','to':'To Do',
                'decision':'approve','approval_reason':'Board owner requested To Do'}

    def test_transition_and_reversible_route_preserve_ledger(self):
        with sandbox(), L.Ledger() as led:
            config.boards()['intake']['open_status']='Waiting Support'
            config.boards()['intake']['dev_transition']='To Do'
            p=approved_proposal(led,'ANSWERABLE')
            before=led.get(p.ticket)
            writer=FakeWriter(); reader=StatusReader(writer); change=self.change(p.ticket)
            writer.status='Waiting Support'
            E.transition_after_review(change,execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.status,'To Do')
            self.assertEqual(led.get(p.ticket),before)
            E.undo_review_transition(change,execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.status,'Waiting Support')
            self.assertEqual([c[1]['to'] for c in writer.calls],['To Do','Waiting for customer','Waiting Support'])

    def test_unapproved_or_changed_state_refuses_write(self):
        with sandbox(), L.Ledger() as led:
            config.boards()['intake']['open_status']='Waiting Support'
            config.boards()['intake']['dev_transition']='To Do'
            p=approved_proposal(led,'ANSWERABLE')
            writer=FakeWriter(); reader=StatusReader(writer)
            change=self.change(p.ticket); change['decision']='pending'
            with self.assertRaises(E.ExecutionRefused):
                E.transition_after_review(change,execute=True,ledger=led,reader=reader,writer=writer)
            change['decision']='approve'; writer.status='Blocked'
            with self.assertRaises(E.ExecutionRefused):
                E.transition_after_review(change,execute=True,ledger=led,reader=reader,writer=writer)
            self.assertEqual(writer.calls,[])
