import winreg
import json
import os
import sys

# Paths
MANIFEST_PATH = r"D:\TEST\com.wrapper.ytdlp_host.json"
HOST_NAME = "com.wrapper.ytdlp_host"
REGISTRY_PATH = r"Software\Google\Chrome\NativeMessagingHosts"
BAT_PATH = r"D:\TEST\run_host.bat"

print("="*60)
print(" CHROME NATIVE MESSAGING AUTO-SETUP & DIAGNOSTIC TOOL ")
print("="*60)

# 1. Get Extension ID
print("\n[ STEP 1 ] Extension ID")
ext_id = input("Paste your Extension ID here (or press Enter to skip): ").strip()

allowed_origins = []
if ext_id:
    allowed_origins = [f"chrome-extension://{ext_id}/"]
    print(f"    -> Using allowed_origins: {allowed_origins}")
else:
    print("    -> WARNING: Skipping allowed_origins (Chrome may block the host).")

# 2. Create / Fix the JSON Manifest
print("\n[ STEP 2 ] Generating JSON Manifest...")
manifest_data = {
    "name": HOST_NAME,
    "description": "YT-DLP Native Messaging Host",
    "path": BAT_PATH,
    "type": "stdio",
    "allowed_origins": allowed_origins
}

try:
    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2)
    print(f"    [OK] Saved manifest to: {MANIFEST_PATH}")
except Exception as e:
    print(f"    [ERROR] Failed to write manifest: {e}")
    sys.exit(1)

# 3. Check run_host.bat exists
print("\n[ STEP 3 ] Checking run_host.bat...")
if not os.path.exists(BAT_PATH):
    print(f"    [ERROR] {BAT_PATH} not found!")
    print("    Please ensure you created run_host.bat in D:\\TEST\\")
else:
    print(f"    [OK] Found {BAT_PATH}")

# 4. Create / Fix Registry Key (Forced 64-bit to avoid Wow6432Node issues)
print("\n[ STEP 4 ] Setting up Windows Registry...")
try:
    # Force 64-bit registry view so Chrome 64-bit can see it
    access_rights = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, access_rights)
    subkey = winreg.CreateKeyEx(key, HOST_NAME, 0, access_rights)
    
    # Set the default value to the JSON manifest path
    winreg.SetValueEx(subkey, "", 0, winreg.REG_SZ, MANIFEST_PATH)
    
    winreg.CloseKey(subkey)
    winreg.CloseKey(key)
    print(f"    [OK] Registry key created: HKCU\\{REGISTRY_PATH}\\{HOST_NAME}")
    print(f"    [OK] Registry points to: {MANIFEST_PATH}")
except Exception as e:
    print(f"    [ERROR] Failed to write to registry: {e}")
    print("    Please run this script as Administrator.")

print("\n[ STEP 5 ] FINAL INSTRUCTIONS")
print(" 1. CLOSE ALL Chrome windows completely (check system tray).")
print(" 2. Reopen Chrome.")
print(" 3. Go to a video page and click your extension icon.")
print(" 4. Open D:\\TEST\\downloader.log to see the live status!")
print("="*60)