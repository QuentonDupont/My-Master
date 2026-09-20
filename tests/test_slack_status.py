"""A named ticket gets a live answer: where it is, who has it, what it cloned to.

The Historian answers from history. This is the Jira Leader's half — the state
of the requester's own ticket right now — which is what someone tagging the
board owner about "their" ticket is nearly always asking for.
"""
import unittest

from agents.slack_leader import status as status_mod, worker as worker_mod
from core import ledger as L, slack_proposals as SP
from tests.helpers import build_corpus, sandbox


class FakeJira:
    """Read-only stand-in. `fail=True` is the offline / no-token case."""

    def __init__(self, issues=None, fail=False):
        self.issues = issues or {}
        self.fail = fail
        self.calls = []

    def issue(self, key, fields="*all"):
        self.calls.append(key)
        if self.fail:
            raise RuntimeError("jira is unreachable")
        if key not in self.issues:
            raise RuntimeError(f"{key} not found")
        return {"fields": self.issues[key]}


def jira(**issues):
    return FakeJira({k: v for k, v in issues.items()})


ISSUE = {"summary": "Add two new locations in NS",
         "status": {"name": "To Do"},
         "assignee": {"displayName": "Vishal Gaikwad"}}


def mention(text, channel="C0OPS", ts="1789900000.001"):
    return {"channel": channel, "channel_name": "ops-tech", "thread_ts": ts,
            "user": "U100", "user_name": "Warehouse Ops TH",
            "permalink": f"https://pomelo.slack.com/archives/{channel}/p1",
            "messages": [{"ts": ts, "user": "U100", "text": text}]}


def claimed(led, m):
    """The leader claims a thread before a worker may touch it."""
    slack_led = L.SlackLedger(led)
    slack_led.claim(m["channel"], m["thread_ts"],
                    L.thread_hash(m["messages"]))
    return m


def tracked(led, key, clone=None):
    """A PESD1 row in the ticket ledger, optionally already cloned."""
    led.claim(key, "h")
    if clone:
        led.transition(key, L.PROPOSED, proposal_id="p_test")
        led.transition(key, L.APPROVED)
        led.transition(key, L.EXECUTED, clone_key=clone)


class LookupTests(unittest.TestCase):
    def test_jira_fields_are_reported(self):
        with sandbox():
            found = status_mod.lookup(["PESD1-11271"],
                                      reader=jira(**{"PESD1-11271": ISSUE}))
            self.assertEqual(found[0]["status"], "To Do")
            self.assertEqual(found[0]["assignee"], "Vishal Gaikwad")

    def test_a_ticket_out_of_scope_is_never_looked_up(self):
        """BUSK is not an allowed project (CLAUDE.md: PESD1 and PRDT only)."""
        with sandbox():
            reader = jira(**{"PESD1-11271": ISSUE})
            found = status_mod.lookup(["BUSK-2805"], reader=reader)
            self.assertEqual(found, [])
            self.assertEqual(reader.calls, [])

    def test_jira_being_down_does_not_lose_the_ledger_half(self):
        with sandbox() as root:
            with L.Ledger() as led:
                tracked(led, "PESD1-11271", clone="PRDT-11559")
                found = status_mod.lookup(["PESD1-11271"], ledger=led,
                                          reader=FakeJira(fail=True))
            self.assertEqual(found[0]["clone_key"], "PRDT-11559")
            self.assertIn("PRDT-11559", status_mod.sentence(found[0]))

    def test_nothing_known_means_nothing_said(self):
        with sandbox():
            self.assertEqual(status_mod.lookup(["PESD1-99999"],
                                               reader=FakeJira(fail=True)), [])
            self.assertEqual(status_mod.paragraph([]), "")

    def test_at_most_two_tickets_per_reply(self):
        with sandbox():
            issues = {f"PESD1-{n}": ISSUE for n in (1, 2, 3, 4)}
            found = status_mod.lookup(list(issues), reader=jira(**issues))
            self.assertEqual(len(found), status_mod.MAX_TICKETS)

    def test_a_key_repeated_in_a_thread_is_answered_once(self):
        with sandbox():
            found = status_mod.lookup(["PESD1-11271", "PESD1-11271"],
                                      reader=jira(**{"PESD1-11271": ISSUE}))
            self.assertEqual(len(found), 1)

    def test_the_sentence_reads_like_a_person_wrote_it(self):
        line = status_mod.sentence({"key": "PESD1-11271", "status": "To Do",
                                    "assignee": "Vishal Gaikwad"})
        self.assertEqual(line, "PESD1-11271 is *To Do*, with Vishal Gaikwad.")


class WorkerTests(unittest.TestCase):
    def test_asking_about_a_ticket_gets_its_status_back(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                out = worker_mod.process(
                    claimed(led, mention("<@UBOT> any update on PESD1-11271? the stores are "
                            "waiting on the new location")),
                    ledger=led, jira_reader=jira(**{"PESD1-11271": ISSUE}),
                    bot_user_id="UBOT")
            self.assertEqual(out.state, L.PROPOSED)
            proposal = SP.load(out.proposal_id)
            self.assertEqual(proposal.kind, SP.POINT_AT_TICKET)
            self.assertIn("PESD1-11271 is *To Do*", proposal.proposed_reply)
            self.assertIn("Vishal Gaikwad", proposal.proposed_reply)

    def test_the_clone_is_named_so_they_can_follow_the_work(self):
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                tracked(led, "PESD1-11271", clone="PRDT-11559")
                m = claimed(led, mention("<@UBOT> is there any progress on "
                                         "PESD1-11271?"))
                out = worker_mod.process(
                    m,
                    ledger=led, jira_reader=jira(**{"PESD1-11271": ISSUE}),
                    bot_user_id="UBOT")
            self.assertIn("PRDT-11559", SP.load(out.proposal_id).proposed_reply)

    def test_no_jira_reader_still_produces_a_reply(self):
        """The Slack path must not depend on Jira being reachable."""
        with sandbox():
            build_corpus()
            with L.Ledger() as led:
                out = worker_mod.process(
                    claimed(led, mention("<@UBOT> where do I find the daily order report?")),
                    ledger=led, bot_user_id="UBOT")
            self.assertEqual(out.state, L.PROPOSED)
            self.assertTrue(SP.load(out.proposal_id).proposed_reply)

    def test_a_never_touch_thread_never_reaches_a_status_lookup(self):
        with sandbox():
            build_corpus()
            reader = jira(**{"PESD1-11271": ISSUE})
            with L.Ledger() as led:
                out = worker_mod.process(
                    claimed(led, mention("<@UBOT> please process the refund on PESD1-11271 again")),
                    ledger=led, jira_reader=reader, bot_user_id="UBOT")
            self.assertEqual(out.state, L.ESCALATED)
            self.assertEqual(SP.load(out.proposal_id).proposed_reply, "")
            self.assertEqual(reader.calls, [])


if __name__ == "__main__":
    unittest.main()
