"""tools.panel._health() — the numbers that used to need a CLI command each.

Every field is meant to degrade to null/empty rather than raise, so a health
check that fails is never the thing that breaks the page.
"""
import datetime as dt
import unittest

from core import config, ledger as L
from tests.helpers import sandbox


def _write_heartbeat(name: str, minutes_ago: float) -> None:
    ts = (dt.datetime.now(dt.timezone.utc)
          - dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    path = config.LOG_DIR / f".heartbeat_{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ts, encoding="utf-8")


class PanelHealthTests(unittest.TestCase):
    def test_missing_heartbeat_is_null_not_an_error(self):
        import tools.panel as panel_mod
        with sandbox():
            data = panel_mod._health()
            self.assertIsNone(data["board_tick_minutes_ago"])
            self.assertIsNone(data["corpus_refresh_minutes_ago"])

    def test_a_recorded_heartbeat_reports_its_age(self):
        import tools.panel as panel_mod
        with sandbox():
            _write_heartbeat("board_tick", 12)
            data = panel_mod._health()
            self.assertAlmostEqual(data["board_tick_minutes_ago"], 12, delta=1)

    def test_active_alert_markers_are_listed(self):
        import tools.panel as panel_mod
        with sandbox():
            marker_dir = config.LOG_DIR / ".markers"
            marker_dir.mkdir(parents=True)
            (marker_dir / "board_tick_sweep").touch()
            data = panel_mod._health()
            self.assertEqual(data["active_alerts"], ["board_tick_sweep"])

    def test_no_marker_dir_means_no_alerts_not_an_error(self):
        import tools.panel as panel_mod
        with sandbox():
            self.assertEqual(panel_mod._health()["active_alerts"], [])

    def test_a_freshly_approved_proposal_is_counted_but_not_aging(self):
        import tools.panel as panel_mod
        with sandbox():
            with L.Ledger() as led:
                led.claim("PESD1-1", "h1")
                led.transition("PESD1-1", L.PROPOSED, proposal_id="p_0001")
                led.transition("PESD1-1", L.APPROVED)
            data = panel_mod._health()
            self.assertEqual(data["approved_not_executed"], 1)
            self.assertEqual(data["approved_and_aging"], [])

    def test_an_old_approval_is_flagged_as_aging(self):
        import tools.panel as panel_mod
        with sandbox():
            with L.Ledger() as led:
                led.claim("PESD1-2", "h1")
                led.transition("PESD1-2", L.PROPOSED, proposal_id="p_0002")
                led.transition("PESD1-2", L.APPROVED)
                old = (dt.datetime.now(dt.timezone.utc)
                      - dt.timedelta(hours=panel_mod.STUCK_APPROVAL_HOURS + 1)
                      ).isoformat(timespec="seconds")
                led.conn.execute(
                    "UPDATE ledger SET last_processed = ? WHERE ticket_key = ?",
                    (old, "PESD1-2"))
            data = panel_mod._health()
            self.assertEqual(len(data["approved_and_aging"]), 1)
            self.assertEqual(data["approved_and_aging"][0]["ticket"], "PESD1-2")

    def test_health_html_renders_without_raising_when_everything_is_empty(self):
        """A brand-new sandbox has no heartbeat yet — and that must not crash
        the render. It also must NOT read as "all green": a missing heartbeat
        on the real system means the scheduled job has never fired or has
        stopped, which is exactly the case this page exists to surface, so a
        fresh/empty state and a genuinely stopped job are meant to look the
        same until a heartbeat is actually recorded (see the next test)."""
        import tools.panel as panel_mod
        with sandbox():
            html = panel_mod._health_html(panel_mod._health())
            self.assertIn("Flow health", html)
            self.assertIn("needs a look", html)

    def test_health_html_is_all_green_once_heartbeats_are_fresh(self):
        import tools.panel as panel_mod
        with sandbox():
            _write_heartbeat("board_tick", 1)
            _write_heartbeat("corpus_refresh", 1)
            html = panel_mod._health_html(panel_mod._health())
            self.assertIn("all green", html)

    def test_health_html_shows_needs_a_look_when_an_alert_is_active(self):
        import tools.panel as panel_mod
        with sandbox():
            marker_dir = config.LOG_DIR / ".markers"
            marker_dir.mkdir(parents=True)
            (marker_dir / "corpus_refresh_export").touch()
            html = panel_mod._health_html(panel_mod._health())
            self.assertIn("needs a look", html)
            self.assertIn("corpus_refresh_export", html)


if __name__ == "__main__":
    unittest.main()
