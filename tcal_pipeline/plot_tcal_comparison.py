#!/usr/bin/env python3
"""Generate visual comparisons between a new Tcal release and recent releases."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .convert_tcal import (
    MODES,
    file_digest,
    validate_date,
    validate_fits,
)


POLARIZATION_NAMES = ("XX", "YY")
DEFAULT_HISTORY_COUNT = 3
DEFAULT_SPECTRUM_RANGE_MHZ = (1020.0, 1480.0)
DEFAULT_SPECTRUM_BIN_MHZ = 1.0
DEFAULT_RELATIVE_RANGE_MHZ = (1330.0, 1430.0)
DEFAULT_RELATIVE_BIN_MHZ = 5.0


class ComparisonError(RuntimeError):
    """Raised when visual comparison inputs or outputs are invalid."""


@dataclass(frozen=True)
class ReleaseData:
    date: str
    frequency: Any
    tcals: dict[str, Any]


def _dependencies() -> tuple[Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        import numpy as np
    except ImportError as exc:
        raise ComparisonError(
            "numpy and matplotlib are required; install requirements.txt"
        ) from exc
    return np, plt


def select_previous_dates(
    manifest_dates: Iterable[str],
    current_date: str,
    count: int = DEFAULT_HISTORY_COUNT,
) -> list[str]:
    validate_date(current_date)
    if count < 1:
        raise ComparisonError("History count must be at least one")

    valid_dates: set[str] = set()
    for value in manifest_dates:
        try:
            valid_dates.add(validate_date(value))
        except Exception as exc:
            raise ComparisonError(
                f"Invalid date in the release manifest: {value!r}"
            ) from exc
    previous = sorted(value for value in valid_dates if value < current_date)
    if not previous:
        raise ComparisonError(
            f"No release date before {current_date} is available for comparison"
        )
    return previous[-count:]


def _expected_release_names(date: str) -> set[str]:
    return {
        f"CAL.{date}.high.W.fits",
        f"CAL.{date}.low.W.fits",
        f"md5sum.{date}.txt",
    }


def extract_release_zip(zip_path: Path, date: str, destination: Path) -> Path:
    """Extract one release ZIP after checking its exact top-level contents."""
    validate_date(date)
    expected = _expected_release_names(date)
    destination.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, mode="r") as archive:
            members = archive.infolist()
            names: list[str] = []
            for member in members:
                member_path = PurePosixPath(member.filename)
                if (
                    member.is_dir()
                    or member_path.is_absolute()
                    or ".." in member_path.parts
                    or len(member_path.parts) != 1
                ):
                    raise ComparisonError(
                        f"Unsafe or nested member {member.filename!r} in {zip_path}"
                    )
                names.append(member_path.name)
            if len(names) != len(set(names)):
                raise ComparisonError(f"{zip_path} contains duplicate members")
            if set(names) != expected:
                missing = sorted(expected - set(names))
                extra = sorted(set(names) - expected)
                raise ComparisonError(
                    f"{zip_path} has unexpected contents; "
                    f"missing={missing}, extra={extra}"
                )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ComparisonError(
                    f"ZIP integrity check failed at {bad_member} in {zip_path}"
                )
            for filename in sorted(expected):
                target = destination / filename
                temporary = target.with_name(f".{target.name}.tmp")
                with archive.open(filename) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                temporary.replace(target)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ComparisonError(f"Could not read release ZIP {zip_path}") from exc

    return destination


def load_release(release_dir: Path, date: str) -> ReleaseData:
    np, _ = _dependencies()
    validate_date(date)
    tcals: dict[str, Any] = {}
    frequency: Any | None = None

    for mode in MODES:
        path = release_dir / f"CAL.{date}.{mode}.W.fits"
        if not path.is_file():
            raise ComparisonError(f"Missing comparison FITS file: {path}")
        checked_frequency, checked_tcals = validate_fits(path)
        if frequency is None:
            frequency = checked_frequency
        elif not np.array_equal(frequency, checked_frequency):
            raise ComparisonError(
                f"The high and low frequency grids differ for {date}"
            )
        tcals[mode] = checked_tcals

    if frequency is None:
        raise ComparisonError(f"No FITS data was loaded for {date}")
    return ReleaseData(date=date, frequency=frequency, tcals=tcals)


def downsample_spectrum(
    frequency: Any,
    values: Any,
    *,
    frequency_range: tuple[float, float] = DEFAULT_SPECTRUM_RANGE_MHZ,
    bin_width_mhz: float = DEFAULT_SPECTRUM_BIN_MHZ,
) -> tuple[Any, Any]:
    """Average a spectrum in fixed-width frequency bins for readable plots."""
    np, _ = _dependencies()
    lower, upper = frequency_range
    if not lower < upper or bin_width_mhz <= 0:
        raise ComparisonError("Invalid spectrum frequency range or bin width")
    mask = (frequency >= lower) & (frequency <= upper)
    selected_frequency = frequency[mask]
    selected_values = values[mask]
    if selected_frequency.size == 0:
        raise ComparisonError(
            f"No channels fall inside {lower:g}-{upper:g} MHz"
        )

    bin_index = np.floor((selected_frequency - lower) / bin_width_mhz).astype(int)
    bin_count = int(np.floor((upper - lower) / bin_width_mhz)) + 1
    counts = np.bincount(bin_index, minlength=bin_count)
    sums = np.bincount(
        bin_index,
        weights=selected_values,
        minlength=bin_count,
    )
    populated = counts > 0
    centers = lower + (np.arange(bin_count) + 0.5) * bin_width_mhz
    return centers[populated], sums[populated] / counts[populated]


def relative_beam_statistics(
    frequency: Any,
    tcals: Any,
    polarization: int,
    *,
    frequency_range: tuple[float, float] = DEFAULT_RELATIVE_RANGE_MHZ,
    bin_width_mhz: float = DEFAULT_RELATIVE_BIN_MHZ,
) -> tuple[Any, Any, Any]:
    """Match the notebook's per-beam Tcal/M01 percentile comparison."""
    np, _ = _dependencies()
    lower, upper = frequency_range
    if polarization not in (0, 1):
        raise ComparisonError(f"Invalid polarization index: {polarization}")
    if not lower < upper or bin_width_mhz <= 0:
        raise ComparisonError("Invalid relative-comparison range or bin width")

    bin_edges = np.arange(lower, upper + bin_width_mhz, bin_width_mhz)
    medians: list[Any] = []
    for start, stop in zip(bin_edges[:-1], bin_edges[1:]):
        mask = (frequency >= start) & (frequency < stop)
        if not np.any(mask):
            raise ComparisonError(
                f"No channels fall inside the {start:g}-{stop:g} MHz bin"
            )
        medians.append(np.median(tcals[:, mask, polarization], axis=1))
    binned = np.stack(medians, axis=1)
    if np.any(binned[0] == 0):
        raise ComparisonError("M01 contains a zero median in a comparison bin")
    relative = binned / binned[0]
    lower_values, middle_values, upper_values = np.percentile(
        relative,
        [16, 50, 84],
        axis=1,
    )
    return lower_values, middle_values, upper_values


