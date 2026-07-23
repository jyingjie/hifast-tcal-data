# hifast-tcal-data

This repository hosts the noise diode temperature (Tcal) data for the [HiFAST](https://github.com/jyingjie/hifast) pipeline.

The data is organized by date (YYYYMMDD) corresponding to specific calibration sessions of the FAST telescope.

## Usage

### For HiFAST Users
HiFAST (v1.4+) automatically downloads data from this repository (or its R2 mirror) as needed. You generally do not need to interact with this repository directly.

### For Maintainers

#### Automated Update

The repository checks the official FAST noise-diode calibration page every
Monday at 02:00 UTC. The workflow can also be started manually from GitHub
Actions.

When no new date is present, the workflow only checks the upstream page and
ensures that the current R2 manifest matches `main`. Comparison images are not
regenerated.

When one or more new calibration dates are found, the workflow processes them
in date order:

1. Downloads the report and the high/low source archives.
2. Extracts the test date from the PDF and checks it against the PDF filename.
3. Verifies source hashes and archive structure.
4. Converts all 19 beams and two polarizations to FITS.
5. Validates the FITS files and creates the release ZIP.
6. Compares the new FITS with the three most recent releases and creates review
   plots.
7. Publishes the ZIP, five PNG files and JSON summary as separate GitHub
   Release assets.
8. Uploads the immutable `YYYYMMDD.zip` object to R2.
9. Updates and pushes `manifest.json` and `sources.json` to `main`.
10. Uploads the exact updated `manifest.json` to R2.

The updater never infers a new day from `manifest.json`. If the PDF filename
contains only a month, it reads the report text and requires one unambiguous
test date. A filename/text conflict stops the build.

Release data and both manifests are exposed only after all download,
conversion and structural validation steps pass. An existing ZIP is never
overwritten with different content. Re-running an interrupted job either
reuses matching Release/R2 objects or stops on a hash mismatch.

To run the same process locally:

```bash
python -m pip install -r requirements.txt
python -m tcal_pipeline.check_upstream
python -m tcal_pipeline.update_from_fast --date 20260708 --output build
python -m tcal_pipeline.promote_release \
  build/20260708/source-record.json \
  --verify-only
```

Validate the exact Release asset names without changing GitHub:

```bash
python -m tcal_pipeline.publish_release \
  build/20260708/source-record.json
```

The automated workflow then performs the equivalent of:

```bash
python -m tcal_pipeline.publish_release \
  build/20260708/source-record.json \
  --publish
python -m tcal_pipeline.upload_to_r2 \
  --zip build/20260708/20260708.zip \
  --date 20260708
python -m tcal_pipeline.promote_release \
  build/20260708/source-record.json
# Commit and push manifest.json and sources.json here.
python -m tcal_pipeline.upload_to_r2 --manifest manifest.json
```

The Release contains `20260708.zip`, five PNG files and
`comparison-summary.json` as separate assets. The HiFAST data URL remains:

```text
https://github.com/jyingjie/hifast-tcal-data/releases/download/20260708/20260708.zip
```

The publishing program never adds images to the ZIP and never overwrites an
existing data ZIP. The periodic workflow uploads only the exact
`YYYYMMDD.zip` to the data path; PNG and JSON review assets remain in the
GitHub Release.
The complete design and failure-handling rules are described in
[`AUTOMATION_PLAN.md`](docs/AUTOMATION_PLAN.md).
The raw text layout and exact frequency rules are documented separately in
[`FREQUENCY_AND_RAW_FORMAT.md`](docs/FREQUENCY_AND_RAW_FORMAT.md).
The calibration-date sources and PDF date checks are documented in
[`DATE_DETECTION.md`](docs/DATE_DETECTION.md).
The historical comparison plots and manual checks are documented in
[`VISUAL_VALIDATION.md`](docs/VISUAL_VALIDATION.md).

#### R2 Recovery Workflow

Normal R2 publication is part of `.github/workflows/check-tcal-updates.yml`.
The separate `.github/workflows/release-to-r2.yml` workflow is manual and is
only intended to restore one exact Release ZIP to R2. It does not modify either
copy of `manifest.json`.

#### Scripts

The Python implementation is in `tcal_pipeline/`, tests are in `tests/`, and
design notes are in `docs/`.

- `tcal_pipeline.check_upstream`: Checks the official FAST page.
- `tcal_pipeline.update_from_fast`: Downloads, converts and validates one date.
- `tcal_pipeline.publish_release`: Publishes ZIP and review assets.
- `tcal_pipeline.upload_to_r2`: Uploads an exact ZIP or manifest to R2.
- `.github/workflows/check-tcal-updates.yml`: Weekly automatic publication.
- `.github/workflows/release-to-r2.yml`: Manual recovery of one Release ZIP.

#### Configuration

Manual R2 uploads require the same `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` and optional `R2_PREFIX` environment
variables used by GitHub Actions.
