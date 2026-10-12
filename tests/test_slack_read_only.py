"""Slack is read-only in this repo: the Digital Twin reads, nothing posts.

The Slack Leader and its write client were removed on 1 Oct 2026; the read
client was kept for the Twin on 12 Oct 2026. These tests keep it that way.
"""
import pathlib
import unittest
from unittest.mock import patch

from core import slack_client as S

ROOT = pathlib.Path(__file__).resolve().parent.parent


class SlackReadOnlyTests(unittest.TestCase):
    def test_there_is_no_write_client(self):
        self.assertFalse(hasattr(S, "SlackWriteClient"))

    def test_every_allowed_method_is_a_read(self):
        for m in S.READ_METHODS:
            self.assertFalse(any(w in m for w in
                                 ("post", "delete", "update", "add", "remove",
                                  "join", "invite", "kick", "archive", "set")), m)

    def test_a_write_method_is_refused_before_any_request(self):
        client = S.SlackReadClient(token="xoxb-not-a-real-token")
        with patch("urllib.request.urlopen") as urlopen:
            for method in ("chat.postMessage", "chat.delete", "reactions.add"):
                with self.assertRaises(S.SlackError) as caught:
                    client._call(method, params={"channel": "C1"})
                self.assertIn("only reads", caught.exception.error)
            urlopen.assert_not_called()

    def test_requests_are_gets(self):
        client = S.SlackReadClient(token="xoxb-not-a-real-token")
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake(req, timeout=0):
            seen.append(req.get_method())
            return Resp()

        with patch("urllib.request.urlopen", side_effect=fake):
            client.auth_test()
        self.assertEqual(seen, ["GET"])

    def test_no_module_imports_a_slack_writer(self):
        bad = []
        for path in list((ROOT / "agents").rglob("*.py")) + list((ROOT / "core").rglob("*.py")) \
                + list((ROOT / "flows").rglob("*.py")) + list((ROOT / "tools").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "slack_execute" in text or "SlackWriteClient" in text:
                bad.append(str(path.relative_to(ROOT)))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
