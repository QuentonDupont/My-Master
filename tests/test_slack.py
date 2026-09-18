"""The Slack Leader: mentions in, reply proposals out. Nothing is ever posted."""
import inspect
import json
import unittest

from agents.slack_leader import leader as leader_mod, worker as worker_mod
from core import ledger as L, slack_execute as SE, slack_proposals as SP
from tests.helpers import FIXTURES, build_corpus, sandbox


class FakeSlackWriter:
    def __init__(self, execute=True, fail=False):
        self.execute = execute
        self.fail = fail
        self.calls = []

    def post_reply(self, channel, thread_ts, text):
        self.calls.append(("post_reply", channel, thread_ts, text))
        if self.fail:
            raise RuntimeError("slack is down")
        return {"ts": "1726399999.999", "channel": channel}

    def delete_message(self, channel, ts):
        self.calls.append(("delete_message", channel, ts))
        return {"ok": True}


def run_mentions():
    return leader_mod.run(leader_mod.FileMentionSource(FIXTURES / "mentions.json"),
                          bot_user_id="UBOT")


class SlackWorkerTests(unittest.TestCase):
    def test_a_mention_becomes_a_proposal_not_a_reply(self):
        with sandbox():
            build_corpus()
            summary = run_mentions()
            self.assertEqual(summary["claimed"], 4)
            self.assertTrue(SP.load_all())
            for proposal in SP.load_all():
                self.assertEqual(SP.validate(proposal.to_dict()), [],
                                 proposal.proposal_id)

    def test_a_refund_question_gets_no_reply_at_all(self):
        with sandbox():
            build_corpus()
            run_mentions()
            refund = next(p for p in SP.load_all() if p.channel == "C0CS")
            self.assertEqual(refund.kind, SP.ESCALATE)
            self.assertEqual(refund.proposed_reply, "")
            self.assertTrue(any(f.startswith("never_touch") for f in refund.flags))

    def test_a_vague_mention_is_escalated(self):
        with sandbox():
            build_corpus()
            run_mentions()
            vague = next(p for p in SP.load_all() if p.channel == "C0RANDOM")
            self.assertEqual(vague.kind, SP.ESCALATE)
            self.assertIn("sanity_gate", vague.flags)

    def test_a_known_question_is_answered_with_evidence(self):
        with sandbox():
            build_corpus()
            run_mentions()
            known = next(p for p in SP.load_all() if p.channel == "C0MERCH")
            self.assertIn(known.kind, (SP.ANSWER, SP.POINT_AT_TICKET))
            self.assertTrue(known.proposed_reply)
            self.assertTrue(known.evidence)

    def test_a_thread_is_not_answered_twice(self):
        with sandbox():
            build_corpus()
            run_mentions()
            again = run_mentions()
            self.assertEqual(again["claimed"], 0)
            self.assertEqual(len(again["skipped"]), 4)

    def test_slack_markup_is_stripped_before_the_gates_see_it(self):
        self.assertEqual(
            worker_mod.clean("<@U123> see <https://x.test/a|this> please"),
            "see https://x.test/a please")

    def test_the_worker_cannot_post(self):
        source = inspect.getsource(worker_mod)
        self.assertNotIn("SlackWriteClient", source)
        self.assertNotIn("post_reply", source)


class SlackExecuteTests(unittest.TestCase):
    def _approved(self, led):
        slack_led = L.SlackLedger(led)
        run_mentions()
        proposal = next(p for p in SP.load_all()
                        if p.kind in (SP.ANSWER, SP.POINT_AT_TICKET, SP.ASK_FOR_TICKET))
        key = slack_led.key(proposal.channel, proposal.thread_ts)
        slack_led.transition(key, L.APPROVED)
        return proposal, key

    def test_posting_requires_approval(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                run_mentions()
                proposal = next(p for p in SP.load_all() if p.proposed_reply)
                with self.assertRaises(SE.ExecutionRefused):
                    SE.execute_reply(proposal.proposal_id, execute=True, ledger=led,
                                     writer=FakeSlackWriter())

    def test_an_escalation_is_never_posted(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                slack_led = L.SlackLedger(led)
                run_mentions()
                escalated = next(p for p in SP.load_all() if p.kind == SP.ESCALATE)
                key = slack_led.key(escalated.channel, escalated.thread_ts)
                with self.assertRaises(SE.ExecutionRefused):
                    SE.execute_reply(escalated.proposal_id, execute=True, ledger=led,
                                     writer=FakeSlackWriter())

    def test_approved_reply_posts_once_and_records_it(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                proposal, key = self._approved(led)
                writer = FakeSlackWriter()
                out = SE.execute_reply(proposal.proposal_id, execute=True, ledger=led,
                                       writer=writer)
                self.assertTrue(out["ok"])
                self.assertEqual(len(writer.calls), 1)
                row = L.SlackLedger(led).get(key)
                self.assertEqual(row["state"], L.EXECUTED)
                self.assertEqual(row["reply_ts"], "1726399999.999")
                with self.assertRaises(SE.ExecutionRefused):
                    SE.execute_reply(proposal.proposal_id, execute=True, ledger=led,
                                     writer=FakeSlackWriter())

    def test_dry_run_posts_nothing(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                proposal, key = self._approved(led)
                writer = FakeSlackWriter(execute=False)
                out = SE.execute_reply(proposal.proposal_id, execute=False,
                                       ledger=led, writer=writer)
                self.assertTrue(out["dry_run"])
                self.assertEqual(L.SlackLedger(led).get(key)["state"], L.APPROVED)

    def test_undo_deletes_the_reply(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                proposal, key = self._approved(led)
                SE.execute_reply(proposal.proposal_id, execute=True, ledger=led,
                                 writer=FakeSlackWriter())
                writer = FakeSlackWriter()
                out = SE.undo_reply(key, execute=True, ledger=led, writer=writer)
                self.assertTrue(out["ok"])
                self.assertEqual(writer.calls[0][0], "delete_message")
                self.assertEqual(L.SlackLedger(led).get(key)["state"], L.ROLLED_BACK)


class SlackProposalValidationTests(unittest.TestCase):
    def base(self, **kw):
        data = {"proposal_id": "s_0001", "channel": "C1", "channel_name": "ops",
                "thread_ts": "1.1", "permalink": "", "asked_by": "U1",
                "asked_by_name": "A", "question_restated": "what is x",
                "kind": SP.ANSWER, "confidence": 0.7, "proposed_reply": "here you go",
                "evidence": [], "flags": []}
        data.update(kw)
        return data

    def test_escalate_carries_no_reply(self):
        problems = SP.validate(self.base(kind=SP.ESCALATE, proposed_reply="hello"))
        self.assertTrue(any("stays silent" in p for p in problems))

    def test_a_reply_may_not_name_a_board_out_of_scope(self):
        problems = SP.validate(self.base(proposed_reply="see BUSK-12 for this"))
        self.assertTrue(any("outside the boards in scope" in p for p in problems))

    def test_a_reply_may_name_pesd1(self):
        self.assertEqual(SP.validate(self.base(proposed_reply="see PESD1-1")), [])

    def test_a_credential_never_reaches_a_reply(self):
        from core import config
        config._secrets.add("xoxb-not-a-real-token-value")
        try:
            problems = SP.validate(self.base(
                proposed_reply="the token is xoxb-not-a-real-token-value"))
            self.assertTrue(any("credential" in p for p in problems))
        finally:
            config._secrets.discard("xoxb-not-a-real-token-value")


if __name__ == "__main__":
    unittest.main()
