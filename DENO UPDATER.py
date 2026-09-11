#!/usr/bin/env python3
"""
Deno Updater - ASCII-only version for Windows compatibility
Downloads deno-x86_64-pc-windows-msvc.exe to script directory
"""
import os
import sys
import hashlib
import requests
import json
from pathlib import Path

# Configuration
DL_BASE_URL = "https://dl.deno.land"
RELEASE_LATEST_URL = f"{DL_BASE_URL}/release-latest.txt"
SCRIPT_DIR = Path(__file__).resolve().parent
TARGET_FILENAME = SCRIPT_DIR / "deno.exe"
TEMP_FILENAME = SCRIPT_DIR / "deno.temp.exe"
VERSION_FILE = SCRIPT_DIR / "deno.version.txt"
TARGET_TRIPLE = "x86_64-pc-windows-msvc"  # Windows 64-bit

def get_sha256(path):
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def read_local_version():
    """Read stored version from version file."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return None

def write_local_version(version):
    """Write version to version file."""
    VERSION_FILE.write_text(version)

def download_latest_deno():
    print("[ ] Checking dl.deno.land for latest Deno release...")
    
    # Fetch latest version tag (e.g., "v1.40.0")
    try:
        resp = requests.get(RELEASE_LATEST_URL, timeout=15)
        resp.raise_for_status()
        latest_version = resp.text.strip()
    except requests.RequestException as e:
        print("[X] Failed to fetch latest version: {}".format(e))
        return False

    if not latest_version.startswith("v"):
        print("[X] Unexpected version format: {}".format(latest_version))
        return False

    local_version = read_local_version()
    print("[i] Latest version: {}".format(latest_version))
    print("[i] Local version:  {}".format(local_version or "None"))

    # Skip if already up to date
    if latest_version == local_version:
        if TARGET_FILENAME.exists():
            print("[+] No update needed. Already on the latest version.")
            print("    Deno {} SHA256: {}...".format(local_version, get_sha256(TARGET_FILENAME)[:12]))
            return True
        else:
            print("[!] Version file exists but deno.exe is missing. Re-downloading...")

    # Construct download URL
    download_url = f"{DL_BASE_URL}/release/{latest_version}/deno-{TARGET_TRIPLE}.zip"
    print("[ ] Downloading: deno-{}.zip".format(TARGET_TRIPLE))
    print("[ ] From: {}".format(download_url))

    # Download ZIP archive
    try:
        with requests.get(download_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            # Stream to temp file
            temp_zip = SCRIPT_DIR / "deno.temp.zip"
            with open(temp_zip, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as e:
        print("[X] Download failed: {}".format(e))
        return False

    # Extract deno.exe from ZIP
    try:
        import zipfile
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            # Extract only deno.exe to temp location
            with zip_ref.open('deno.exe') as src, open(TEMP_FILENAME, 'wb') as dst:
                while True:
                    chunk = src.read(8192)
                    if not chunk:
                        break
                    dst.write(chunk)
        temp_zip.unlink()
    except Exception as e:
        print("[X] Extraction failed: {}".format(e))
        if temp_zip.exists():
            temp_zip.unlink()
        return False

    # Verify file exists and is executable
    if not TEMP_FILENAME.exists() or TEMP_FILENAME.stat().st_size == 0:
        print("[X] Extracted file is missing or empty")
        return False

    # Atomic replacement
    try:
        if TARGET_FILENAME.exists():
            TARGET_FILENAME.unlink()
        TEMP_FILENAME.rename(TARGET_FILENAME)
    except Exception as e:
        print("[X] Failed to replace deno.exe: {}".format(e))
        return False

    # Save version and show summary
    write_local_version(latest_version)
    final_hash = get_sha256(TARGET_FILENAME)
    print("[+] Deno {} installed successfully".format(latest_version))
    print("    SHA256: {}...".format(final_hash[:12]))
    
    # Optional: Show version info
    try:
        import subprocess
        result = subprocess.run([str(TARGET_FILENAME), "--version"], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                print("    {}".format(line))
    except Exception:
        pass

    return True

if __name__ == "__main__":
    print("=== Deno Updater (ASCII Mode) ===")
    success = download_latest_deno()
    print("=================================")
    sys.exit(0 if success else 1)