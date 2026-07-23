import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tcal_pipeline.promote_release import PromotionError, promote


class PromotionIdempotencyTests(unittest.TestCase):
    def _paths(self, root):
        record = root / "source-record.json"
        manifest = root / "manifest.json"
        sources = root / "sources.json"
        record.write_text("{}\n", encoding="utf-8")
        manifest.write_text('{"date": []}\n', encoding="utf-8")
        sources.write_text(
            '{"schema_version": 1, "sources": {}}\n',
            encoding="utf-8",
        )
        return record, manifest, sources

    def test_matching_existing_metadata_is_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            record, manifest, sources = self._paths(Path(temporary))
            source_entry = {"release_zip_sha256": "abc"}
            with (
                patch(
                    "tcal_pipeline.promote_release.verify_record",
                    return_value="20260708",
                ),
                patch(
                    "tcal_pipeline.promote_release._source_entry",
                    return_value=source_entry,
                ),
            ):
                self.assertEqual(
                    promote(record, manifest, sources),
                    "20260708",
                )
                first_manifest = manifest.read_bytes()
                first_sources = sources.read_bytes()
                self.assertEqual(
                    promote(record, manifest, sources),
                    "20260708",
                )

            self.assertEqual(manifest.read_bytes(), first_manifest)
            self.assertEqual(sources.read_bytes(), first_sources)

    def test_existing_different_source_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record, manifest, sources = self._paths(root)
            manifest.write_text('{"date": ["20260708"]}\n', encoding="utf-8")
            sources.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sources": {
                            "20260708": {"release_zip_sha256": "old"}
                        },
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch(
                    "tcal_pipeline.promote_release.verify_record",
                    return_value="20260708",
                ),
                patch(
                    "tcal_pipeline.promote_release._source_entry",
                    return_value={"release_zip_sha256": "new"},
                ),
                self.assertRaises(PromotionError),
            ):
                promote(record, manifest, sources)


if __name__ == "__main__":
    unittest.main()
