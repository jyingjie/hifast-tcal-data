#!/usr/bin/env python3
"""Convert FAST noise-diode calibration archives to HiFAST FITS files."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


CHANNEL_COUNT = 65536
BEAM_COUNT = 19
POLARIZATIONS = ("a", "b")
MODES = ("high", "low")


class ConversionError(RuntimeError):
    """Raised when source files cannot be safely converted."""


def _dependencies() -> tuple[Any, Any]:
    try:
        import numpy as np
        from astropy.io import fits
    except ImportError as exc:
        raise ConversionError(
            "numpy and astropy are required; install requirements.txt"
        ) from exc
    return np, fits


def validate_date(value: str) -> str:
    if not re.fullmatch(r"20\d{6}", value):
        raise ConversionError(f"Invalid calibration date: {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ConversionError(f"Invalid calibration date: {value!r}") from exc
    return value


def canonical_frequency() -> Any:
    np, _ = _dependencies()
    channel_width = 500.0 / CHANNEL_COUNT
    return 1000.0 + (np.arange(CHANNEL_COUNT, dtype=np.float64) + 0.5) * (
        channel_width
    )


def expected_archive_names(mode: str) -> set[str]:
    if mode not in MODES:
        raise ConversionError(f"Unknown noise mode: {mode}")
    names = {"freq.dat"}
    for beam in range(1, BEAM_COUNT + 1):
        for polarization in POLARIZATIONS:
            names.add(
                f"T_noise_W_{mode}_{beam:02d}{polarization}.dat"
            )
    return names


def extract_archive(archive_path: Path, destination: Path, mode: str) -> None:
    expected = expected_archive_names(mode)
    destination.mkdir(parents=True, exist_ok=False)
    found: set[str] = set()

    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ConversionError(f"Could not open archive {archive_path}") from exc

    with archive:
        for member in archive.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ConversionError(
                    f"Unsafe path {member.name!r} in {archive_path}"
                )
            if member.isdir():
                continue
            if not member.isfile():
                raise ConversionError(
                    f"Links and special files are not allowed: {member.name!r}"
                )
            if len(member_path.parts) != 1:
                raise ConversionError(
                    f"Archive files must be stored at the top level: {member.name!r}"
                )
            filename = member_path.name
            if filename not in expected:
                raise ConversionError(
                    f"Unexpected file {filename!r} in {archive_path}"
                )
            if filename in found:
                raise ConversionError(
                    f"Duplicate file {filename!r} in {archive_path}"
                )
            source = archive.extractfile(member)
            if source is None:
                raise ConversionError(
                    f"Could not read {filename!r} from {archive_path}"
                )
            with source, (destination / filename).open("xb") as target:
                shutil.copyfileobj(source, target)
            found.add(filename)

    missing = sorted(expected - found)
    if missing:
        raise ConversionError(
            f"{archive_path} is missing {len(missing)} required files: "
            + ", ".join(missing[:5])
        )


def _load_vector(path: Path, *, expected_size: int = CHANNEL_COUNT) -> Any:
    np, _ = _dependencies()
    try:
        # The official files use groups of five or six values per line and a
        # shorter final line. Treat the file as one whitespace-separated stream;
        # the line breaks do not define a two-dimensional table.
        values = np.fromfile(path, dtype=np.float64, sep=" ").reshape(-1)
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Could not read numeric data from {path}") from exc
    if values.size != expected_size:
        raise ConversionError(
            f"{path} contains {values.size} values; expected {expected_size}"
        )
    if not np.isfinite(values).all():
        raise ConversionError(f"{path} contains NaN or infinite values")
    return values


def load_mode(directory: Path, mode: str) -> tuple[Any, Any]:
    np, _ = _dependencies()
    expected_frequency = canonical_frequency()
    raw_frequency = _load_vector(directory / "freq.dat")
    # Some official files print four decimals, while the 202607 low file prints
    # two decimals and therefore contains adjacent duplicate values. The FITS
    # product always uses the exact canonical grid below.
    if np.any(np.diff(raw_frequency) < 0):
        raise ConversionError(f"{directory / 'freq.dat'} is decreasing")
    max_difference = float(np.max(np.abs(raw_frequency - expected_frequency)))
    if max_difference > 0.006:
        raise ConversionError(
            f"{directory / 'freq.dat'} does not match the W-band frequency grid; "
            f"maximum difference is {max_difference:g} MHz"
        )

    beams: list[Any] = []
    for beam in range(1, BEAM_COUNT + 1):
        polarizations = [
            _load_vector(
                directory
                / f"T_noise_W_{mode}_{beam:02d}{polarization}.dat"
            )
            for polarization in POLARIZATIONS
        ]
        beams.append(np.column_stack(polarizations))

    tcals = np.stack(beams).astype(np.float32, copy=False)
    expected_shape = (BEAM_COUNT, CHANNEL_COUNT, len(POLARIZATIONS))
    if tcals.shape != expected_shape:
        raise ConversionError(
            f"{mode} TCAL shape is {tcals.shape}; expected {expected_shape}"
        )
    if not np.isfinite(tcals).all():
        raise ConversionError(f"{mode} TCAL data contains NaN or infinite values")
    return expected_frequency, tcals


def write_fits(path: Path, frequency: Any, tcals: Any) -> None:
    np, fits = _dependencies()
    record = np.rec.array(
        [(frequency, tcals)],
        dtype=np.dtype(
            [
                ("FREQ", frequency.dtype, frequency.shape),
                ("TCAL", tcals.dtype, tcals.shape),
            ]
        ),
    )
    table = fits.BinTableHDU(record)
    table.writeto(path, overwrite=True)


def validate_fits(path: Path) -> tuple[Any, Any]:
    np, fits = _dependencies()
    try:
        with fits.open(path, memmap=False, checksum=True) as hdus:
            if len(hdus) != 2 or hdus[1].data is None or len(hdus[1].data) != 1:
                raise ConversionError(f"{path} does not contain one binary-table row")
            frequency = np.asarray(hdus[1].data["FREQ"][0], dtype=np.float64)
            tcals = np.asarray(hdus[1].data["TCAL"][0], dtype=np.float32)
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Could not validate FITS file {path}") from exc

    expected_tcal_shape = (BEAM_COUNT, CHANNEL_COUNT, len(POLARIZATIONS))
    if frequency.shape != (CHANNEL_COUNT,):
        raise ConversionError(
            f"{path} FREQ shape is {frequency.shape}; expected {(CHANNEL_COUNT,)}"
        )
    if tcals.shape != expected_tcal_shape:
        raise ConversionError(
            f"{path} TCAL shape is {tcals.shape}; expected {expected_tcal_shape}"
        )
    if not np.isfinite(frequency).all() or not np.isfinite(tcals).all():
        raise ConversionError(f"{path} contains NaN or infinite values")
    if not np.all(np.diff(frequency) > 0):
        raise ConversionError(f"{path} frequency is not strictly increasing")
    return frequency, tcals


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_md5_file(paths: list[Path], output_path: Path) -> None:
    with output_path.open("w", encoding="ascii") as handle:
        for path in paths:
            handle.write(f"{file_digest(path, 'md5')}  {path.name}\n")


def package_release(paths: list[Path], zip_path: Path, date: str) -> None:
    release_date = datetime.strptime(date, "%Y%m%d")
    zip_timestamp = (
        release_date.year,
        release_date.month,
        release_date.day,
        0,
        0,
        0,
    )
    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in paths:
            info = zipfile.ZipInfo(path.name, date_time=zip_timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with path.open("rb") as source, archive.open(
                info,
                mode="w",
                force_zip64=True,
            ) as target:
                shutil.copyfileobj(source, target)
    with zipfile.ZipFile(zip_path, mode="r") as archive:
        bad_file = archive.testzip()
    if bad_file is not None:
        raise ConversionError(f"ZIP integrity check failed at {bad_file}")


def convert_archives(
    *,
    date: str,
    high_archive: Path,
    low_archive: Path,
    output_dir: Path,
    zip_path: Path | None = None,
) -> dict[str, Any]:
    np, _ = _dependencies()
    validate_date(date)
    high_archive = high_archive.resolve()
    low_archive = low_archive.resolve()
    for archive_path in (high_archive, low_archive):
        if not archive_path.is_file():
            raise ConversionError(f"Archive does not exist: {archive_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    if zip_path is None:
        zip_path = output_dir.parent / f"{date}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=f".extract-{date}-",
        dir=output_dir.parent,
    ) as temporary:
        extract_root = Path(temporary)
        generated: list[Path] = []
        mode_frequencies: dict[str, Any] = {}
        for mode, archive_path in (
            ("high", high_archive),
            ("low", low_archive),
        ):
            mode_dir = extract_root / mode
            extract_archive(archive_path, mode_dir, mode)
            frequency, tcals = load_mode(mode_dir, mode)
            output_path = output_dir / f"CAL.{date}.{mode}.W.fits"
            write_fits(output_path, frequency, tcals)
            checked_frequency, _ = validate_fits(output_path)
            mode_frequencies[mode] = checked_frequency
            generated.append(output_path)

    if not np.array_equal(mode_frequencies["high"], mode_frequencies["low"]):
        raise ConversionError("high and low FITS files use different frequencies")

    md5_path = output_dir / f"md5sum.{date}.txt"
    write_md5_file(generated, md5_path)
    package_paths = generated + [md5_path]
    package_release(package_paths, zip_path, date)

    return {
        "date": date,
        "fits": [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": file_digest(path),
            }
            for path in generated
        ],
        "md5": str(md5_path),
        "zip": {
            "path": str(zip_path),
            "size": zip_path.stat().st_size,
            "sha256": file_digest(zip_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="calibration date in YYYYMMDD")
    parser.add_argument("--high", required=True, type=Path, help="high tar archive")
    parser.add_argument("--low", required=True, type=Path, help="low tar archive")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="directory for FITS and MD5 files",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        help="output ZIP path; defaults to DATE.zip beside --output",
    )
    args = parser.parse_args()

    try:
        result = convert_archives(
            date=args.date,
            high_archive=args.high,
            low_archive=args.low,
            output_dir=args.output,
            zip_path=args.zip,
        )
    except (OSError, ConversionError) as exc:
        parser.exit(1, f"error: {exc}\n")

    print(f"Created {result['zip']['path']}")
    for item in result["fits"]:
        print(f"  {item['path']} ({item['size']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
