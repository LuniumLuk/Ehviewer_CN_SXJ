#!/usr/bin/env python3
"""
Extract EhViewer download label mappings from an Android device via ADB.

Workflow:
  1. Scan device for all EhViewer installations (release + debug builds)
  2. Let user pick which one to extract from
  3. Pull eh.db via adb, extract DOWNLOADS.LABEL and DOWNLOAD_LABELS
  4. Write download-labels-mapping.json (compatible with app's Restore feature)

Usage:
  python extract_labels_from_device.py
"""

import subprocess
import sys
import os
import json
import sqlite3
import time
import tempfile
import shutil
import zlib
from pathlib import Path

# Known EhViewer package prefixes to scan for
PACKAGE_PREFIXES = [
    "com.xjs.ehviewer",       # release build
    "com.xjs.ehviewer.debug", # debug build
    "com.hippo.ehviewer",     # upstream / older builds
]
DB_RELATIVE_PATH = "databases/eh.db"
TMP_DIR = None  # set in main()

# ADB executable — try well-known SDK locations, then PATH
_ADB_EXE = None


def _find_adb() -> str | None:
    """Locate adb on this machine. Returns absolute path or None."""
    # Platform-specific candidate paths
    candidates = []
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
            os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe") if os.environ.get("ANDROID_HOME") else None,
            os.path.expandvars(r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe") if os.environ.get("ANDROID_SDK_ROOT") else None,
            r"C:\Android\Sdk\platform-tools\adb.exe",
        ]
    else:
        candidates = [
            os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
            "/usr/local/android-sdk/platform-tools/adb",
            "/opt/android-sdk/platform-tools/adb",
        ]
    # Also try ANDROID_HOME / ANDROID_SDK_ROOT env vars
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = os.environ.get(env_var)
        if val:
            exe = os.path.join(val, "platform-tools", "adb")
            if sys.platform == "win32":
                exe += ".exe"
            candidates.insert(0, exe)

    # Remove None entries and non-existing files
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    # Fallback: check PATH via shutil
    which = shutil.which("adb")
    if which:
        return which

    return None


# ── ADB helpers ──────────────────────────────────────────────────────────────

def adb(*args, check: bool = True) -> subprocess.CompletedProcess:
    """Run an adb command, return CompletedProcess."""
    if _ADB_EXE is None:
        raise RuntimeError("ADB not available — call check_adb_available() first")
    cmd = [_ADB_EXE] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def adb_lines(*args) -> list[str]:
    """Run adb, return stdout as list of non-empty lines."""
    r = adb(*args, check=False)
    if r.returncode != 0:
        return []
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


# ── Device & package scanning ────────────────────────────────────────────────

def check_adb_available() -> bool:
    """Return True if adb is found and a device is connected."""
    global _ADB_EXE
    _ADB_EXE = _find_adb()
    if _ADB_EXE is None:
        return False
    r = adb("devices", check=False)
    lines = r.stdout.strip().splitlines()
    # First line is "List of devices attached", subsequent lines have device IDs
    devices = [l for l in lines[1:] if l.strip() and "offline" not in l]
    return len(devices) > 0