def _line_style(index: int, final_index: int) -> dict[str, Any]:
    if index == final_index:
        return {
            "color": "black",
            "linewidth": 2.0,
            "alpha": 1.0,
            "zorder": 10,
        }
    return {
        "color": f"C{index}",
        "linewidth": 1.1,
        "alpha": 0.8,
        "zorder": index + 1,
    }


def _plot_spectra(
    releases: list[ReleaseData],
    mode: str,
    polarization: int,
    output_path: Path,
) -> None:
    _, plt = _dependencies()
    fig, axes = plt.subplots(
        5,
        4,
        figsize=(20, 16),
        sharex=True,
        constrained_layout=False,
    )
    flat_axes = axes.flatten()
    final_index = len(releases) - 1
    legend_handles: list[Any] = []
    legend_labels: list[str] = []

    for beam_index in range(19):
        axis = flat_axes[beam_index]
        for release_index, release in enumerate(releases):
            x_values, y_values = downsample_spectrum(
                release.frequency,
                release.tcals[mode][beam_index, :, polarization],
            )
            style = _line_style(release_index, final_index)
            (line,) = axis.plot(x_values, y_values, **style)
            if beam_index == 0:
                legend_handles.append(line)
                suffix = " (new)" if release_index == final_index else ""
                legend_labels.append(f"{release.date}{suffix}")
        axis.set_title(f"M{beam_index + 1:02d}")
        axis.grid(alpha=0.25)
        if beam_index // 4 == 4:
            axis.set_xlabel("Frequency (MHz)")
        if beam_index % 4 == 0:
            axis.set_ylabel("Tcal (K)")

    legend_axis = flat_axes[-1]
    legend_axis.axis("off")
    legend_axis.legend(
        legend_handles,
        legend_labels,
        loc="center",
        fontsize=12,
        frameon=False,
    )
    fig.suptitle(
        f"Tcal spectra comparison: {mode}, "
        f"{POLARIZATION_NAMES[polarization]} "
        f"(1 MHz mean bins)",
        fontsize=18,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _plot_relative_beams(
    releases: list[ReleaseData],
    output_path: Path,
) -> None:
    np, plt = _dependencies()
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), sharex=True)
    beam_numbers = np.arange(1, 20)
    final_index = len(releases) - 1

    for mode_index, mode in enumerate(MODES):
        for polarization in (0, 1):
            axis = axes[mode_index, polarization]
            for release_index, release in enumerate(releases):
                low, middle, high = relative_beam_statistics(
                    release.frequency,
                    release.tcals[mode],
                    polarization,
                )
                style = _line_style(release_index, final_index)
                suffix = " (new)" if release_index == final_index else ""
                axis.errorbar(
                    beam_numbers,
                    middle,
                    yerr=(middle - low, high - middle),
                    marker="o",
                    markersize=3.5,
                    capsize=2,
                    label=f"{release.date}{suffix}",
                    **style,
                )
            axis.axhline(1.0, color="0.65", linewidth=0.8)
            axis.set_title(f"{mode}, {POLARIZATION_NAMES[polarization]}")
            axis.set_xticks(beam_numbers)
            axis.grid(alpha=0.25)
            if mode_index == 1:
                axis.set_xlabel("Beam")
            if polarization == 0:
                axis.set_ylabel("Tcal / Tcal(M01)")
            axis.legend(fontsize=8)

    fig.suptitle(
        "Relative beam comparison "
        "(1330-1430 MHz, 5 MHz median bins; bars show 16-84%)",
        fontsize=15,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def generate_comparison(
    *,
    current_date: str,
    current_release_dir: Path,
    historical_release_dirs: list[tuple[str, Path]],
    output_dir: Path,
) -> dict[str, Any]:
    """Create four spectral plots, one relative-beam plot and a JSON summary."""
    np, _ = _dependencies()
    validate_date(current_date)
    if not historical_release_dirs:
        raise ComparisonError("At least one historical release is required")

    history = sorted(historical_release_dirs, key=lambda item: item[0])
    history_dates = [date for date, _ in history]
    if len(history_dates) != len(set(history_dates)):
        raise ComparisonError("Historical release dates must be unique")
    if any(date >= current_date for date in history_dates):
        raise ComparisonError(
            "Every historical release must be older than the current release"
        )

    releases = [load_release(path, date) for date, path in history]
    releases.append(load_release(current_release_dir, current_date))
    reference_frequency = releases[-1].frequency
    for release in releases[:-1]:
        if not np.array_equal(release.frequency, reference_frequency):
            raise ComparisonError(
                f"{release.date} uses a different frequency grid from {current_date}"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    for mode in MODES:
        for polarization, polarization_name in enumerate(POLARIZATION_NAMES):
            output_path = (
                output_dir
                / f"tcal-spectra-{mode}-{polarization_name.lower()}.png"
            )
            _plot_spectra(releases, mode, polarization, output_path)
            image_paths.append(output_path)

    relative_path = output_dir / "tcal-relative-beams.png"
    _plot_relative_beams(releases, relative_path)
    image_paths.append(relative_path)

    result = {
        "schema_version": 1,
        "manual_review_required": True,
        "current_date": current_date,
        "history_dates": history_dates,
        "settings": {
            "spectrum_frequency_range_mhz": list(
                DEFAULT_SPECTRUM_RANGE_MHZ
            ),
            "spectrum_bin_mhz": DEFAULT_SPECTRUM_BIN_MHZ,
            "relative_frequency_range_mhz": list(
                DEFAULT_RELATIVE_RANGE_MHZ
            ),
            "relative_bin_mhz": DEFAULT_RELATIVE_BIN_MHZ,
        },
        "images": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": file_digest(path),
            }
            for path in image_paths
        ],
    }
    summary_path = output_dir / "comparison-summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    result["summary"] = {
        "path": str(summary_path),
        "size": summary_path.stat().st_size,
        "sha256": file_digest(summary_path),
    }
    return result


