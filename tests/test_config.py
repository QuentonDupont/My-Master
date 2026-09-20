import pathlib
import shutil
import unittest

from core import config, miniyaml


class ConfigTests(unittest.TestCase):
    def test_every_config_file_parses(self):
        for name in ("boards", "never_touch", "repos"):
            data = config.load_yaml(config.CONFIG_DIR / f"{name}.yml")
            self.assertIsInstance(data, dict, name)
            self.assertTrue(data, name)

    def test_boards_are_pesd1_and_prdt_only(self):
        self.assertEqual(config.allowed_projects(), ["PESD1", "PRDT"])
        self.assertEqual(config.intake_project(), "PESD1")
        self.assertEqual(config.dev_project(), "PRDT")

    def test_clone_issue_type_is_configured(self):
        live = config.load_yaml(config.CONFIG_DIR / "boards.yml")
        development = live["development"]
        self.assertIn("issue_type", development,
                      "boards.yml must say which issue type clones are created as")
        self.assertTrue(development["issue_type"])

    def test_plural_folding(self):
        from corpus.index import singular
        self.assertEqual(singular("locations"), "location")
        self.assertEqual(singular("categories"), "category")
        self.assertEqual(singular("addresses"), "address")
        for unchanged in ("status", "analysis", "bonus", "po", "ns"):
            self.assertEqual(singular(unchanged), unchanged)

    def test_confluence_spaces_are_configured(self):
        spaces = config.confluence().get("spaces") or []
        self.assertTrue(spaces, "config/confluence.yml must list at least one space")
        self.assertTrue(all("key" in s for s in spaces))

    def test_miniyaml_subset(self):
        parsed = miniyaml.loads("""
a: 1
b:
  - x
  - y
c:
  d: [p, q]   # inline
  e: "quoted: value"
f: true
""")
        self.assertEqual(parsed, {"a": 1, "b": ["x", "y"],
                                  "c": {"d": ["p", "q"], "e": "quoted: value"},
                                  "f": True})

    def test_miniyaml_flow_maps(self):
        """`required_fields: {}` must be an empty map, not the string "{}"."""
        parsed = miniyaml.loads('a: {}\nb: {x: 1, y: two}\n')
        self.assertEqual(parsed, {"a": {}, "b": {"x": 1, "y": "two"}})

    def test_miniyaml_handles_multiline_flow_lists(self):
        parsed = miniyaml.loads("k:\n  - one\nwords: [a, b,\n         c]\n")
        self.assertEqual(parsed["words"], ["a", "b", "c"])

    def test_env_never_overrides_the_real_environment(self):
        import os
        os.environ["JIRA_EMAIL"] = "set-by-test@example.com"
        try:
            self.assertEqual(config.env("JIRA_EMAIL"), "set-by-test@example.com")
        finally:
            del os.environ["JIRA_EMAIL"]


if __name__ == "__main__":
    unittest.main()


class LoggerRobustnessTests(unittest.TestCase):
    """Logging must never break the operation it is describing."""

    def test_the_path_follows_a_changed_log_dir(self):
        import tempfile
        from core import config, log
        saved = config.LOG_DIR
        try:
            with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
                logger = log.get("probe")
                config.LOG_DIR = pathlib.Path(a)
                first = logger.path
                config.LOG_DIR = pathlib.Path(b)
                self.assertNotEqual(first, logger.path)
                self.assertTrue(str(logger.path).startswith(b))
        finally:
            config.LOG_DIR = saved

    def test_a_write_failure_does_not_reach_the_caller(self):
        """A rejection that logs and then cannot transition is worse than a
        missing log line. This is what broke mobile.apply mid-rejection."""
        import tempfile
        from core import config, log
        saved = config.LOG_DIR
        try:
            with tempfile.TemporaryDirectory() as d:
                gone = pathlib.Path(d) / "removed"
                gone.mkdir()
                config.LOG_DIR = gone / "deeper"
                config.LOG_DIR.mkdir()
                logger = log.get("probe")
                logger.info("before", ok=True)
                shutil.rmtree(gone)
                logger.info("after", ok=True)     # must not raise
        finally:
            config.LOG_DIR = saved
