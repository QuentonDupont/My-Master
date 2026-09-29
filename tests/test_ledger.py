import unittest

from tests.helpers import sandbox


class LedgerTests(unittest.TestCase):
    def test_state_machine_refuses_illegal_moves(self):
        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                led.upsert_new("PESD1-1", "h1")
                with self.assertRaises(L.LedgerError):
                    led.transition("PESD1-1", L.EXECUTED)      # NEW -> EXECUTED
                led.claim("PESD1-1", "h1")
                led.transition("PESD1-1", L.PROPOSED, proposal_id="p_0001")
                with self.assertRaises(L.LedgerError):
                    led.transition("PESD1-1", L.EXECUTED)      # must be approved first
                led.transition("PESD1-1", L.APPROVED)
                led.transition("PESD1-1", L.EXECUTED, clone_key="PRDT-1")
                self.assertTrue(led.is_executed("PESD1-1"))

    def test_executed_is_terminal_except_rollback(self):
        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                led.upsert_new("PESD1-2", "h")
                led.claim("PESD1-2", "h")
                led.transition("PESD1-2", L.PROPOSED)
                led.transition("PESD1-2", L.APPROVED)
                led.transition("PESD1-2", L.EXECUTED)
                for state in (L.EXECUTED, L.APPROVED, L.PROPOSED, L.CLAIMED):
                    with self.assertRaises(L.LedgerError):
                        led.transition("PESD1-2", state)
                led.transition("PESD1-2", L.ROLLED_BACK)

    def test_reprocessing_gate(self):
        """Invariant 4: only a changed hash AND the open status re-opens a ticket."""
        from core import config, ledger as L
        with sandbox():
            open_status = config.boards()["intake"]["open_status"]
            with L.Ledger() as led:
                self.assertTrue(led.should_process("PESD1-3", "h1", open_status)[0])
                led.claim("PESD1-3", "h1")
                led.transition("PESD1-3", L.PROPOSED)
                led.transition("PESD1-3", L.APPROVED)
                led.transition("PESD1-3", L.EXECUTED)

                self.assertFalse(led.should_process("PESD1-3", "h1", open_status)[0])
                self.assertFalse(led.should_process("PESD1-3", "h2", "In Development")[0])
                ok, reason = led.should_process("PESD1-3", "h2", open_status)
                self.assertTrue(ok, reason)

    def test_in_flight_tickets_are_not_reclaimed(self):
        from core import config, ledger as L
        with sandbox():
            open_status = config.boards()["intake"]["open_status"]
            with L.Ledger() as led:
                led.claim("PESD1-4", "h1")
                self.assertFalse(led.should_process("PESD1-4", "h2", open_status)[0])
                self.assertFalse(led.claim("PESD1-4", "h2"))

    def test_released_ticket_is_retried(self):
        """A worker that crashes releases the ticket; it must come back round."""
        from core import config, ledger as L
        with sandbox():
            open_status = config.boards()["intake"]["open_status"]
            with L.Ledger() as led:
                led.claim("PESD1-6", "h1")
                led.transition("PESD1-6", L.NEW, last_error="worker: boom")
                ok, reason = led.should_process("PESD1-6", "h1", open_status)
                self.assertTrue(ok, reason)
                self.assertTrue(led.claim("PESD1-6", "h1"))

    def test_content_hash_tracks_comment_count(self):
        from core.ledger import content_hash
        a = content_hash("s", "d", 1)
        self.assertEqual(a, content_hash("s", "d", 1))
        self.assertNotEqual(a, content_hash("s", "d", 2))
        self.assertNotEqual(a, content_hash("s", "d2", 1))

    def test_transition_rejects_unknown_fields(self):
        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                led.upsert_new("PESD1-5", "h")
                with self.assertRaises(L.LedgerError):
                    led.transition("PESD1-5", L.CLAIMED, state="EXECUTED")

    def test_stale_claim_is_reclaimed_to_new(self):
        """A crashed worker's claim must not be permanent — should_process()
        blocks CLAIMED unconditionally, so this is the only way out."""
        import datetime as dt

        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                led.claim("PESD1-7", "h1")
                cutoff = (dt.datetime.now(dt.timezone.utc)
                         - dt.timedelta(minutes=L.STALE_CLAIM_MINUTES + 5)
                         ).isoformat(timespec="seconds")
                led.conn.execute(
                    "UPDATE ledger SET last_processed = ? WHERE ticket_key = ?",
                    (cutoff, "PESD1-7"))
                reclaimed = led.reclaim_stale_claims()
                self.assertEqual(reclaimed, ["PESD1-7"])
                row = led.get("PESD1-7")
                self.assertEqual(row["state"], L.NEW)
                self.assertIn("presumed dead", row["last_error"])

    def test_fresh_claim_is_not_reclaimed(self):
        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                led.claim("PESD1-8", "h1")
                self.assertEqual(led.reclaim_stale_claims(), [])
                self.assertEqual(led.get("PESD1-8")["state"], L.CLAIMED)

    def test_slack_thread_stale_claim_is_reclaimed(self):
        import datetime as dt

        from core import ledger as L
        with sandbox():
            with L.Ledger() as led:
                sl = L.SlackLedger(led)
                sl.claim("C1", "1.1", "h1")
                key = sl.key("C1", "1.1")
                cutoff = (dt.datetime.now(dt.timezone.utc)
                         - dt.timedelta(minutes=L.STALE_CLAIM_MINUTES + 5)
                         ).isoformat(timespec="seconds")
                led.conn.execute(
                    "UPDATE slack_threads SET last_processed = ? WHERE thread_key = ?",
                    (cutoff, key))
                self.assertEqual(sl.reclaim_stale_claims(), [key])
                self.assertEqual(sl.get(key)["state"], L.NEW)


if __name__ == "__main__":
    unittest.main()
