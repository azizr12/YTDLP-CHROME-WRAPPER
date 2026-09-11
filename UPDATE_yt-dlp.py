import os
import hashlib
import requests
import json

GITHUB_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
TARGET_FILENAME = os.path.join(SCRIPT_DIR, "yt-dlp.exe")
TEMP_FILENAME = os.path.join(SCRIPT_DIR, "yt-dlp.temp.exe")
VERSION_FILE = os.path.join(SCRIPT_DIR, "yt-dlp.version.txt")

def get_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def read_local_version():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    return None

def write_local_version(version):
    with open(VERSION_FILE, "w") as f:
        f.write(version)

def download_latest_exe():
    print("Checking GitHub for latest yt-dlp.exe...")
    response = requests.get(GITHUB_API_URL)
    if response.status_code != 200:
        print("ERROR: GitHub API error: {}".format(response.status_code))
        return

    data = response.json()
    latest_version = data.get("tag_name")
    local_version = read_local_version()

    print("Latest version: {}".format(latest_version))
    print("Local version: {}".format(local_version or "None"))

    if latest_version == local_version:
        print("No update needed. Already on the latest version.")
        return

    exe_asset = next((a for a in data.get("assets", []) if a["name"] == "yt-dlp.exe"), None)

    if not exe_asset:
        print("ERROR: No yt-dlp.exe file found in the latest release.")
        return

    url = exe_asset["browser_download_url"]
    print("Downloading: {}".format(exe_asset["name"]))
    print("From: {}".format(url))

    with requests.get(url, stream=True) as r:
        if r.status_code != 200:
            print("ERROR: Download failed: {}".format(r.status_code))
            return
        with open(TEMP_FILENAME, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

    os.replace(TEMP_FILENAME, TARGET_FILENAME)
    write_local_version(latest_version)
    print("yt-dlp.exe has been updated successfully.")

if __name__ == "__main__":
    download_latest_exe()