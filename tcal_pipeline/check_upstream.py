#!/usr/bin/env python3
"""Check the FAST website for new or changed Tcal source files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .fast_source import ArticleSource, UpstreamError, discover_sources


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return payload


def _classify(
    source: ArticleSource,
    manifest_dates: set[str],
    tracked_sources: dict[str, Any],
) -> tuple[str, list[str]]:
    date = source.calibration_date
    if date is None:
        return "manual_date_required", ["full calibration date is unavailable"]
    if date not in manifest_dates:
        return "new", []

    tracked = tracked_sources.get(date)
    if not isinstance(tracked, dict):
        return "known_untracked", []

    changes: list[str] = []
    expected = {
        "article_id": source.article_id,
        "article_updated_at": source.updated_at,
        "report_url": source.report_url,
        "high_url": source.high_url,
        "low_url": source.low_url,
    }
    for field, current_value in expected.items():
        previous_value = tracked.get(field)
        if previous_value is not None and previous_value != current_value:
            changes.append(field)
    return ("changed" if changes else "tracked"), changes


def check(
    manifest_path: Path,
    sources_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    manifest_values = manifest.get("date", [])
    if not isinstance(manifest_values, list) or not all(
        isinstance(value, str) for value in manifest_values
    ):
        raise RuntimeError(f"{manifest_path} must contain a string array named 'date'")
    manifest_dates = set(manifest_values)

    source_state = _load_json(sources_path)
    tracked_sources = source_state.get("sources", {})
    if not isinstance(tracked_sources, dict):
        raise RuntimeError(f"{sources_path} must contain an object named 'sources'")

    discovered = discover_sources()
    entries: list[dict[str, Any]] = []
    for source in discovered:
        status, changes = _classify(source, manifest_dates, tracked_sources)
        entry = source.to_dict()
        entry["status"] = status
        entry["changes"] = changes
        entries.append(entry)

    new_dates = [
        entry["calibration_date"]
        for entry in entries
        if entry["status"] == "new" and entry["calibration_date"]
    ]
    return {
        "schema_version": 1,
        "new_dates": new_dates,
        "has_new": bool(new_dates),
        "has_changed": any(entry["status"] == "changed" for entry in entries),
        "manual_date_required": any(
            entry["status"] == "manual_date_required" for entry in entries
        ),
        "entries": entries,
    }


def _print_human(result: dict[str, Any]) -> None:
    print("FAST noise-diode calibration sources:")
    for entry in result["entries"]:
        date = entry["calibration_date"] or entry["report_month"] or "unknown"
        print(
            f"  {date}: {entry['status']}"
            f" ({entry['title']}, article {entry['article_id']})"
        )
        if entry["changes"]:
            print(f"    changed fields: {', '.join(entry['changes'])}")
    if result["new_dates"]:
        print(f"New calibration dates: {', '.join(result['new_dates'])}")
    else:
        print("No new calibration date found.")


def _write_github_output(result: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("--github-output requires the GITHUB_OUTPUT environment variable")
    latest = result["new_dates"][0] if result["new_dates"] else ""
    values = {
        "has_new": str(result["has_new"]).lower(),
        "new_dates": json.dumps(result["new_dates"], separators=(",", ":")),
        "latest_new_date": latest,
        "has_changed": str(result["has_changed"]).lower(),
        "manual_date_required": str(result["manual_date_required"]).lower(),
    }
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--sources", type=Path, default=Path("sources.json"))
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="write the complete check result to this JSON file",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="write summary fields to the current GitHub Actions output file",
    )
    args = parser.parse_args()

    try:
        result = check(args.manifest, args.sources)
        if args.snapshot:
            args.snapshot.parent.mkdir(parents=True, exist_ok=True)
            with args.snapshot.open("w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
        if args.github_output:
            _write_github_output(result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_human(result)
    except (OSError, RuntimeError, UpstreamError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
