# hifast-tcal-data

This repository hosts the noise diode temperature (Tcal) data for the [HiFAST](https://github.com/jyingjie/hifast) pipeline.

The data is organized by date (YYYYMMDD) corresponding to specific calibration sessions of the FAST telescope.

## Usage

### For HiFAST Users
HiFAST (v1.4+) automatically downloads data from this repository (or its R2 mirror) as needed. You generally do not need to interact with this repository directly.

### For Maintainers

#### Adding New Data
1.  Place the new Tcal data folder (e.g., `20251101`) into the repository root or a source directory.
2.  Run `python3 prepare_release.py`. This script will:
    *   Find all valid `20xxxxxx` data folders.
    *   Create corresponding `.zip` archives.
    *   Update `manifest.json`.
    *   Generate `upload_releases.sh`.
3.  Upload the new release to GitHub:
    *   **Option A (Recommended)**: Use the generated `upload_releases.sh` (requires `gh` CLI).
    *   **Option B**: Manually create a release on GitHub for the tag (e.g., `20251101`) and upload the zip file.

#### Automated R2 Sync (GitHub Actions)
When a new release is **published** on GitHub, a GitHub Action workflow (`.github/workflows/release-to-r2.yml`) is automatically triggered.
*   It downloads the assets (zip files) from the new release.
*   It uses `upload_to_r2.py` to upload them to the configured Cloudflare R2 bucket.
*   This ensures the R2 mirror is always in sync with GitHub Releases.

#### Scripts
*   `prepare_release.py`: Scans folders, creates zips, updates manifest, and generates upload commands.
*   `upload_to_r2.py`: Uploads files to R2/S3. Reusable manually or by CI.
*   `.github/workflows/release-to-r2.yml`: CI definition for R2 sync.

#### Configuration
(Only needed for manual R2 upload or local testing)
Copy `.env.example` to `.env` and fill in your R2 credentials.