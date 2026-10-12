"""The ops flows: corpus, daily report, rules, housekeeping."""
from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

from core import config, scheduler
from core.flow import Context
from flows.corpus import CORPUS
from flows.housekeeping import HOUSEKEEPING
from flows.report import DAILY_REPORT, RULES
from tests.helpers import fixture_boards, sandbox


class DailyReportTests(unittest.TestCase):
    def test_report_is_saved_to_a_file_and_email_skipped_without_gmail(self):
        with sandbox(), fixture_boards(), \
                patch("agents.chief_of_staff.health.launchd", return_value={}), \
                patch("core.mailer.configured", return_value=False), \
                patch("core.mailer.send") as send:
            res = scheduler.run_flow(DAILY_REPORT, trigger="schedule")
            self.assertTrue(res.ok, res.error)
            files = list((config.REVIEW_DIR / "daily_reports").glob("*.md"))
            self.assertEqual(len(files), 1)
            self.assertIn("HEALTH", files[0].read_text())
            self.assertIn("skipped", res.data["sent"])
            send.assert_not_called()

    def test_with_gmail_configured_it_also_sends(self):
        with sandbox(), fixture_boards(), \
                patch("agents.chief_of_staff.health.launchd", return_value={}), \
                patch("core.mailer.configured", return_value=True), \
                patch("core.mailer.send", return_value={"dry_run": False}) as send:
            res = scheduler.run_flow(DAILY_REPORT, trigger="schedule")
            self.assertTrue(res.ok, res.error)
            self.assertFalse(send.call_args.kwargs["dry_run"])

    def test_a_mail_failure_when_configured_fails_the_flow_after_saving(self):
        from core.mailer import MailerNotConfigured

        with sandbox(), fixture_boards(), \
                patch("agents.chief_of_staff.health.launchd", return_value={}), \
                patch("core.mailer.configured", return_value=True), \
                patch("core.mailer.send", side_effect=MailerNotConfigured("revoked")):
            res = scheduler.run_flow(DAILY_REPORT, trigger="schedule")
            self.assertFalse(res.ok)
            self.assertIn("send_report", res.error)
            self.assertTrue(list((config.REVIEW_DIR / "daily_reports").glob("*.md")))

    def test_report_shows_flow_health(self):
        from agents.chief_of_staff import health

        with sandbox(), fixture_boards():
            self.assertFalse(health.flows()["intake"]["overdue"])   # scheduler not started
            self.assertNotIn("execute", health.flows())             # human-only: no schedule
            scheduler.run_flow(HOUSEKEEPING)
            self.assertFalse(health.flows()["housekeeping"]["overdue"])

    def _age_scheduler(self, hours):
        """Pretend the scheduler's first run was `hours` ago."""
        with scheduler.Store() as store:
            rid = store.start_run("housekeeping", "schedule")
            store.conn.execute("UPDATE flow_runs SET started = ?, ok = 1 WHERE id = ?",
                               ((dt.datetime.now(dt.timezone.utc)
                                 - dt.timedelta(hours=hours)).isoformat(), rid))

    def test_a_weekly_flow_is_not_overdue_in_its_first_week(self):
        from agents.chief_of_staff import health

        with sandbox(), fixture_boards():
            self._age_scheduler(hours=24)
            fl = health.flows()
            self.assertFalse(fl["confluence"]["overdue"])   # weekly: allowed a week
            self.assertFalse(fl["rules"]["overdue"])
            self.assertTrue(fl["intake"]["overdue"])        # every 5m: 15m allowance

    def test_never_run_weekly_flow_is_overdue_after_its_week(self):
        from agents.chief_of_staff import health

        with sandbox(), fixture_boards():
            self._age_scheduler(hours=24 * 8)
            self.assertTrue(health.flows()["confluence"]["overdue"])

    def test_a_running_flow_is_not_overdue(self):
        from agents.chief_of_staff import health

        with sandbox(), fixture_boards():
            self._age_scheduler(hours=24 * 3)
            with scheduler.Store() as store:
                store.acquire(DAILY_REPORT, "me")
            self.assertFalse(health.flows()["daily-report"]["overdue"])

    def test_daily_report_does_not_call_itself_overdue(self):
        with sandbox(), fixture_boards(), \
                patch("agents.chief_of_staff.health.launchd", return_value={}), \
                patch("core.mailer.configured", return_value=False):
            self._age_scheduler(hours=24 * 3)
            res = scheduler.run_flow(DAILY_REPORT, trigger="schedule")
            self.assertNotIn("daily-report", res.data["report"].split("FLOWS", 1)[1]
                             .split("\n", 1)[0])


class CorpusTests(unittest.TestCase):
    def test_one_export_failing_still_reindexes(self):
        def export(project, **kw):
            if project == "PESD1":
                raise RuntimeError("jira down")
            return 7

        with sandbox(), fixture_boards(), \
                patch("corpus.export.export_jira", side_effect=export) as ex, \
                patch("corpus.index.build", return_value={"documents": 1}) as build:
            res = CORPUS.run()
            self.assertFalse(res.ok)                         # reported, not hidden
            self.assertEqual(res.errors, ["export_pesd1: jira down"])
            build.assert_called_once()
            self.assertEqual(res.data["report"]["exported_PRDT"], 7)
            self.assertTrue(all(c.kwargs["merge"] for c in ex.call_args_list))


class RulesTests(unittest.TestCase):
    def test_no_corrections_writes_nothing(self):
        with sandbox():
            res = RULES.run()
            self.assertEqual(res.data["report"], "no rule proposals this week")
            self.assertEqual(list(config.REVIEW_DIR.glob("rule_proposals_*")), [])


class HousekeepingTests(unittest.TestCase):
    def test_truncates_big_logs_including_flow_output(self):
        with sandbox():
            big = config.LOG_DIR / "flows" / "intake.out"
            big.parent.mkdir(parents=True)
            big.write_bytes(b"a" * 50 + b"TAIL")
            small = config.LOG_DIR / "x.jsonl"
            small.write_text("{}\n")
            res = HOUSEKEEPING.run(max_bytes=20, keep_bytes=4)
            self.assertEqual(big.read_bytes(), b"TAIL")
            self.assertEqual(small.read_text(), "{}\n")
            self.assertIn("flows/intake.out",
                          [t["file"] for t in res.data["report"]["truncated"]])

    def test_prunes_old_runs_only(self):
        with sandbox():
            with scheduler.Store() as store:
                old = store.start_run("intake", "schedule")
                store.conn.execute("UPDATE flow_runs SET started = ? WHERE id = ?",
                                   ((dt.datetime.now(dt.timezone.utc)
                                     - dt.timedelta(days=30)).isoformat(), old))
                store.start_run("intake", "schedule")
            res = HOUSEKEEPING.run()
            self.assertEqual(res.data["report"]["runs_pruned"], 1)
            with scheduler.Store() as store:
                self.assertEqual(len(store.runs("intake")), 1)


if __name__ == "__main__":
    unittest.main()
