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
