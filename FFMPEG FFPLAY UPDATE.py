#!/usr/bin/env python3
"""
FFmpeg Updater - Minimal ASCII version
Downloads and extracts the latest BtbN FFmpeg Windows build directly to the script directory.
Uses release metadata (release_id, author_id) for accurate update detection.
"""
import sys
import os
import shutil
import requests
import subprocess
import zipfile
import json
from pathlib import Path

GITHUB_API_URL = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
SCRIPT_DIR = Path(__file__).resolve().parent
VERSION_FILE = SCRIPT_DIR / "ffmpeg.version.txt"
TEMP_ARCHIVE = SCRIPT_DIR / "ffmpeg.temp.zip"
ASSET_PREFIX = "ffmpeg-master-latest-win64-gpl."
ASSET_SUFFIX = ".zip"

def format_size(bytes_size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"

def load_version_metadata() -> dict:
    """Load and parse version metadata from file."""
    if not VERSION_FILE.exists():
        return {}
    try:
        content = VERSION_FILE.read_text().strip()
        # Support both old format (plain asset name) and new JSON format
        if content.startswith("{"):
            return json.loads(content)
        else:
            # Legacy: just asset name
            return {"asset_name": content}
    except Exception:
        return {}

def save_version_metadata(release_id: int, author_id: int, asset_name: str, updated_at: str) -> None:
    """Save release metadata to version file in JSON format."""
    metadata = {
        "release_id": release_id,
        "author_id": author_id,
        "asset_name": asset_name,
        "updated_at": updated_at
    }
    VERSION_FILE.write_text(json.dumps(metadata, indent=2))

def is_update_needed(latest_release: dict, latest_asset: dict, local_meta: dict) -> bool:
    """Determine if an update is needed based on release metadata."""
    # If no local metadata exists, update is needed
    if not local_meta:
        return True
    
    # Compare release ID (primary indicator - changes when release content changes)
    if local_meta.get("release_id") != latest_release.get("id"):
        return True
    
    # Fallback: compare asset name and author ID for extra safety
    if local_meta.get("asset_name") != latest_asset.get("name"):
        return True
    if local_meta.get("author_id") != latest_release.get("author", {}).get("id"):
        return True
    
    # Verify ffmpeg.exe still exists
    if not (SCRIPT_DIR / "ffmpeg.exe").exists():
        return True
        
    return False

def main() -> bool:
    print("=== FFmpeg Updater ===")
    print("Repo: BtbN/FFmpeg-Builds")
    print("=======================")
    print()
    
    # Get latest release
    print("[ ] Checking GitHub...")
    try:
        resp = requests.get(GITHUB_API_URL, timeout=15)
        resp.raise_for_status()
        release = resp.json()
    except Exception as e:
        print(f"[X] GitHub error: {e}")
        return False
    
    # Find matching asset
    asset = None
    for a in release.get("assets", []):
        if a["name"].startswith(ASSET_PREFIX) and a["name"].endswith(ASSET_SUFFIX):
            asset = a
            break
    
    if not asset:
        print("[X] No matching asset found")
        return False
    
    latest_asset_name = asset["name"]
    release_id = release.get("id")
    author_id = release.get("author", {}).get("id")
    updated_at = release.get("updated_at")
    
    local_meta = load_version_metadata()
    local_asset_name = local_meta.get("asset_name", "None")
    
    print(f"[i] Latest: {latest_asset_name}")
    print(f"[i] Local:  {local_asset_name}")
    print(f"[i] Release ID: {release_id} | Author ID: {author_id}")
    print()
    
    # Check if update is needed using metadata
    if not is_update_needed(release, asset, local_meta):
        print("[+] Already up to date")
        return True
    
    # Show size and download
    if size := asset.get("size"):
        print(f"[i] Size: {format_size(size)}")
    
    print("[ ] Downloading...")
    try:
        with requests.get(asset["browser_download_url"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(TEMP_ARCHIVE, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        print("[+] Download complete")
    except Exception as e:
        print(f"[X] Download failed: {e}")
        TEMP_ARCHIVE.unlink(missing_ok=True)
        return False
    
    # Extract to script directory
    print("[ ] Extracting...")
    try:
        with zipfile.ZipFile(TEMP_ARCHIVE, 'r') as zip_ref:
            zip_ref.extractall(SCRIPT_DIR)
        TEMP_ARCHIVE.unlink(missing_ok=True)
        
        # BtbN archives contain a root folder like "ffmpeg-master-latest-win64-gpl"
        # Find that folder and move executables from its bin/ subdirectory
        extracted_root = None
        for item in SCRIPT_DIR.iterdir():
            if item.is_dir() and item.name.startswith("ffmpeg-") and (item / "bin").exists():
                extracted_root = item
                break
        
        if extracted_root:
            bin_dir = extracted_root / "bin"
            for exe in ["ffmpeg.exe", "ffplay.exe", "ffprobe.exe"]:
                src = bin_dir / exe
                if src.exists():
                    shutil.move(str(src), str(SCRIPT_DIR / exe))
            # Clean up the extracted root folder
            shutil.rmtree(extracted_root, ignore_errors=True)
        else:
            print("[!] Warning: Could not locate extracted bin/ directory")
                    
    except Exception as e:
        print(f"[X] Extraction error: {e}")
        TEMP_ARCHIVE.unlink(missing_ok=True)
        return False
    
    # Verify ffmpeg.exe exists
    if not (SCRIPT_DIR / "ffmpeg.exe").exists():
        print("[X] ffmpeg.exe not found after extraction")
        return False
    
    # Save version metadata (release_id, author_id, asset_name, updated_at)
    save_version_metadata(release_id, author_id, latest_asset_name, updated_at)
    
    # Show version
    try:
        result = subprocess.run(
            [str(SCRIPT_DIR / "ffmpeg.exe"), "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.splitlines()[0].split(" Copyright")[0][:60]
            print(f"[+] Updated: {version}")
    except Exception:
        pass
    
    return True

if __name__ == "__main__":
    success = main()
    print()
    print("=======================")
    sys.exit(0 if success else 1)