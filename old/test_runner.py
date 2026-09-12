import subprocess
import struct
import json

with open('test_input.json', 'r', encoding='utf-8') as f:
    payload = json.load(f)

payload_bytes = json.dumps(payload).encode('utf-8')
length_prefix = struct.pack('=I', len(payload_bytes))

process = subprocess.run(
    ['python', 'ytdlp_host.py'],
    input=length_prefix + payload_bytes,
    capture_output=True
)

if process.stdout:
    resp_length = struct.unpack('=I', process.stdout[:4])[0]
    resp_data = json.loads(process.stdout[4:4+resp_length].decode('utf-8'))
    print("Response:", json.dumps(resp_data, indent=2))
else:
    print("No response. Check stderr:", process.stderr.decode('utf-8'))