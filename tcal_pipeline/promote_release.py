#!/usr/bin/env python3
"""Verify a local build and add its date and source hashes to tracked metadata."""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from .convert_tcal import ConversionError, file_digest, validate_date


class PromotionError(RuntimeError):
    """Raised when a local release record cannot be promoted."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"Could not read {path}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{path} must contain a JSON object")
    return value


def _verify_file(item: dict[str, Any], label: str) -> Path:
    path_value = item.get("path")
    expected_hash = item.get("sha256")
    expected_size = item.get("size")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise PromotionError(f"{label} record is missing path or SHA256")
    path = Path(path_value)
    if not path.is_file():
        raise PromotionError(f"{label} file does not exist: {path}")
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        raise PromotionError(f"{label} size changed: {path}")
    actual_hash = file_digest(path)
    if actual_hash != expected_hash:
        raise PromotionError(
            f"{label} SHA256 changed: expected {expected_hash}, got {actual_hash}"
        )
    return path


def verify_record(record: dict[str, Any]) -> str:
    date_value = record.get("calibration_date")
    if not isinstance(date_value, str):
        raise PromotionError("Source record is missing calibration_date")
    date = validate_date(date_value)

    downloads = record.get("downloads")
    conversion = record.get("conversion")
    if not isinstance(downloads, dict) or not isinstance(conversion, dict):
        raise PromotionError("Source record is missing downloads or conversion")
    for kind in ("report", "high", "low"):
        item = downloads.get(kind)
        if not isinstance(item, dict):
            raise PromotionError(f"Source record is missing the {kind} download")
        _verify_file(item, kind)

    fits_items = conversion.get("fits")
    if not isinstance(fits_items, list) or len(fits_items) != 2:
        raise PromotionError("Conversion record must contain two FITS files")
    for index, item in enumerate(fits_items):
        if not isinstance(item, dict):
            raise PromotionError("Invalid FITS record")
        _verify_file(item, f"FITS {index + 1}")

    zip_item = conversion.get("zip")
    if not isinstance(zip_item, dict):
        raise PromotionError("Conversion record is missing the ZIP")
    zip_path = _verify_file(zip_item, "ZIP")
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            bad_file = archive.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise PromotionError(f"Could not read ZIP {zip_path}") from exc
    if bad_file:
        raise PromotionError(f"ZIP integrity check failed at {bad_file}")

    comparison = record.get("comparison")
    if comparison is not None:
        if not isinstance(comparison, dict):
            raise PromotionError("Comparison record must be an object or null")
        images = comparison.get("images")
        history_dates = comparison.get("history_dates")
        if (
            not isinstance(images, list)
            or len(images) != 5
            or not isinstance(history_dates, list)
            or not history_dates
        ):
            raise PromotionError(
                "Comparison record must contain five images and history dates"
            )
        for index, item in enumerate(images):
            if not isinstance(item, dict):
                raise PromotionError("Invalid comparison image record")
            _verify_file(item, f"comparison image {index + 1}")
        summary = comparison.get("summary")
        if not isinstance(summary, dict):
            raise PromotionError("Comparison record is missing its JSON summary")
        _verify_file(summary, "comparison summary")
    return date


def _source_entry(record: dict[str, Any]) -> dict[str, Any]:
    downloads = record["downloads"]
    conversion = record["conversion"]
    return {
        "article_id": record.get("article_id"),
        "article_title": record.get("article_title"),
        "article_created_at": record.get("article_created_at"),
        "article_updated_at": record.get("article_updated_at"),
        "date_source": record.get("date_source"),
        "report_url": record.get("report_url"),
        "high_url": record.get("high_url"),
        "low_url": record.get("low_url"),
        "report_sha256": downloads["report"]["sha256"],
        "high_sha256": downloads["high"]["sha256"],
        "low_sha256": downloads["low"]["sha256"],
        "high_fits_sha256": conversion["fits"][0]["sha256"],
        "low_fits_sha256": conversion["fits"][1]["sha256"],
        "release_zip_sha256": conversion["zip"]["sha256"],
        "release_tag": record.get("calibration_date"),
    }


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def promote(
    record_path: Path,
    manifest_path: Path,
    sources_path: Path,
) -> str:
    record = _load_object(record_path)
    date = verify_record(record)
    manifest = _load_object(manifest_path)
    sources = _load_object(sources_path)

    dates = manifest.get("date")
    tracked_sources = sources.get("sources")
    if not isinstance(dates, list) or not all(isinstance(item, str) for item in dates):
        raise PromotionError(f"{manifest_path} has an invalid date list")
    if not isinstance(tracked_sources, dict):
        raise PromotionError(f"{sources_path} has an invalid sources object")
    expected_source = _source_entry(record)
    in_manifest = date in dates
    in_sources = date in tracked_sources
    if in_manifest != in_sources:
        raise PromotionError(
            f"{date} is present in only one metadata file; "
            "refusing to repair a partial update automatically"
        )
    if in_manifest:
        if tracked_sources[date] != expected_source:
            raise PromotionError(
                f"{date} already exists with different source metadata; "
                "refusing to overwrite it"
            )
        return date

    manifest["date"] = sorted(set(dates + [date]))
    tracked_sources[date] = expected_source
    sources["schema_version"] = 1
    sources["sources"] = tracked_sources

    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(sources_path, sources)
    return date


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="source-record.json from a local build")
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--sources", type=Path, default=Path("sources.json"))
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify files without changing tracked metadata",
    )
    args = parser.parse_args()

    try:
        record = _load_object(args.record)
        if args.verify_only:
            date = verify_record(record)
            print(f"Verified local release {date}")
        else:
            date = promote(args.record, args.manifest, args.sources)
            print(f"Metadata verified for {date} in {args.manifest} and {args.sources}")
    except (ConversionError, OSError, PromotionError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
