"""The poller: channel history in, the same proposals out, nothing posted.

The poller is the only part of the Slack path that runs unattended, so these
tests care less about wording than about the three things that would hurt if
they broke: it must not work the same thread twice, it must not bypass the
never-touch gate, and it must not be able to post.
"""
import json
import pathlib
import unittest

from agents.slack_leader import poller as poller_mod
from core import slack_proposals as SP
from tests.helpers import FIXTURES, build_corpus, sandbox

HISTORY = FIXTURES / "slack_history.json"


def reader():
    return poller_mod.FileSlackReader(HISTORY)


def poll(**kwargs):
    kwargs.setdefault("first_lookback_s", 10 ** 9)
    rdr = reader()
    return poller_mod.poll_once(rdr, rdr.bot_user_id, **kwargs)


class DiscoveryTests(unittest.TestCase):
    def test_only_mentions_become_events(self):
        with sandbox():
            events, _ = poller_mod.discover(reader(), {}, "UBOT",
                                            first_lookback_s=10 ** 9)
            channels = sorted(e["channel"] for e in events)
            self.assertEqual(channels, ["C0CS", "C0MERCH", "D0SABARI"])

    def test_chatter_without_a_mention_is_ignored(self):
        with sandbox():
            events, _ = poller_mod.discover(reader(), {}, "UBOT",
                                            first_lookback_s=10 ** 9)
            self.assertNotIn("C0QUIET", [e["channel"] for e in events])

    def test_the_bot_never_answers_itself(self):
        """C0QUIET holds a message from UBOT that mentions UBOT."""
        with sandbox():
            events, _ = poller_mod.discover(reader(), {}, "UBOT",
                                            first_lookback_s=10 ** 9)
            self.assertEqual([e for e in events if e["user"] == "UBOT"], [])

    def test_cursor_advances_past_every_message_read(self):
        with sandbox():
            _, cursors = poller_mod.discover(reader(), {}, "UBOT",
                                             first_lookback_s=10 ** 9)
            self.assertEqual(cursors["C0MERCH"], "1726400100.002")
            self.assertEqual(cursors["C0QUIET"], "1726400400.005")

    def test_a_cursor_suppresses_what_was_already_seen(self):
        with sandbox():
            events, _ = poller_mod.discover(
                reader(), {"C0MERCH": "1726400100.002", "C0CS": "1726400200.003",
                           "D0SABARI": "1726400500.006"},
                "UBOT", first_lookback_s=10 ** 9)
            self.assertEqual(events, [])

    def test_the_first_sweep_respects_the_lookback_window(self):
        """Fixture timestamps are old; a short lookback must find nothing."""
        with sandbox():
            events, _ = poller_mod.discover(reader(), {}, "UBOT",
                                            first_lookback_s=60)
            self.assertEqual(events, [])

    def test_one_unreadable_channel_does_not_stop_the_sweep(self):
        class Flaky(poller_mod.FileSlackReader):
            def history(self, channel, oldest=None, limit=50):
                if channel == "C0MERCH":
                    raise RuntimeError("channel_not_found")
                return super().history(channel, oldest=oldest, limit=limit)

        with sandbox():
            events, _ = poller_mod.discover(Flaky(HISTORY), {}, "UBOT",
                                            first_lookback_s=10 ** 9)
            self.assertEqual([e["channel"] for e in events], ["C0CS", "D0SABARI"])


class PollOnceTests(unittest.TestCase):
    def test_a_polled_mention_becomes_a_proposal(self):
        with sandbox():
            build_corpus()
            summary = poll()
            self.assertEqual(summary["events"], 3)
            self.assertEqual(summary["claimed"], 3)
            proposals = SP.load_all()
            self.assertEqual(len(proposals), 3)
            for proposal in proposals:
                self.assertEqual(SP.validate(proposal.to_dict()), [],
                                 proposal.proposal_id)

    def test_a_refund_still_escalates_with_no_reply(self):
        """The poller must not become a way around the never-touch gate."""
        with sandbox():
            build_corpus()
            poll()
            refund = next(p for p in SP.load_all() if p.channel == "C0CS")
            self.assertEqual(refund.kind, SP.ESCALATE)
            self.assertEqual(refund.proposed_reply, "")
            self.assertTrue(any(f.startswith("never_touch") for f in refund.flags))

    def test_polling_twice_does_not_propose_twice(self):
        with sandbox():
            build_corpus()
            first = poll()
            second = poll()
            self.assertEqual(first["claimed"], 3)
            self.assertEqual(second["events"], 0)
            self.assertEqual(len(SP.load_all()), 3)

    def test_the_ledger_still_guards_when_the_cursor_is_lost(self):
        with sandbox():
            build_corpus()
            poll()
            poller_mod.cursor_path().unlink()
            again = poll()
            self.assertEqual(again["events"], 3)      # re-read from Slack
            self.assertEqual(again["claimed"], 0)     # but the ledger refuses
            self.assertEqual(len(SP.load_all()), 3)

    def test_the_cursor_survives_a_round_trip(self):
        with sandbox():
            build_corpus()
            poll()
            saved = json.loads(poller_mod.cursor_path().read_text())
            self.assertEqual(saved["channels"]["C0MERCH"], "1726400100.002")
            self.assertEqual(poller_mod.load_cursors()["C0CS"], "1726400200.003")

    def test_no_commit_leaves_the_cursor_alone(self):
        with sandbox():
            build_corpus()
            poll(commit_cursor=False)
            self.assertFalse(poller_mod.cursor_path().exists())


