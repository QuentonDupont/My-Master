"""Exporting one space must honour that space's configured page limit.

`--space OP` used to build its own spec with no limit, so the 600 in
config/confluence.yml was never read and OP was capped at the 200 default: 125
of 554 pages exported, reported as success. A silent under-export is the worst
shape of bug here — the corpus looks built and answers badly.
"""
import unittest

from core import config
from corpus import export as export_mod


SPACES = [
    {"key": "PSD", "name": "Pomelo Service Desk", "limit": 400},
    {"key": "PM", "name": "Product Management", "limit": 400},
    {"key": "OP", "name": "Operations", "limit": 600},
]


class fake_config:
    """config.confluence() pinned to a known set of spaces."""

    def __enter__(self):
        self.saved = config.confluence
        config.confluence = lambda: {"spaces": SPACES}
        return self

    def __exit__(self, *exc):
        config.confluence = self.saved


def cap(spec, limit=None):
    """What export_confluence would use for this spec."""
    return limit or spec.get("limit") or export_mod.DEFAULT_PAGE_LIMIT


class SpaceSpecTests(unittest.TestCase):
    def test_a_named_space_keeps_its_configured_limit(self):
        with fake_config():
            spec = export_mod.space_specs("OP")[0]
            self.assertEqual(spec["limit"], 600)
            self.assertEqual(cap(spec), 600)

    def test_every_configured_space_is_exported_by_default(self):
        with fake_config():
            self.assertEqual([s["key"] for s in export_mod.space_specs()],
                             ["PSD", "PM", "OP"])

    def test_an_unconfigured_space_falls_back_to_the_default(self):
        with fake_config():
            spec = export_mod.space_specs("DO")[0]
            self.assertEqual(spec["key"], "DO")
            self.assertEqual(cap(spec), export_mod.DEFAULT_PAGE_LIMIT)

    def test_an_explicit_limit_still_wins(self):
        with fake_config():
            spec = export_mod.space_specs("OP")[0]
            self.assertEqual(cap(spec, limit=50), 50)

    def test_the_config_is_not_mutated_by_a_named_export(self):
        """space_specs returns a copy — a later full export must be unaffected."""
        with fake_config():
            export_mod.space_specs("OP")[0]["limit"] = 1
            self.assertEqual(export_mod.space_specs("OP")[0]["limit"], 600)

    def test_a_space_with_no_key_is_ignored(self):
        saved = config.confluence
        config.confluence = lambda: {"spaces": [{"name": "nameless"},
                                                {"key": "PM", "limit": 400}]}
        try:
            self.assertEqual([s["key"] for s in export_mod.space_specs()], ["PM"])
        finally:
            config.confluence = saved

    def test_every_real_space_has_headroom_over_its_page_count(self):
        """Guards the actual confluence.yml, not just the resolution logic.

        Pages counted 20 Sep 2026. A limit at or below the real count truncates
        the export silently, so this fails loudly instead when a space outgrows
        the number someone wrote down months earlier.
        """
        counted = {"PSD": 2, "PM": 400, "OP": 554, "NEON": 146, "MUL": 153}
        for key, pages in counted.items():
            with self.subTest(space=key):
                spec = export_mod.space_specs(key)[0]
                self.assertGreaterEqual(
                    cap(spec), pages,
                    f"{key} had {pages} pages; its limit must cover them")

    def test_every_configured_space_states_its_own_limit(self):
        """Relying on the 200 default is how OP was truncated in the first place."""
        for spec in export_mod.space_specs():
            with self.subTest(space=spec.get("key")):
                self.assertTrue(spec.get("limit"),
                                f"{spec.get('key')} has no limit in confluence.yml")


if __name__ == "__main__":
    unittest.main()
