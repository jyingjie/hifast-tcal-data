import tempfile
import unittest
import zipfile
from pathlib import Path

from tcal_pipeline.convert_tcal import file_digest
from tcal_pipeline.publish_release import (
    PublishError,
    PublishPlan,
    _missing_review_assets,
    _validate_data_zip,
)


class DataZipTests(unittest.TestCase):
    def _write_zip(self, path, names):
        with zipfile.ZipFile(path, mode="w") as archive:
            for name in names:
                archive.writestr(name, b"test")

    def test_accepts_only_data_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "20260708.zip"
            self._write_zip(
                path,
                [
                    "CAL.20260708.high.W.fits",
                    "CAL.20260708.low.W.fits",
                    "md5sum.20260708.txt",
                ],
            )

            _validate_data_zip(path, "20260708")

    def test_rejects_image_inside_data_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "20260708.zip"
            self._write_zip(
                path,
                [
                    "CAL.20260708.high.W.fits",
                    "CAL.20260708.low.W.fits",
                    "md5sum.20260708.txt",
                    "tcal-relative-beams.png",
                ],
            )

            with self.assertRaises(PublishError):
                _validate_data_zip(path, "20260708")


class ExistingReleaseTests(unittest.TestCase):
    def test_accepts_matching_remote_data_zip_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_zip = root / "20260708.zip"
            review = root / "tcal-relative-beams.png"
            data_zip.write_bytes(b"data-zip")
            review.write_bytes(b"image")
            plan = PublishPlan(
                repository="example/project",
                date="20260708",
                data_zip=data_zip,
                review_assets=(review,),
            )
            release = {
                "assets": [
                    {
                        "name": data_zip.name,
                        "size": data_zip.stat().st_size,
                        "digest": f"sha256:{file_digest(data_zip)}",
                        "url": plan.data_url,
                    },
                    {
                        "name": review.name,
                        "size": review.stat().st_size,
                        "digest": f"sha256:{file_digest(review)}",
                    },
                ]
            }

            self.assertEqual(_missing_review_assets(plan, release), [])

    def test_rejects_remote_data_zip_with_different_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_zip = root / "20260708.zip"
            data_zip.write_bytes(b"local-data")
            plan = PublishPlan(
                repository="example/project",
                date="20260708",
                data_zip=data_zip,
                review_assets=(),
            )
            release = {
                "assets": [
                    {
                        "name": data_zip.name,
                        "size": data_zip.stat().st_size,
                        "digest": "sha256:" + ("0" * 64),
                        "url": plan.data_url,
                    }
                ]
            }

            with self.assertRaises(PublishError):
                _missing_review_assets(plan, release)


if __name__ == "__main__":
    unittest.main()
