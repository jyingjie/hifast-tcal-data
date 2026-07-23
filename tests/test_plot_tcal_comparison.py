import tempfile
import unittest
import zipfile
from pathlib import Path

from tcal_pipeline.plot_tcal_comparison import (
    ComparisonError,
    extract_release_zip,
    select_previous_dates,
)


class SelectPreviousDatesTests(unittest.TestCase):
    def test_selects_latest_dates_before_current(self):
        dates = [
            "20240330",
            "20251101",
            "20241029",
            "20250329",
            "20260708",
        ]

        selected = select_previous_dates(dates, "20260708", 3)

        self.assertEqual(
            selected,
            ["20241029", "20250329", "20251101"],
        )

    def test_requires_an_older_release(self):
        with self.assertRaises(ComparisonError):
            select_previous_dates(["20260708"], "20260708", 3)


class ExtractReleaseZipTests(unittest.TestCase):
    def test_rejects_nested_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "20251101.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "nested/CAL.20251101.high.W.fits",
                    b"not-a-fits",
                )

            with self.assertRaises(ComparisonError):
                extract_release_zip(
                    archive_path,
                    "20251101",
                    root / "release",
                )


if __name__ == "__main__":
    unittest.main()
