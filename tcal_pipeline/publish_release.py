#!/usr/bin/env python3
"""Publish one verified Tcal ZIP and its review images as separate assets."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .convert_tcal import ConversionError, file_digest
from .promote_release import PromotionError, verify_record


DEFAULT_REPOSITORY = "jyingjie/hifast-tcal-data"
EXPECTED_IMAGE_NAMES = {
    "tcal-spectra-high-xx.png",
    "tcal-spectra-high-yy.png",
    "tcal-spectra-low-xx.png",
    "tcal-spectra-low-yy.png",
    "tcal-relative-beams.png",
}


class PublishError(RuntimeError):
    """Raised when release assets are unsafe or GitHub publishing fails."""


@dataclass(frozen=True)
class PublishPlan:
    repository: str
    date: str
    data_zip: Path
    review_assets: tuple[Path, ...]

    @property
    def data_url(self) -> str:
        return (
            f"https://github.com/{self.repository}/releases/download/"
            f"{self.date}/{self.date}.zip"
        )

    def asset_url(self, path: Path) -> str:
        return (
            f"https://github.com/{self.repository}/releases/download/"
            f"{self.date}/{path.name}"
        )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError(f"Could not read {path}") from exc
    if not isinstance(value, dict):
        raise PublishError(f"{path} must contain a JSON object")
    return value


def _item_path(item: Any, label: str) -> Path:
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        raise PublishError(f"{label} record is missing its path")
    path = Path(item["path"])
    if not path.is_file():
        raise PublishError(f"{label} file does not exist: {path}")
    return path


def _validate_data_zip(path: Path, date: str) -> None:
    expected = {
        f"CAL.{date}.high.W.fits",
        f"CAL.{date}.low.W.fits",
        f"md5sum.{date}.txt",
    }
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            names = [item.filename for item in archive.infolist()]
            if len(names) != len(set(names)) or set(names) != expected:
                raise PublishError(
                    f"{path} must contain only the two FITS files and MD5 file; "
                    f"found {sorted(names)}"
                )
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        raise PublishError(f"Could not read data ZIP {path}") from exc
    if bad_member is not None:
        raise PublishError(f"ZIP integrity check failed at {bad_member}")


def build_publish_plan(
    record: dict[str, Any],
    *,
    repository: str = DEFAULT_REPOSITORY,
) -> PublishPlan:
    try:
        date = verify_record(record)
    except (ConversionError, PromotionError) as exc:
        raise PublishError(f"Local release verification failed: {exc}") from exc

    if (
        not repository
        or repository.count("/") != 1
        or any(character.isspace() for character in repository)
    ):
        raise PublishError(f"Invalid GitHub repository: {repository!r}")

    conversion = record.get("conversion")
    comparison = record.get("comparison")
    if not isinstance(conversion, dict):
        raise PublishError("Source record is missing conversion data")
    if not isinstance(comparison, dict):
        raise PublishError(
            "Source record has no comparison images; refusing to publish"
        )

    data_zip = _item_path(conversion.get("zip"), "data ZIP")
    if data_zip.name != f"{date}.zip":
        raise PublishError(
            f"Data ZIP must be named {date}.zip, got {data_zip.name!r}"
        )
    _validate_data_zip(data_zip, date)

    image_items = comparison.get("images")
    if not isinstance(image_items, list) or len(image_items) != 5:
        raise PublishError("Exactly five comparison images are required")
    image_paths = tuple(
        _item_path(item, f"comparison image {index + 1}")
        for index, item in enumerate(image_items)
    )
    image_names = {path.name for path in image_paths}
    if image_names != EXPECTED_IMAGE_NAMES:
        raise PublishError(
            "Comparison image names differ from the required release assets: "
            f"{sorted(image_names)}"
        )

    summary_path = _item_path(
        comparison.get("summary"),
        "comparison summary",
    )
    if summary_path.name != "comparison-summary.json":
        raise PublishError(
            "Comparison summary must be named comparison-summary.json"
        )
    review_assets = image_paths + (summary_path,)
    if any(path.suffix.lower() == ".zip" for path in review_assets):
        raise PublishError("Review assets must not be ZIP files")

    return PublishPlan(
        repository=repository,
        date=date,
        data_zip=data_zip,
        review_assets=review_assets,
    )


def _release_notes(plan: PublishPlan) -> str:
    image_links = "\n".join(
        f"- [{path.stem}]({plan.asset_url(path)})"
        for path in plan.review_assets
        if path.suffix.lower() == ".png"
    )
    return (
        "Converted from the official FAST noise-diode calibration data.\n\n"
        f"HiFAST data archive: `{plan.date}.zip`\n"
        "The PNG files are separate visual-review assets and are not part "
        "of the data archive.\n\n"
        "Visual review:\n"
        f"{image_links}"
    )


def _create_command(plan: PublishPlan) -> list[str]:
    return [
        "gh",
        "release",
        "create",
        plan.date,
        str(plan.data_zip),
        *(str(path) for path in plan.review_assets),
        "--repo",
        plan.repository,
        "--title",
        f"Tcal Data {plan.date}",
        "--notes",
        _release_notes(plan),
    ]


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PublishError(f"Could not run {command[0]!r}") from exc


def _view_release(plan: PublishPlan) -> dict[str, Any] | None:
    command = [
        "gh",
        "release",
        "view",
        plan.date,
        "--repo",
        plan.repository,
        "--json",
        "tagName,assets,isDraft,isImmutable,url",
    ]
    result = _run_command(command)
    if result.returncode == 0:
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PublishError("GitHub CLI returned invalid release JSON") from exc
        if not isinstance(value, dict):
            raise PublishError("GitHub CLI returned an invalid release record")
        return value
    if "release not found" in result.stderr.lower():
        return None
    message = result.stderr.strip() or result.stdout.strip()
    raise PublishError(f"Could not inspect the GitHub Release: {message}")


def _missing_review_assets(
    plan: PublishPlan,
    release: dict[str, Any],
) -> list[Path]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PublishError("Existing GitHub Release has no asset list")
    remote_assets = {
        item.get("name"): item
        for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    data_asset = remote_assets.get(plan.data_zip.name)
    if not isinstance(data_asset, dict):
        raise PublishError(
            f"Existing release has no {plan.data_zip.name}; "
            "refusing to modify the release"
        )
    if data_asset.get("size") != plan.data_zip.stat().st_size:
        raise PublishError(
            f"Existing {plan.data_zip.name} has a different size; "
            "refusing to replace it"
        )
    if data_asset.get("url") != plan.data_url:
        raise PublishError(
            "Existing data ZIP URL differs from the required stable URL"
        )
    expected_data_digest = f"sha256:{file_digest(plan.data_zip)}"
    if data_asset.get("digest") != expected_data_digest:
        raise PublishError(
            f"Existing {plan.data_zip.name} SHA256 differs from the local file; "
            "refusing to add review assets"
        )

    missing: list[Path] = []
    for path in plan.review_assets:
        remote = remote_assets.get(path.name)
        if remote is None:
            missing.append(path)
        elif remote.get("size") != path.stat().st_size:
            raise PublishError(
                f"Existing review asset {path.name} has a different size; "
                "refusing to overwrite it"
            )
        elif remote.get("digest") != f"sha256:{file_digest(path)}":
            raise PublishError(
                f"Existing review asset {path.name} has a different SHA256; "
                "refusing to overwrite it"
            )
    return missing


def publish(plan: PublishPlan) -> str:
    release = _view_release(plan)
    if release is None:
        result = _run_command(_create_command(plan))
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise PublishError(f"Could not create GitHub Release: {message}")
        return "created"

    missing = _missing_review_assets(plan, release)
    if not missing:
        return "already-complete"
    if release.get("isImmutable"):
        raise PublishError(
            "The existing release is immutable; missing review assets "
            "cannot be added"
        )
    command = [
        "gh",
        "release",
        "upload",
        plan.date,
        *(str(path) for path in missing),
        "--repo",
        plan.repository,
    ]
    result = _run_command(command)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise PublishError(f"Could not upload review assets: {message}")
    return "review-assets-added"


def _print_plan(plan: PublishPlan) -> None:
    print(f"Release tag: {plan.date}")
    print(f"Data ZIP: {plan.data_zip}")
    print(f"Stable data URL: {plan.data_url}")
    print("Separate review assets:")
    for path in plan.review_assets:
        print(f"  {path}")
        print(f"    {plan.asset_url(path)}")
    print("Create command:")
    print(f"  {shlex.join(_create_command(plan))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "record",
        type=Path,
        help="verified source-record.json from tcal_pipeline.update_from_fast",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository (default: {DEFAULT_REPOSITORY})",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="perform GitHub changes; without this option only validate and print",
    )
    args = parser.parse_args()

    try:
        record = _load_json_object(args.record)
        plan = build_publish_plan(record, repository=args.repo)
        _print_plan(plan)
        if not args.publish:
            print("Dry run only; no GitHub Release was changed.")
            return 0

        result = publish(plan)
    except (OSError, PublishError) as exc:
        parser.exit(1, f"error: {exc}\n")

    if result == "created":
        print(f"Published GitHub Release {plan.date}")
    elif result == "review-assets-added":
        print(
            f"Added review assets to {plan.date}; "
            f"{plan.data_zip.name} was not changed"
        )
    else:
        print(f"GitHub Release {plan.date} already has all required assets")
    print(f"HiFAST data URL remains: {plan.data_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
