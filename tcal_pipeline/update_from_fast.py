#!/usr/bin/env python3
"""Download, convert and package a new FAST Tcal data release locally."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from .convert_tcal import ConversionError, convert_archives, file_digest
from .fast_source import (
    ArticleSource,
    UpstreamError,
    USER_AGENT,
    discover_sources,
    trusted_ssl_context,
)
from .pdf_date import PdfDateError, verify_pdf_test_date
from .plot_tcal_comparison import (
    DEFAULT_HISTORY_COUNT,
    ComparisonError,
    generate_comparison_from_archives,
    select_previous_dates,
)


DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
RELEASE_DOWNLOAD_BASE = (
    "https://github.com/jyingjie/hifast-tcal-data/releases/download"
)


class DownloadError(RuntimeError):
    """Raised when an upstream file cannot be downloaded safely."""


def _load_manifest(path: Path) -> set[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Could not read {path}") from exc
    dates = payload.get("date") if isinstance(payload, dict) else None
    if not isinstance(dates, list) or not all(isinstance(item, str) for item in dates):
        raise DownloadError(f"{path} must contain a string array named 'date'")
    return set(dates)


def _url_filename(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not name or Path(name).name != name or name in {".", ".."}:
        raise DownloadError(f"Could not derive a safe filename from {url}")
    return name


def download_file(
    url: str,
    destination: Path,
    *,
    timeout: float = 120,
    retries: int = 3,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    force: bool = False,
) -> dict[str, Any]:
    if destination.is_file() and not force:
        return {
            "url": url,
            "path": str(destination),
            "size": destination.stat().st_size,
            "sha256": file_digest(destination),
            "reused": True,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
            with urlopen(
                request,
                timeout=timeout,
                context=trusted_ssl_context(),
            ) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise DownloadError(
                        f"{url} is larger than the {max_bytes}-byte safety limit"
                    )
                total = 0
                with partial.open("wb") as handle:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > max_bytes:
                            raise DownloadError(
                                f"{url} exceeded the {max_bytes}-byte safety limit"
                            )
                        handle.write(block)
            if total == 0:
                raise DownloadError(f"{url} returned an empty file")
            os.replace(partial, destination)
            return {
                "url": url,
                "path": str(destination),
                "size": total,
                "sha256": file_digest(destination),
                "reused": False,
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if partial.exists():
                partial.unlink()
            if attempt + 1 < retries:
                time.sleep(2**attempt)
        except DownloadError:
            if partial.exists():
                partial.unlink()
            raise

    raise DownloadError(f"Could not download {url}: {last_error}") from last_error


def _select_source(
    sources: list[ArticleSource],
    manifest_dates: set[str],
    requested_date: str | None,
) -> ArticleSource:
    if requested_date:
        matching = [
            source for source in sources if source.calibration_date == requested_date
        ]
        if len(matching) != 1:
            raise DownloadError(
                f"Expected one upstream source for {requested_date}, found {len(matching)}"
            )
        return matching[0]

    for source in sources:
        if (
            source.calibration_date
            and source.calibration_date not in manifest_dates
        ):
            return source
    raise DownloadError("No new calibration date was found")


def _verify_pdf(path: Path) -> None:
    with path.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise DownloadError(f"{path} is not a PDF file")


def build_release(
    *,
    manifest_path: Path,
    output_root: Path,
    requested_date: str | None = None,
    force_download: bool = False,
    history_count: int = DEFAULT_HISTORY_COUNT,
) -> dict[str, Any]:
    manifest_dates = _load_manifest(manifest_path)
    sources = discover_sources()
    source = _select_source(sources, manifest_dates, requested_date)
    if source.calibration_date is None:
        raise DownloadError(
            f"{source.title} does not provide a complete calibration date"
        )

    work_dir = output_root / source.calibration_date
    raw_dir = work_dir / "raw"
    release_dir = work_dir / "release"
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"Selected {source.title} as {source.calibration_date}")
    downloads: dict[str, dict[str, Any]] = {}
    for kind, url in (
        ("report", source.report_url),
        ("high", source.high_url),
        ("low", source.low_url),
    ):
        destination = raw_dir / _url_filename(url)
        print(f"Downloading {kind}: {url}")
        downloads[kind] = download_file(
            url,
            destination,
            force=force_download,
        )
        print(
            f"  {downloads[kind]['size']} bytes, "
            f"sha256={downloads[kind]['sha256']}"
        )

    report_path = Path(downloads["report"]["path"])
    _verify_pdf(report_path)
    try:
        pdf_test_date = verify_pdf_test_date(
            report_path,
            source.calibration_date,
        )
    except PdfDateError as exc:
        raise DownloadError(f"Could not verify the PDF test date: {exc}") from exc
    conversion = convert_archives(
        date=source.calibration_date,
        high_archive=Path(downloads["high"]["path"]),
        low_archive=Path(downloads["low"]["path"]),
        output_dir=release_dir,
        zip_path=work_dir / f"{source.calibration_date}.zip",
    )

    comparison = None
    if history_count:
        history_dates = select_previous_dates(
            manifest_dates,
            source.calibration_date,
            history_count,
        )
        history_downloads: dict[str, dict[str, Any]] = {}
        history_archives: list[tuple[str, Path]] = []
        history_dir = output_root / "history"
        for history_date in history_dates:
            history_url = (
                f"{RELEASE_DOWNLOAD_BASE}/{history_date}/{history_date}.zip"
            )
            history_path = history_dir / f"{history_date}.zip"
            print(f"Downloading comparison release: {history_url}")
            history_downloads[history_date] = download_file(
                history_url,
                history_path,
            )
            history_archives.append((history_date, history_path))
        comparison = generate_comparison_from_archives(
            current_date=source.calibration_date,
            current_release_dir=release_dir,
            historical_archives=history_archives,
            output_dir=work_dir / "qa",
        )
        comparison["history_downloads"] = history_downloads

    record = {
        "schema_version": 1,
        "article_id": source.article_id,
        "article_title": source.title,
        "article_created_at": source.created_at,
        "article_updated_at": source.updated_at,
        "calibration_date": source.calibration_date,
        "date_source": source.date_source,
        "pdf_test_date": pdf_test_date,
        "report_url": source.report_url,
        "high_url": source.high_url,
        "low_url": source.low_url,
        "downloads": downloads,
        "conversion": conversion,
        "comparison": comparison,
    }
    record_path = work_dir / "source-record.json"
    with record_path.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    record["record_path"] = str(record_path)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("build"))
    parser.add_argument(
        "--date",
        help="specific YYYYMMDD date; defaults to the newest unpublished date",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="download files again even if they already exist",
    )
    parser.add_argument(
        "--history-count",
        type=int,
        default=DEFAULT_HISTORY_COUNT,
        help=(
            "number of previous releases in comparison plots "
            f"(default: {DEFAULT_HISTORY_COUNT}; use 0 to skip)"
        ),
    )
    args = parser.parse_args()

    try:
        result = build_release(
            manifest_path=args.manifest,
            output_root=args.output,
            requested_date=args.date,
            force_download=args.force_download,
            history_count=args.history_count,
        )
    except (
        ComparisonError,
        ConversionError,
        DownloadError,
        OSError,
        UpstreamError,
    ) as exc:
        parser.exit(1, f"error: {exc}\n")

    print(f"Created {result['conversion']['zip']['path']}")
    print(f"Source record: {result['record_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
