"""Boards in scope: PESD1 and PRDT only, and no second write path."""
import inspect
import unittest

from core import jira_client as J


class ScopeTests(unittest.TestCase):
    def test_jql_must_name_an_allowed_project(self):
        J.assert_jql_scoped('project = PESD1 AND status = "Waiting for Support"')
        J.assert_jql_scoped("project in (PESD1, PRDT)")
        for bad in ("status = Open", "project = APOLLO", "project in (PESD1, HENRY)"):
            with self.assertRaises(J.ScopeError, msg=bad):
                J.assert_jql_scoped(bad)

    def test_issue_keys_are_checked(self):
        J.assert_key_allowed("PESD1-1")
        J.assert_key_allowed("PRDT-1")
        for bad in ("APOLLO-1", "HENRY-9", "not-a-key", ""):
            with self.assertRaises(J.ScopeError):
                J.assert_key_allowed(bad)

    def test_read_client_has_no_write_methods(self):
        source = inspect.getsource(J.JiraReadClient)
        for verb in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"'):
            if verb == '"POST"':
                continue  # POST /search is a read in Jira's API
            self.assertNotIn(verb, source, f"read client issues {verb}")
        self.assertNotIn("/rest/api/2/issue\"", source)

    def test_write_client_is_inert_without_execute(self):
        writer = J.JiraWriteClient(base_url="https://x", email="e", token="t")
        self.assertFalse(writer.execute)
        result = writer.add_comment("PESD1-1", "hello")
        self.assertTrue(result["dry_run"])
        self.assertEqual(writer.performed, [])

    def test_every_write_method_has_an_undo(self):
        pairs = [("add_comment", "delete_comment"), ("create_issue", "delete_issue"),
                 ("link_issues", "delete_link"), ("assign", "unassign")]
        for write, undo in pairs:
            self.assertTrue(hasattr(J.JiraWriteClient, write))
            self.assertTrue(hasattr(J.JiraWriteClient, undo),
                            f"{write} has no undo (invariant 8)")

    def test_workers_never_import_a_write_client(self):
        from agents.jira_leader import worker
        source = inspect.getsource(worker)
        self.assertNotIn("JiraWriteClient", source)
        self.assertNotIn("execute_proposal", source)


class RedactionTests(unittest.TestCase):
    def test_secrets_are_redacted_in_logs(self):
        from core import config, log
        config._secrets.add("tok_abcdef123456")
        try:
            self.assertEqual(log.redact("bearer tok_abcdef123456"), "bearer ***")
            self.assertEqual(log.redact({"api_key": "x"})["api_key"], "***")
        finally:
            config._secrets.discard("tok_abcdef123456")


if __name__ == "__main__":
    unittest.main()