def generate_comparison_from_archives(
    *,
    current_date: str,
    current_release_dir: Path,
    historical_archives: list[tuple[str, Path]],
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".comparison-history-",
        dir=output_dir.parent,
    ) as temporary:
        extraction_root = Path(temporary)
        historical_release_dirs = []
        for date, archive_path in historical_archives:
            release_dir = extract_release_zip(
                archive_path,
                date,
                extraction_root / date,
            )
            historical_release_dirs.append((date, release_dir))
        return generate_comparison(
            current_date=current_date,
            current_release_dir=current_release_dir,
            historical_release_dirs=historical_release_dirs,
            output_dir=output_dir,
        )


def _parse_history(value: str) -> tuple[str, Path]:
    date, separator, path_value = value.partition("=")
    if not separator or not path_value:
        raise argparse.ArgumentTypeError(
            "--history must use the form YYYYMMDD=/path/to/YYYYMMDD.zip"
        )
    try:
        validate_date(date)
    except Exception as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    path = Path(path_value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"History ZIP does not exist: {path}")
    return date, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="new calibration date")
    parser.add_argument(
        "--current",
        required=True,
        type=Path,
        help="directory containing the new high/low FITS files",
    )
    parser.add_argument(
        "--history",
        action="append",
        required=True,
        type=_parse_history,
        metavar="DATE=ZIP",
        help="historical release ZIP; repeat for more than one date",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="directory for comparison PNG and JSON files",
    )
    args = parser.parse_args()

    try:
        result = generate_comparison_from_archives(
            current_date=args.date,
            current_release_dir=args.current,
            historical_archives=args.history,
            output_dir=args.output,
        )
    except (ComparisonError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")

    print(
        f"Created {len(result['images'])} comparison images in {args.output}"
    )
    print(f"Compared with: {', '.join(result['history_dates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