def scan_ehviewer_packages() -> list[dict]:
    """
    Return list of dicts for every installed EhViewer package.
    Each dict: {package, versionName, versionCode, path, debuggable}
    """
    # Get all installed third-party packages
    raw = adb_lines("shell", "pm", "list", "packages", "-3")
    pkgs = [l.removeprefix("package:").strip() for l in raw]

    eh_pkgs = []
    for pfx in PACKAGE_PREFIXES:
        for pkg in pkgs:
            if pkg.startswith(pfx):
                eh_pkgs.append(pkg)

    if not eh_pkgs:
        return []

    results = []
    for pkg in eh_pkgs:
        info = {"package": pkg}

        # versionName / versionCode
        dumpsys = subprocess.run(
            [_ADB_EXE, "shell", "dumpsys", "package", pkg],
            capture_output=True, text=True
        )
        for line in dumpsys.stdout.splitlines():
            ls = line.strip()
            if ls.startswith("versionName="):
                info["versionName"] = ls.split("=", 1)[1]
            elif ls.startswith("versionCode="):
                info["versionCode"] = ls.split("=", 1)[1].split()[0]
            elif "dataDir=" in ls:
                info["dataDir"] = ls.split("dataDir=", 1)[1].strip()
            elif "pkgFlags=" in ls:
                # Check debug flags: FLAG_DEBUGGABLE = 0x2
                flags_str = ls.split("=", 1)[1].strip()
                flags_str = flags_str.replace("[", "").replace("]", "")
                info["debuggable"] = "DEBUGGABLE" in flags_str or "0x2" in flags_str

        # If we didn't get dataDir from dumpsys, construct it
        if "dataDir" not in info:
            info["dataDir"] = f"/data/data/{pkg}"
        if "debuggable" not in info:
            info["debuggable"] = pkg.endswith(".debug")

        results.append(info)

    return results


def find_ehdb_path(pkg: str, data_dir: str) -> str | None:
    """
    Find eh.db for the given package. Returns the full remote path, or None.
    Tries multiple known locations and resolves symlinks.
    """
    # Candidate paths (most common first)
    candidates = [
        f"{data_dir}/databases/eh.db",
        f"{data_dir}/databases/eh.db-wal",  # sometimes only WAL exists if DB wasn't closed
        f"{data_dir}/databases/eh.db-shm",
        f"{data_dir}/files/eh.db",          # some apps use filesDir
        f"{data_dir}/eh.db",                # bare data dir (unlikely but try)
    ]

    for db_path in candidates:
        # Use ls: exit 0 if it exists, non-zero if not
        r = subprocess.run(
            [_ADB_EXE, "shell", "ls", db_path],
            capture_output=True, text=True
        )
        if r.returncode == 0 and "No such file" not in r.stderr:
            # If we found a WAL/SHM, the actual db is the base name
            if db_path.endswith(("-wal", "-shm")):
                db_path = db_path[:-4] if db_path.endswith("-wal") else db_path[:-4]
                # Verify the actual .db exists
                r2 = subprocess.run(
                    [_ADB_EXE, "shell", "ls", db_path],
                    capture_output=True, text=True
                )
                if r2.returncode != 0:
                    # Only WAL exists but .db was deleted — still might be recoverable
                    # Try to find the actual db
                    pass
            return db_path

    # Fallback: do a find inside the data directory (slower but thorough)
    print(f"  → Searching for eh.db inside {data_dir} …")
    r = subprocess.run(
        [_ADB_EXE, "shell", "find", data_dir, "-name", "eh.db", "-type", "f", "2>/dev/null"],
        capture_output=True, text=True
    )
    if r.returncode == 0 and r.stdout.strip():
        found = r.stdout.strip().splitlines()
        for f in found:
            f = f.strip()
            if f:
                print(f"  ✓ Found at: {f}")
                return f
    return None


def check_db_exists(pkg_data_dir: str) -> bool:
    """Quick check: does this package have an eh.db anywhere?"""
    # Use ls on the expected path; treat exit 0 as "something exists"
    db_path = f"{pkg_data_dir}/databases/eh.db"
    r = subprocess.run(
        [_ADB_EXE, "shell", "ls", db_path],
        capture_output=True, text=True
    )
    if r.returncode == 0 and "No such file" not in r.stderr:
        return True
    # Also try find as a broader fallback
    r2 = subprocess.run(
        [_ADB_EXE, "shell", "ls", f"{pkg_data_dir}/databases/"],
        capture_output=True, text=True
    )
    if r2.returncode == 0:
        for line in r2.stdout.splitlines():
            if "eh.db" in line:
                return True
    return False


# ── Database extraction ──────────────────────────────────────────────────────

