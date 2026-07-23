import io
import tempfile
import unittest
from pathlib import Path

from botocore.exceptions import ClientError

from tcal_pipeline.upload_to_r2 import (
    R2Config,
    R2UploadError,
    object_key,
    upload_file,
)


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        item = self.objects.get((Bucket, Key))
        if item is None:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {
            "ContentLength": len(item["body"]),
            "Metadata": item["metadata"],
        }

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["body"])}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = {
            "body": Path(filename).read_bytes(),
            "metadata": ExtraArgs["Metadata"],
        }


class R2UploadTests(unittest.TestCase):
    def setUp(self):
        self.config = R2Config(
            endpoint_url="https://example.invalid",
            access_key_id="key",
            secret_access_key="secret",
            bucket="bucket",
            prefix="tcal-data",
        )

    def test_immutable_zip_can_repeat_but_cannot_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "20260708.zip"
            path.write_bytes(b"first")
            client = FakeS3Client()
            key = object_key(self.config, "20260708/20260708.zip")

            self.assertEqual(
                upload_file(
                    client,
                    self.config,
                    path,
                    key,
                    immutable=True,
                ),
                "uploaded",
            )
            self.assertEqual(
                upload_file(
                    client,
                    self.config,
                    path,
                    key,
                    immutable=True,
                ),
                "already-current",
            )

            path.write_bytes(b"changed")
            with self.assertRaises(R2UploadError):
                upload_file(
                    client,
                    self.config,
                    path,
                    key,
                    immutable=True,
                )

    def test_manifest_is_allowed_to_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text('{"date": []}\n', encoding="utf-8")
            client = FakeS3Client()
            key = object_key(self.config, "manifest.json")

            upload_file(
                client,
                self.config,
                path,
                key,
                immutable=False,
            )
            path.write_text('{"date": ["20260708"]}\n', encoding="utf-8")
            self.assertEqual(
                upload_file(
                    client,
                    self.config,
                    path,
                    key,
                    immutable=False,
                ),
                "uploaded",
            )


if __name__ == "__main__":
    unittest.main()
