import sys
import json
import struct
import subprocess
import tempfile
import os
import pathlib
import logging

# ==============================================================================
# 1. Path Resolution & Logging Setup
# ==============================================================================
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
LOG_FILE = SCRIPT_DIR / "downloader.log"

# Configure logging to write to a file instead of the console (stdout)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

logging.info("="*50)
logging.info("Native Host Initialized and Waiting for Messages...")
logging.info(f"Script Directory: {SCRIPT_DIR}")
logging.info("="*50)

YTDLP_PATH = SCRIPT_DIR / "yt-dlp.exe" if (SCRIPT_DIR / "yt-dlp.exe").exists() else SCRIPT_DIR / "yt-dlp"
FFMPEG_PATH = SCRIPT_DIR / "ffmpeg.exe" if (SCRIPT_DIR / "ffmpeg.exe").exists() else SCRIPT_DIR / "ffmpeg"

if not YTDLP_PATH.exists():
    logging.critical(f"yt-dlp not found in {SCRIPT_DIR}")
    sys.exit(1)

if not FFMPEG_PATH.exists():
    logging.critical(f"ffmpeg not found in {SCRIPT_DIR}")
    sys.exit(1)

COOKIES_FILE_PATH = SCRIPT_DIR / "cookies.txt"

# ==============================================================================
# 2. Native Messaging Protocol Handlers (Strictly untouched for stdout)
# ==============================================================================
def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        raise EOFError("End of input stream")
    
    message_length = struct.unpack('=I', raw_length)[0]
    message_bytes = sys.stdin.buffer.read(message_length)
    return json.loads(message_bytes.decode('utf-8'))

def send_message(message_dict):
    encoded_content = json.dumps(message_dict).encode('utf-8')
    encoded_length = struct.pack('=I', len(encoded_content))
    
    sys.stdout.buffer.write(encoded_length)
    sys.stdout.buffer.write(encoded_content)
    sys.stdout.buffer.flush()

# ==============================================================================
# 3. Core Execution Logic (With Fallback Strategy)
# ==============================================================================
def process_download_request(request):
    url = request.get('url')
    cookies_data = request.get('cookies')
    
    logging.info(f"Received request for URL: {url}")
    
    if not url:
        logging.error("No URL provided in request.")
        return {"status": "error", "message": "No URL provided in request"}

    # Base command arguments (Robust format selector and web clients)
    base_args = [
        str(YTDLP_PATH),
        '--ffmpeg-location', str(FFMPEG_PATH),
        '-f', 'bv*+ba/b', 
        '--merge-output-format', 'mp4',
        # FIX: Use 'home' for final downloads, and 'temp' for intermediate files
        '--paths', f'home:{os.path.join(os.path.expanduser("~"), "Downloads")}',
        '--paths', f'temp:{tempfile.gettempdir()}',
        '-o', '%(title)s.%(ext)s', # Just the filename, the 'home' path handles the folder
        '--extractor-args', 'youtube:player_client=web,mweb',
        str(url)
    ]

    attempt = 1
    # If we have cookies, we allow up to 2 attempts. Otherwise, just 1.
    max_attempts = 2 if cookies_data else 1
    result = None

    while attempt <= max_attempts:
        command = base_args.copy()
        
        # CASE 2: If this is the second attempt and we have cookies, use them
        if attempt == 2 and cookies_data:
            logging.info(f"[ATTEMPT 2] Initial download failed. Retrying WITH cookies.")
            try:
                with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
                    f.write(cookies_data)
                command.extend(['--cookies', str(COOKIES_FILE_PATH)])
            except Exception as e:
                logging.error(f"Failed to write cookies.txt: {e}")
                break
        else:
            logging.info(f"[ATTEMPT {attempt}] Executing yt-dlp (WITHOUT cookies).")

        logging.info(f"Executing command: {' '.join(command)}")
        
        # Execute yt-dlp
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        logging.info(f"--- yt-dlp Attempt {attempt} Output Start ---")
        for line in result.stdout.splitlines():
            logging.info(f"yt-dlp: {line}")
        logging.info(f"--- yt-dlp Attempt {attempt} Output End ---")

        if result.returncode == 0:
            logging.info(f"Download completed successfully on attempt {attempt}.")
            break # Success, exit the loop
        else:
            logging.warning(f"Attempt {attempt} failed with return code {result.returncode}.")
            if attempt < max_attempts:
                logging.info("Preparing for fallback attempt...")
        
        attempt += 1

    # Final cleanup: Always delete the cookies file for security
    if os.path.exists(COOKIES_FILE_PATH):
        try:
            os.remove(COOKIES_FILE_PATH)
            logging.info("Cleaned up cookies.txt successfully.")
        except Exception as e:
            logging.error(f"Failed to delete cookies.txt: {e}")

    # Return final status to Chrome
    if result and result.returncode == 0:
        return {"status": "success", "message": f"Download completed on attempt {attempt}"}
    else:
        return {"status": "error", "message": "All download attempts failed. Check downloader.log."}
# ==============================================================================
# 4. Main Event Loop
# ==============================================================================
def main():
    while True:
        try:
            request = read_message()
            response = process_download_request(request)
            send_message(response)
            
        except EOFError:
            logging.info("Chrome closed the connection. Exiting gracefully.")
            sys.exit(0)
        except json.JSONDecodeError:
            logging.error("Received invalid JSON from Chrome.")
            send_message({"status": "error", "message": "Invalid JSON received"})
        except Exception as e:
            logging.exception("Host crashed unexpectedly.")
            send_message({"status": "error", "message": f"Host crashed: {str(e)}"})

if __name__ == '__main__':
    main()