def _adb_backup_extract(pkg: str, target_dir: str) -> str | None:
    """
    Use 'adb backup' to extract the app's data, then pull eh.db from the backup.
    This is the standard non-root method — works on Android ≤ 12.
    Requires user to tap "Back up my data" on the device screen.
    """
    import gzip

    backup_ab = os.path.join(target_dir, f"{pkg}.ab")
    backup_tar = os.path.join(target_dir, f"{pkg}.tar")

    print(f"  → Trying adb backup (non-root method) …")
    print(f"  ⚠ On your device, tap 'Back up my data' when prompted!")
    print(f"  ⚠ DO NOT set a password — leave it blank and tap 'Back up'.")
    print(f"  ⚠ Waiting for you to confirm on device (30s timeout) …")

    # Run adb backup — this will prompt on the device
    r = subprocess.run(
        [_ADB_EXE, "backup", "-f", backup_ab, "-noapk", "-noshared", pkg],
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        print(f"  ✗ adb backup failed: {r.stderr.strip()}")
        return None
    if not os.path.isfile(backup_ab) or os.path.getsize(backup_ab) < 100:
        print(f"  ✗ Backup file is empty or too small (did you confirm on device?)")
        return None

    print(f"  Backup received ({os.path.getsize(backup_ab):,} bytes), extracting …")

    # The .ab file format: 24-byte header (ASCII), then deflate-compressed tar
    try:
        with open(backup_ab, "rb") as f:
            header = f.read(24)
            if not header.startswith(b"ANDROID BACKUP"):
                print(f"  ✗ Not a valid Android backup file")
                return None
            compressed = f.read()
    except OSError as e:
        print(f"  ✗ Failed to read backup: {e}")
        return None

    if len(compressed) < 50:
        print(f"  ✗ Backup appears to have no data")
        return None

    # Decompress (zlib, raw deflate — no gzip header on some versions)
    try:
        decompressed = zlib.decompress(compressed)
    except zlib.error:
        # Try with gzip wrapper
        try:
            decompressed = gzip.decompress(compressed)
        except Exception:
            print(f"  ✗ Failed to decompress backup (may be encrypted with a password)")
            return None

    with open(backup_tar, "wb") as f:
        f.write(decompressed)

    # Now search the tar for eh.db (Android backup path: apps/<pkg>/db/eh.db)
    db_local = os.path.join(target_dir, f"{pkg}_eh.db")

    # Use Python tarfile — more reliable cross-platform than system tar
    import tarfile
    try:
        with tarfile.open(backup_tar, "r") as tar:
            for member in tar.getmembers():
                # Match path ending with /db/eh.db (the Android backup layout)
                if member.name.endswith("/db/eh.db") and member.isfile():
                    member.name = os.path.basename(member.name)
                    tar.extract(member, target_dir)
                    extracted = os.path.join(target_dir, "eh.db")
                    if os.path.isfile(extracted):
                        if os.path.isfile(db_local):
                            os.remove(db_local)
                        os.rename(extracted, db_local)
                        size = os.path.getsize(db_local)
                        print(f"  ✓ Extracted eh.db from backup ({size:,} bytes)")
                        return db_local
        print(f"  ✗ eh.db not found in backup tar")
        return None
    except Exception as e:
        print(f"  ✗ tar extraction failed: {e}")
        return None


def pull_database(pkg_info: dict) -> str | None:
    """
    Pull eh.db from the device. Returns path to local copy, or None on failure.
    Tries multiple strategies in order:
      1. run-as (debuggable builds)
      2. adb backup (standard non-root method — user confirms on device)
      3. adb shell cat (needs world-readable or root)
      4. adb pull (needs adbd as root)
    """
    pkg = pkg_info["package"]
    data_dir = pkg_info.get("dataDir", f"/data/data/{pkg}")

    local_path = os.path.join(TMP_DIR, f"{pkg}_eh.db")

    # Strategy 1: run-as (only works for debuggable / debug builds)
    if pkg_info.get("debuggable"):
        # Find the db path inside the sandbox (we have access)
        remote_db = find_ehdb_path(pkg, data_dir)
        if remote_db is None:
            print(f"  ✗ Could not locate eh.db for {pkg} (debuggable)")
            return None
        print(f"  Database path: {remote_db}")

        db_rel = remote_db
        if remote_db.startswith(data_dir + "/"):
            db_rel = remote_db[len(data_dir) + 1:]

        print(f"  → Trying run-as (debuggable build) …")
        r = subprocess.run(
            [_ADB_EXE, "shell", "run-as", pkg, "cat", db_rel],
            capture_output=True
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            with open(local_path, "wb") as f:
                f.write(r.stdout)
            size = os.path.getsize(local_path)
            print(f"  ✓ Pulled via run-as ({size:,} bytes)")
            return local_path
        print(f"  ✗ run-as failed (returncode={r.returncode})")

    # Strategy 2: adb backup (standard non-root extraction)
    result = _adb_backup_extract(pkg, TMP_DIR)
    if result is not None:
        # Rename to expected filename
        if result != local_path:
            if os.path.isfile(local_path):
                os.remove(local_path)
            os.rename(result, local_path)
        return local_path

    # Strategy 3: adb shell cat (needs root or world-readable)
    # Only try if we can locate the db path
    remote_db = find_ehdb_path(pkg, data_dir)
    if remote_db is not None:
        print(f"  Database path: {remote_db}")
        print(f"  → Trying adb shell cat (may need root) …")
        r = subprocess.run(
            [_ADB_EXE, "shell", "cat", remote_db],
            capture_output=True
        )
        if r.returncode == 0 and len(r.stdout) > 100:
            if r.stdout[:15] == b"SQLite format 3\x00":
                with open(local_path, "wb") as f:
                    f.write(r.stdout)
                size = os.path.getsize(local_path)
                print(f"  ✓ Pulled via cat ({size:,} bytes)")
                return local_path
            else:
                print(f"  ✗ File doesn't look like SQLite (bad permissions?)")
        else:
            print(f"  ✗ cat failed (returncode={r.returncode})")

        # Strategy 4: adb pull (requires root or relaxed SELinux)
        print(f"  → Trying adb pull …")
        r = subprocess.run(
            [_ADB_EXE, "pull", remote_db, local_path],
            capture_output=True
        )
        if r.returncode == 0 and os.path.getsize(local_path) > 100:
            size = os.path.getsize(local_path)
            print(f"  ✓ Pulled via adb pull ({size:,} bytes)")
            return local_path
        print(f"  ✗ adb pull failed (returncode={r.returncode})")

    return None


# ── Data extraction from SQLite ──────────────────────────────────────────────

def extract_labels_from_db(db_path: str) -> dict:
    """
    Read eh.db, return dict with:
      - labelDefs: [{id, label, time}, ...]
      - labeledDownloads: [{gid, label}, ...]
      - unlabeledCount: int
      - totalDownloads: int
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Label definitions
    try:
        label_rows = conn.execute(
            'SELECT "_id", "LABEL", "TIME" FROM "DOWNLOAD_LABELS" ORDER BY "TIME" ASC'
        ).fetchall()
    except sqlite3.Error:
        label_rows = []

    # GID → Label mappings
    try:
        mapping_rows = conn.execute(
            'SELECT "GID", "LABEL" FROM "DOWNLOADS" '
            'WHERE "LABEL" IS NOT NULL AND "LABEL" != \'\''
        ).fetchall()
    except sqlite3.Error:
        mapping_rows = []

    # Unlabeled count
    try:
        null_count = conn.execute(
            'SELECT COUNT(*) as cnt FROM "DOWNLOADS" '
            'WHERE "LABEL" IS NULL OR "LABEL" = \'\''
        ).fetchone()["cnt"]
    except sqlite3.Error:
        null_count = 0

    conn.close()

    return {
        "labelDefs": [{"id": r["_id"], "label": r["LABEL"], "time": r["TIME"]}
                      for r in label_rows],
        "labeledDownloads": [{"gid": r["GID"], "label": r["LABEL"]}
                             for r in mapping_rows],
        "unlabeledCount": null_count,
        "totalDownloads": len(mapping_rows) + null_count,
    }


# ── Output ───────────────────────────────────────────────────────────────────

# Resolve output directory: same directory as this script (tools/)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(_SCRIPT_DIR, "download-labels-mapping.json")
SUMMARY_FILE = os.path.join(_SCRIPT_DIR, "download-labels-mapping_summary.txt")


def write_output(data: dict):
    """Write the mapping JSON and a summary report to tools/."""
    mapping_json = {
        "version": 1,
        "exportTime": int(time.time() * 1000),
        "labels": {str(d["gid"]): d["label"] for d in data["labeledDownloads"]}
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping_json, f, ensure_ascii=False, indent=2)
    print(f"\n  ✓ Wrote {OUTPUT_FILE} ({len(data['labeledDownloads'])} entries)")

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write("=== EhViewer Label Extraction Report ===\n")
        f.write(f"Total downloads:     {data['totalDownloads']}\n")
        f.write(f"  Labeled:           {len(data['labeledDownloads'])}\n")
        f.write(f"  Unlabeled:         {data['unlabeledCount']}\n")
        f.write(f"Label definitions:   {len(data['labelDefs'])}\n\n")
        if data["labelDefs"]:
            from collections import Counter
            label_counts = Counter(d["label"] for d in data["labeledDownloads"])
            f.write("Per-label counts:\n")
            for lbl in sorted(data["labelDefs"], key=lambda x: x["time"]):
                count = label_counts.get(lbl["label"], 0)
                f.write(f"  [{count:4d}] {lbl['label']}\n")
    print(f"  ✓ Wrote {SUMMARY_FILE}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global TMP_DIR
    print("EhViewer Label Mapping Extractor")
    print("=" * 50)

    # 1. Check adb
    if not check_adb_available():
        print("\n✗ adb not found or no device connected.")
        print("  Tried these locations:")
        print(f"    %LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe")
        print(f"    %ANDROID_HOME%\\platform-tools\\adb.exe")
        print(f"    PATH")
        print("  Make sure Android SDK platform-tools is installed and USB debugging is enabled.")
        sys.exit(1)
    print(f"✓ ADB found: {_ADB_EXE}")
    print(f"✓ Device connected\n")

    # 2. Scan for EhViewer packages
    print("Scanning device for EhViewer installations …")
    packages = scan_ehviewer_packages()

    if not packages:
        # Try a broader search as fallback
        print("  No known EhViewer packages found with known prefixes.")
        print("  Searching for any package containing 'ehviewer' …")
        all_pkgs = adb_lines("shell", "pm", "list", "packages")
        eh_candidates = []
        for line in all_pkgs:
            pkg = line.removeprefix("package:").strip()
            if "ehviewer" in pkg.lower():
                eh_candidates.append(pkg)
        if eh_candidates:
            print(f"  Found {len(eh_candidates)} candidate(s):")
            for p in eh_candidates:
                print(f"    • {p}")
            # Build minimal info for each
            packages = []
            for p in eh_candidates:
                packages.append({"package": p, "dataDir": f"/data/data/{p}",
                                 "debuggable": p.endswith(".debug")})
        else:
            print("\n✗ No EhViewer installation found on this device.")
            sys.exit(1)

    # 3. Enrich and display
    print(f"\nFound {len(packages)} installation(s):\n")
    for i, p in enumerate(packages, 1):
        data_dir = p.get("dataDir", f"/data/data/{p['package']}")
        p["dataDir"] = data_dir
        debuggable = p.get("debuggable", False)
        ver = p.get("versionName", "unknown")
        deb = "debug" if debuggable else "release"

        # For debuggable builds, we can probe the data dir
        # For release builds, we can't — skip the check and rely on adb backup
        if debuggable:
            has_db = check_db_exists(data_dir)
            p["hasDb"] = has_db
            db_mark = "📁 has data" if has_db else "⚠ no database"
        else:
            p["hasDb"] = None  # unknown, will use adb backup
            db_mark = "🔒 sandboxed — will use adb backup"

        print(f"  [{i}] {p['package']}")
        print(f"      Version: {ver} ({deb})")
        print(f"      Data dir: {data_dir}")
        print(f"      {db_mark}")

        # If debuggable and no db at expected path, show what IS in databases/
        if debuggable and p.get("hasDb") is False:
            r = subprocess.run(
                [_ADB_EXE, "shell", "ls", "-la", f"{data_dir}/databases/"],
                capture_output=True, text=True
            )
            if r.returncode == 0 and r.stdout.strip():
                print(f"      Contents of databases/:")
                for line in r.stdout.strip().splitlines():
                    print(f"        {line}")
            else:
                print(f"      (databases/ not readable)")

    # 4. Let user pick
    if len(packages) == 1:
        choice = 1
        print(f"\nOnly one installation found — selecting [{1}] automatically.")
    else:
        print()
        while True:
            raw = input(f"Select package [1-{len(packages)}]: ").strip()
            try:
                choice = int(raw)
                if 1 <= choice <= len(packages):
                    break
            except ValueError:
                pass
            print(f"  Please enter a number between 1 and {len(packages)}.")

    selected = packages[choice - 1]
    print(f"\nSelected: {selected['package']}")

    # For debuggable builds with no DB: bail out
    # For release builds (hasDb is None): proceed with adb backup
    if selected.get("hasDb") is False:
        print("\n⚠ This debug package has no eh.db — nothing to extract.")
        print("  The app may not have been launched yet, or downloads were never used.")
        sys.exit(0)

    # 5. Pull database
    TMP_DIR = tempfile.mkdtemp(prefix="eh_extract_")
    print(f"\nPulling database from device …")
    db_local = pull_database(selected)

    if db_local is None:
        print("\n✗ Could not pull the database from the device.")
        print("  Possible reasons:")
        print("    • App is not debuggable and device is not rooted")
        print("    • SELinux is enforcing access to /data/data")
        print("    • Try: adb root  then re-run this script")
        shutil.rmtree(TMP_DIR, ignore_errors=True)
        sys.exit(1)

    # 6. Extract labels
    print(f"\nExtracting labels from database …")
    try:
        data = extract_labels_from_db(db_local)
    except sqlite3.Error as e:
        print(f"\n✗ Failed to read database: {e}")
        shutil.rmtree(TMP_DIR, ignore_errors=True)
        sys.exit(1)

    # 7. Print summary
    print(f"\n  Total downloads:     {data['totalDownloads']}")
    print(f"  Labeled downloads:   {len(data['labeledDownloads'])}")
    print(f"  Unlabeled:           {data['unlabeledCount']}")
    print(f"  Label definitions:   {len(data['labelDefs'])}")

    if data["labeledDownloads"]:
        from collections import Counter
        label_counts = Counter(d["label"] for d in data["labeledDownloads"])
        print(f"\n  Labels found:")
        for lbl, count in label_counts.most_common():
            print(f"    [{count:4d}] {lbl}")
    else:
        print("\n  ⚠ No labeled downloads found in this database.")

    # 8. Write output
    print()
    write_output(data)

    # Cleanup
    shutil.rmtree(TMP_DIR, ignore_errors=True)

    print()
    print("━" * 50)
    print("How to restore these labels in EhViewer:")
    print()
    print("  Method A — Restore from file (recommended):")
    print(f"    1. Copy tools\\download-labels-mapping.json")
    print(f"       to your phone's EhViewer download root directory.")
    print(f"       (e.g. /sdcard/Download/EhViewer/download-labels-mapping.json)")
    print(f"       via USB, adb push, or any file transfer.")
    print(f"    2. Open EhViewer → Settings → Download → Restore download labels")
    print()
    print("  Method B — adb push directly:")
    print(f'    adb push tools\\download-labels-mapping.json /sdcard/Download/EhViewer/')
    print(f"    Then: Settings → Download → Restore download labels")
    print("━" * 50)


if __name__ == "__main__":
    main()
