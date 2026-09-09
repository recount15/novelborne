"""No implicit work/persona/character seeds; safe standalone V00 regression."""
from tests.v00_isolation import bootstrap

ISOLATION = bootstrap()  # Must precede application imports, including standalone runs.

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import fate_engine as fe, server
from core.engine import catalog, character_db as db, character_library as library
from core.services import registries


def forbidden(*args, **kwargs):
    raise AssertionError("implicit bundled content read")


class NoPresetContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_db = db.DATABASE_PATH
        db.set_database_path(self.root / "empty.db")
        db.ensure_database()
        registries.invalidate_character_pool_cache()

    def tearDown(self):
        db.set_database_path(self.previous_db)
        registries.invalidate_character_pool_cache()
        self.temp.cleanup()

    def test_default_work_reader_ignores_residual_asset_and_cache(self):
        residual = self.root / "work_library.md"
        residual.write_text("### W01 · 《Synthetic residual》\nDo not preload", encoding="utf8")
        with patch.object(fe, "WORK_LIBRARY_PATH", str(residual)), \
             patch.object(fe, "_rules_cache", residual.read_text(encoding="utf8")), \
             patch.object(fe, "read_upload_text", forbidden):
            self.assertEqual(fe.load_rules(), "")
            self.assertEqual(fe.list_works(), [])
            self.assertEqual(fe.get_work_block("W01"), "")
            fe.invalidate_rules_cache()
            self.assertEqual(fe.list_works(), [])

    def test_missing_default_work_asset_is_empty(self):
        with patch.object(fe, "WORK_LIBRARY_PATH", str(self.root / "absent.md")):
            self.assertEqual(fe.list_works(), [])
            self.assertEqual(fe.get_work_block("W01"), "")

    def test_explicit_own_work_file_still_reads(self):
        own = self.root / "own.md"
        own.write_text("### W01 · 《Synthetic own work》\nOwn text\n### W02 · 《Next》\nNext text", encoding="utf8")
        self.assertEqual(fe.list_works(own), ["W01 《Synthetic own work》", "W02 《Next》"])
        self.assertIn("Own text", fe.get_work_block("W01", own))
        self.assertNotIn("Next text", fe.get_work_block("W01", own))
        self.assertEqual(fe.read_upload_text(own), own.read_text(encoding="utf8"))

    def test_empty_db_never_falls_back_to_character_files(self):
        with patch.object(catalog, "_load_json", forbidden), \
             patch.object(catalog, "_builtin_character_rows", forbidden), \
             patch.object(library, "_scan_cards", forbidden), \
             patch.object(db, "migrate_from_json", forbidden):
            self.assertEqual(catalog.load_character_pool(), ())
            self.assertEqual(catalog.load_character_pool_from_json(), ())
            self.assertEqual(library.merged_pool(), ((), set()))
            db.ensure_database()
            self.assertEqual(catalog.load_character_pool(), ())

    def test_explicit_character_import_does_not_merge_bundled_cards(self):
        own = self.root / "own.json"
        own.write_text(json.dumps({"characters": [{"id": "own-test", "name": "Synthetic own character", "role": "伙伴"}]}), encoding="utf8")
        with patch.object(catalog, "_builtin_character_rows", forbidden):
            cards = catalog.load_character_pool_from_json(own)
        self.assertEqual([card.id for card in cards], ["own-test"])
        self.assertEqual(catalog.load_character_pool(), ())  # Reading does not import silently.
        saved = library.save_card({"name": cards[0].name, "role": cards[0].role})
        self.assertEqual(len(catalog.load_character_pool()), 1)
        self.assertEqual(library.merged_pool()[0][0].id, saved["record"]["id"])

    def test_bootstrap_empty_db_has_zero_preloads_and_retains_generic_options(self):
        with patch.object(fe, "_scan_models", forbidden), \
             patch.object(catalog, "_builtin_character_rows", forbidden), \
             patch.object(db, "migrate_from_json", forbidden):
            payload = server.bootstrap_payload()
        for key in ("works", "character_pools", "character_models", "custom_character_ids"):
            self.assertEqual(payload[key], [], key)
        for key in ("works", "character_pools", "character_models"):
            self.assertEqual(payload["counts"][key], 0, key)
        self.assertEqual(payload["personas"], fe.PERSONAS)
        self.assertGreater(len(payload["personas"]), 1)
        self.assertTrue(payload["modes"])
        self.assertTrue(payload["paper_tiers"])
        self.assertTrue(fe.load_runtime_rules())


if __name__ == "__main__":
    unittest.main(verbosity=2)
