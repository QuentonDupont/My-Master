import unittest

from core import preflight


class PreflightTests(unittest.TestCase):
    def test_missing_credentials_fail_fast_without_touching_the_network(self):
        real_env = preflight.config.env
        preflight.config.env = lambda name, default=None, **kw: ""
        try:
            report = preflight.run()
        finally:
            preflight.config.env = real_env
        self.assertEqual(len(report.checks), 1)
        self.assertEqual(report.checks[0].status, preflight.FAIL)
        self.assertIn("JIRA_EMAIL", report.checks[0].detail)
        self.assertTrue(report.failed)

    def test_render_lists_status_and_fix(self):
        report = preflight.Report()
        report.add("auth", preflight.PASS, "someone")
        report.add("issue_type", preflight.FAIL, "Task missing",
                   key="development.issue_type", fix="set it to Bug")
        text = report.render()
        self.assertIn("[PASS] auth", text)
        self.assertIn("[FAIL] issue_type", text)
        self.assertIn("-> set it to Bug", text)
        self.assertIn("1 pass, 0 warn, 1 fail", text)

    def test_preflight_is_read_only(self):
        import inspect
        source = inspect.getsource(preflight)
        self.assertNotIn("JiraWriteClient", source)
        self.assertNotIn("execute_proposal", source)


if __name__ == "__main__":
    unittest.main()
