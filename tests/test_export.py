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


class IncrementalRefreshTests(unittest.TestCase):
    """export_jira's `created`-only JQL means a ticket opened long ago but
    resolved yesterday is never re-fetched by any created-date window,
    including a full re-run of the original --months pull — its resolution
    note (exactly what Historian's precedent matching wants) goes stale
    permanently. updated_since_days fixes the filter; merge=True stops that
    short window from overwriting the rest of the file's history."""

    def _run(self, existing: list[dict], fetched: list[dict]) -> dict[str, dict]:
        import json
        import pathlib
        import tempfile

        from corpus import export as export_mod

        saved_raw, saved_client = export_mod.RAW, export_mod.JiraReadClient
        try:
            with tempfile.TemporaryDirectory() as tmp:
                export_mod.RAW = pathlib.Path(tmp)
                out = export_mod.RAW / "pesd1.jsonl"
                out.write_text("\n".join(json.dumps(i) for i in existing) + "\n"
                               if existing else "")

                class FakeClient:
                    captured_jql = None

                    def search(self, jql, fields=None, limit=None):
                        FakeClient.captured_jql = jql
                        return fetched

                export_mod.JiraReadClient = FakeClient
                export_mod.export_jira("PESD1", updated_since_days=3, merge=True)
                self.assertIn("updated >=", FakeClient.captured_jql)
                self.assertNotIn("created >=", FakeClient.captured_jql)
                lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
                return {l["key"]: l for l in lines}
        finally:
            export_mod.RAW, export_mod.JiraReadClient = saved_raw, saved_client

    def test_untouched_history_survives_a_short_incremental_window(self):
        existing = [{"key": "PESD1-1", "fields": {"summary": "old-1"}},
                   {"key": "PESD1-2", "fields": {"summary": "old-2"}}]
        by_key = self._run(existing, fetched=[])
        self.assertEqual(set(by_key), {"PESD1-1", "PESD1-2"})

    def test_a_ticket_updated_long_after_creation_is_refreshed(self):
        existing = [{"key": "PESD1-1", "fields": {"summary": "stale summary"}}]
        fetched = [{"key": "PESD1-1", "fields": {"summary": "resolved: fixed it"}}]
        by_key = self._run(existing, fetched)
        self.assertEqual(by_key["PESD1-1"]["fields"]["summary"], "resolved: fixed it")

    def test_a_new_ticket_is_added_without_losing_the_rest(self):
        existing = [{"key": "PESD1-1", "fields": {"summary": "old-1"}}]
        fetched = [{"key": "PESD1-9", "fields": {"summary": "new-9"}}]
        by_key = self._run(existing, fetched)
        self.assertEqual(set(by_key), {"PESD1-1", "PESD1-9"})

    def test_no_existing_file_still_works(self):
        by_key = self._run(existing=[],
                           fetched=[{"key": "PESD1-9", "fields": {"summary": "x"}}])
        self.assertEqual(set(by_key), {"PESD1-9"})


if __name__ == "__main__":
    unittest.main()