class SafetyTests(unittest.TestCase):
    def test_the_poller_has_no_write_client(self):
        source = (poller_mod.__file__)
        text = pathlib.Path(source).read_text(encoding="utf-8")
        self.assertNotIn("SlackWriteClient", text)
        self.assertNotIn("post_reply", text)

    def test_the_offline_reader_cannot_post(self):
        rdr = reader()
        for forbidden in ("post_reply", "delete_message", "add_reaction"):
            self.assertFalse(hasattr(rdr, forbidden), forbidden)


if __name__ == "__main__":
    unittest.main()


class ChannelScopeTests(unittest.TestCase):
    """An explicit channel list keeps the Slack app on channels:history alone."""

    def test_an_explicit_list_is_used_verbatim(self):
        self.assertEqual(
            poller_mod.resolve_channels(reader(), ["C0MERCH", " C0CS "]),
            [{"id": "C0MERCH"}, {"id": "C0CS"}])

    def test_an_explicit_list_never_asks_slack_to_list_channels(self):
        class NoListing(poller_mod.FileSlackReader):
            def conversations(self):
                raise AssertionError("conversations() needs channels:read")

        with sandbox():
            events, _ = poller_mod.discover(NoListing(HISTORY), {}, "UBOT",
                                            channels=["C0CS"],
                                            first_lookback_s=10 ** 9)
            self.assertEqual([e["channel"] for e in events], ["C0CS"])

    def test_blank_entries_are_dropped(self):
        self.assertEqual(poller_mod.resolve_channels(reader(), ["", "  "]),
                         [])

    def test_no_list_falls_back_to_discovery(self):
        chans = poller_mod.resolve_channels(reader(), None)
        self.assertEqual([c["id"] for c in chans],
                         ["C0MERCH", "C0CS", "C0QUIET", "D0SABARI"])


class CliTests(unittest.TestCase):
    def test_absent_channels_flag_sweeps_everything_not_nothing(self):
        """"".split(",") is [""] — that must not mean "sweep no channels"."""
        with sandbox():
            build_corpus()
            rc = poller_mod.main(["once", "--history", str(HISTORY),
                                  "--lookback", "1000000000", "--no-commit"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(SP.load_all()), 3)

    def test_channels_flag_narrows_the_sweep(self):
        with sandbox():
            build_corpus()
            poller_mod.main(["once", "--history", str(HISTORY),
                             "--channels", "C0CS",
                             "--lookback", "1000000000", "--no-commit"])
            self.assertEqual([p.channel for p in SP.load_all()], ["C0CS"])


class CredentialSplitTests(unittest.TestCase):
    """Reads may act as the person; writes never may."""

    def test_read_client_prefers_the_user_token(self):
        from core import config, slack_client as SC
        saved = config.env
        config.env = lambda n, d=None, **k: {"SLACK_USER_TOKEN": "xoxp-me",
                                             "SLACK_BOT_TOKEN": "xoxb-app"}.get(n, d)
        try:
            client = SC.SlackReadClient()
            self.assertEqual(client.token, "xoxp-me")
            self.assertTrue(client.acting_as_user)
        finally:
            config.env = saved

    def test_read_client_falls_back_to_the_bot_token(self):
        from core import config, slack_client as SC
        saved = config.env
        config.env = lambda n, d=None, **k: {"SLACK_BOT_TOKEN": "xoxb-app"}.get(n, d)
        try:
            client = SC.SlackReadClient()
            self.assertEqual(client.token, "xoxb-app")
            self.assertFalse(client.acting_as_user)
        finally:
            config.env = saved

    def test_write_client_refuses_a_user_token(self):
        from core import slack_client as SC
        with self.assertRaises(SC.SlackError) as caught:
            SC.SlackWriteClient(token="xoxp-me", execute=True)
        self.assertIn("user token", str(caught.exception))

    def test_write_client_accepts_the_bot_token(self):
        from core import slack_client as SC
        self.assertFalse(SC.SlackWriteClient(token="xoxb-app").execute)

    def test_dms_are_in_the_swept_conversation_types(self):
        import inspect
        from core import slack_client as SC
        sig = inspect.signature(SC.SlackReadClient.conversations)
        types = sig.parameters["types"].default
        self.assertIn("im", types.split(","))
        self.assertIn("mpim", types.split(","))


class DirectMessageTests(unittest.TestCase):
    """A bot cannot see a person's DMs; a user token can, so the poller must."""

    def test_a_dm_mention_is_swept_like_any_channel(self):
        with sandbox():
            events, _ = poller_mod.discover(reader(), {}, "UBOT",
                                            first_lookback_s=10 ** 9)
            self.assertIn("D0SABARI", [e["channel"] for e in events])

    def test_a_dm_mention_becomes_a_proposal(self):
        with sandbox():
            build_corpus()
            summary = poll()
            self.assertEqual(summary["events"], 3)
            self.assertIn("D0SABARI", [p.channel for p in SP.load_all()])
