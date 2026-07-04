# Tools

Utility scripts for EhViewer development and data migration.

## Scripts

### `extract_labels_from_device.py`

Extracts download label mappings from an EhViewer installation on a connected
Android device via ADB. Runs without root — uses `adb backup` for release
builds and `run-as` for debug builds.

**Requirements:**
- Python 3.9+ (no extra packages needed)
- ADB on PATH or Android SDK platform-tools installed
- USB debugging enabled on the device

**Usage:**
```powershell
python extract_labels_from_device.py
```

**What it does:**
1. Scans the device for all EhViewer installations (release + debug)
2. Lets you pick which one to extract from
3. Pulls `eh.db` via `adb backup` (non-root) or `run-as` (debug builds)
4. Reads `DOWNLOADS.LABEL` and `DOWNLOAD_LABELS` tables
5. Writes `download-labels-mapping.json` compatible with the app's
   **Settings → Download → Restore download labels** feature

**Output:**
- `eh_labels_<package>.json` — label mapping (GID → label name)
- `eh_labels_<package>_summary.txt` — human-readable report

**Restoring on target device:**
1. Copy the `.json` file to your EhViewer download root directory
2. Rename it to `download-labels-mapping.json`
3. Open EhViewer → Settings → Download → **Restore download labels**
