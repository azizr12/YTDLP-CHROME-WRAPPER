import subprocess
import struct
import json
import os
import sys

print("="*50)
print("YT-DLP NATIVE HOST DIAGNOSTIC TOOL")
print("="*50)

# 1. Verify all required files are present
print("\n[1] Checking required files in current directory...")
required_files = ['ytdlp_host.py', 'test_input.json', 'yt-dlp.exe', 'ffmpeg.exe']
all_present = True
for f in required_files:
    if os.path.exists(f):
        print(f"    [OK] Found: {f}")
    else:
        print(f"    [MISSING] Not found: {f}")
        all_present = False

if not all_present:
    print("\n[ERROR] Missing files detected. Please ensure yt-dlp.exe and ffmpeg.exe are in this folder and named exactly as shown.")
    sys.exit(1)

# 2. Prepare the payload
print("\n[2] Preparing Native Messaging payload...")
try:
    with open('test_input.json', 'r', encoding='utf-8') as f:
        payload = json.load(f)
    
    payload_bytes = json.dumps(payload).encode('utf-8')
    length_prefix = struct.pack('=I', len(payload_bytes))
    print("    [OK] Payload prepared successfully.")
except Exception as e:
    print(f"    [ERROR] Failed to read test_input.json: {e}")
    sys.exit(1)

# 3. Execute the host script
print("\n[3] Executing ytdlp_host.py...")
try:
    # Use sys.executable to guarantee we use the exact Python interpreter running this script
    process = subprocess.run(
        [sys.executable, 'ytdlp_host.py'],
        input=length_prefix + payload_bytes,
        capture_output=True,
        timeout=120 # Timeout after 2 minutes in case yt-dlp hangs
    )

    print(f"    Host Process Return Code: {process.returncode}")
    print(f"    Host Stdout Size: {len(process.stdout)} bytes")

    # 4. Decode and display the response
    print("\n[4] Analyzing Host Output...")
    if process.stdout and len(process.stdout) >= 4:
        resp_length = struct.unpack('=I', process.stdout[:4])[0]
        resp_data = json.loads(process.stdout[4:4+resp_length].decode('utf-8'))
        print("    [SUCCESS] Received valid JSON response from host:")
        print("    " + json.dumps(resp_data, indent=4).replace('\n', '\n    '))
    else:
        print("    [WARNING] Host did not return a valid Native Messaging response.")

    # 5. Display any errors (stderr)
    if process.stderr:
        print("\n[5] Host Error Log (Stderr):")
        print("-" * 30)
        print(process.stderr.decode('utf-8', errors='replace'))
        print("-" * 30)
    else:
        print("\n[5] No errors reported in Stderr.")

except subprocess.TimeoutExpired:
    print("    [ERROR] The host script timed out after 120 seconds. yt-dlp may be hanging.")
except Exception as e:
    print(f"    [CRITICAL ERROR] The diagnostic script crashed: {e}")

print("\n" + "="*50)
print("DIAGNOSTIC COMPLETE")
print("="*50)