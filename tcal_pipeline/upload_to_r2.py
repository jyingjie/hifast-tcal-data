#!/usr/bin/env python3
"""Upload one immutable Tcal ZIP and/or the mutable manifest to Cloudflare R2."""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import boto3
    from boto3.exceptions import Boto3Error
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as exc:  # pragma: no cover - exercised by the command line
    raise SystemExit(
        "error: boto3 is required; install the project requirements first"
    ) from exc


DATE_PATTERN = re.compile(r"20\d{6}")


class R2UploadError(RuntimeError):
    """Raised when an R2 object cannot be uploaded or safely reused."""


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    prefix: str


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_date(value: str) -> str:
    if not DATE_PATTERN.fullmatch(value):
        raise R2UploadError(f"Invalid calibration date: {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise R2UploadError(f"Invalid calibration date: {value!r}") from exc
    return value


def load_config() -> R2Config:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    values = {
        "endpoint_url": os.environ.get("R2_ENDPOINT_URL"),
        "access_key_id": os.environ.get("R2_ACCESS_KEY_ID"),
        "secret_access_key": os.environ.get("R2_SECRET_ACCESS_KEY"),
        "bucket": os.environ.get("R2_BUCKET_NAME"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        variable_names = {
            "endpoint_url": "R2_ENDPOINT_URL",
            "access_key_id": "R2_ACCESS_KEY_ID",
            "secret_access_key": "R2_SECRET_ACCESS_KEY",
            "bucket": "R2_BUCKET_NAME",
        }
        raise R2UploadError(
            "Missing R2 configuration: "
            + ", ".join(variable_names[name] for name in missing)
        )
    return R2Config(
        endpoint_url=values["endpoint_url"],
        access_key_id=values["access_key_id"],
        secret_access_key=values["secret_access_key"],
        bucket=values["bucket"],
        prefix=os.environ.get("R2_PREFIX", "").strip("/"),
    )


def object_key(config: R2Config, relative_key: str) -> str:
    relative = relative_key.lstrip("/")
    return f"{config.prefix}/{relative}" if config.prefix else relative


def _head_object(client: Any, config: R2Config, key: str) -> dict[str, Any] | None:
    try:
        return client.head_object(Bucket=config.bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return None
        raise R2UploadError(f"Could not inspect R2 object {key}: {exc}") from exc


def _download_digest(client: Any, config: R2Config, key: str) -> str:
    try:
        response = client.get_object(Bucket=config.bucket, Key=key)
        body = response["Body"]
        digest = hashlib.sha256()
        while True:
            block = body.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
        body.close()
        return digest.hexdigest()
    except (ClientError, KeyError, OSError) as exc:
        raise R2UploadError(
            f"Could not verify existing R2 object {key}: {exc}"
        ) from exc


def upload_file(
    client: Any,
    config: R2Config,
    local_path: Path,
    key: str,
    *,
    immutable: bool,
) -> str:
    if not local_path.is_file():
        raise R2UploadError(f"Local upload file does not exist: {local_path}")

    local_size = local_path.stat().st_size
    local_hash = file_digest(local_path)
    remote = _head_object(client, config, key)
    if remote is not None:
        remote_size = remote.get("ContentLength")
        metadata = remote.get("Metadata")
        remote_hash = metadata.get("sha256") if isinstance(metadata, dict) else None
        if remote_size == local_size and remote_hash == local_hash:
            print(f"Already current: s3://{config.bucket}/{key}")
            return "already-current"
        if immutable:
            if remote_size == local_size and remote_hash is None:
                remote_hash = _download_digest(client, config, key)
                if remote_hash == local_hash:
                    print(f"Existing immutable object matches: {key}")
                    return "already-current"
            raise R2UploadError(
                f"Immutable R2 object {key} already exists with different content"
            )

    content_type, _ = mimetypes.guess_type(local_path.name)
    extra_args = {
        "ContentType": content_type or "application/octet-stream",
        "Metadata": {"sha256": local_hash},
    }
    print(f"Uploading {local_path} -> s3://{config.bucket}/{key}")
    try:
        client.upload_file(
            str(local_path),
            config.bucket,
            key,
            ExtraArgs=extra_args,
        )
    except (Boto3Error, BotoCoreError, ClientError, OSError) as exc:
        raise R2UploadError(f"Could not upload {key}: {exc}") from exc

    uploaded = _head_object(client, config, key)
    uploaded_metadata = uploaded.get("Metadata") if uploaded else None
    if (
        uploaded is None
        or uploaded.get("ContentLength") != local_size
        or not isinstance(uploaded_metadata, dict)
        or uploaded_metadata.get("sha256") != local_hash
    ):
        raise R2UploadError(f"R2 verification failed after uploading {key}")
    print(f"Verified SHA256 {local_hash}")
    return "uploaded"


def create_client(config: R2Config) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, help="one YYYYMMDD.zip data archive")
    parser.add_argument("--date", help="calibration date belonging to --zip")
    parser.add_argument(
        "--manifest",
        type=Path,
        help="manifest.json to upload after any ZIP",
    )
    args = parser.parse_args()

    if args.zip is None and args.manifest is None:
        parser.error("at least one of --zip or --manifest is required")
    if (args.zip is None) != (args.date is None):
        parser.error("--zip and --date must be supplied together")

    try:
        config = load_config()
        client = create_client(config)
        if args.zip is not None:
            date = validate_date(args.date)
            if args.zip.name != f"{date}.zip":
                raise R2UploadError(
                    f"ZIP must be named {date}.zip, got {args.zip.name!r}"
                )
            upload_file(
                client,
                config,
                args.zip,
                object_key(config, f"{date}/{date}.zip"),
                immutable=True,
            )
        if args.manifest is not None:
            if args.manifest.name != "manifest.json":
                raise R2UploadError("Manifest file must be named manifest.json")
            upload_file(
                client,
                config,
                args.manifest,
                object_key(config, "manifest.json"),
                immutable=False,
            )
    except R2UploadError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
