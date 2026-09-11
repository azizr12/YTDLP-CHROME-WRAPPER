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


#        '--paths', f'home:{os.path.join(os.path.expanduser("~"), "Downloads")}',
#        '--paths', f'temp:{tempfile.gettempdir()}',
# ==============================================================================
# 3. Core Execution Logic (With 4-Layer Fallback Strategy)
# ==============================================================================
def process_download_request(request):
    url = request.get('url')
    cookies_data = request.get('cookies')
    
    logging.info(f"Received request for URL: {url}")
    
    if not url:
        logging.error("No URL provided in request.")
        return {"status": "error", "message": "No URL provided in request"}

    # Base command arguments
    base_args = [
        str(YTDLP_PATH),
        str(url)
    ]

    # Prepare cookies file ONCE if data is provided (more efficient than rewriting it per attempt)
    cookies_file_ready = False
    if cookies_data:
        try:
            with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(cookies_data)
            cookies_file_ready = True
            logging.info(f"Cookies successfully written to {COOKIES_FILE_PATH}")
        except Exception as e:
            logging.error(f"Failed to write cookies.txt: {e}")

    # Define the 4 fallback layers
    # Note: We use "%(id)s.%(ext)s" to save the file by its ID in the script's directory.
    variations = [
        {"desc": "Standard (No cookies)", "args": [], "needs_cookies": False},
        {"desc": "Standard (With cookies)", "args": ['--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True},
        {"desc": "ID-only filename (No cookies)", "args": ['-o', '%(id)s.%(ext)s'], "needs_cookies": False},
        {"desc": "ID-only filename (With cookies)", "args": ['-o', '%(id)s.%(ext)s', '--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True}
    ]

    attempt_num = 0
    result = None
    last_error_msg = "Unknown error"

    for variation in variations:
        # Skip cookie layers if the extension didn't send us any cookies
        if variation["needs_cookies"] and not cookies_file_ready:
            continue
            
        attempt_num += 1
        command = base_args.copy()
        command.extend(variation["args"])
        
        logging.info(f"[ATTEMPT {attempt_num}] Strategy: {variation['desc']}")
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

        logging.info(f"--- yt-dlp Attempt {attempt_num} Output Start ---")
        for line in result.stdout.splitlines():
            logging.info(f"yt-dlp: {line}")
        logging.info(f"--- yt-dlp Attempt {attempt_num} Output End ---")

        if result.returncode == 0:
            logging.info(f"Download completed successfully on attempt {attempt_num}.")
            break # Success, exit the loop
        else:
            logging.warning(f"Attempt {attempt_num} failed with return code {result.returncode}.")
            # Capture the last line of yt-dlp output to send back to Chrome if all attempts fail
            output_lines = result.stdout.splitlines()
            if output_lines:
                last_error_msg = output_lines[-1]

    # Final cleanup: Always delete the cookies file for security
    if os.path.exists(COOKIES_FILE_PATH):
        try:
            os.remove(COOKIES_FILE_PATH)
            logging.info("Cleaned up cookies.txt successfully.")
        except Exception as e:
            logging.error(f"Failed to delete cookies.txt: {e}")

    # Return final status to Chrome
    if result and result.returncode == 0:
        return {"status": "success", "message": f"Download completed on attempt {attempt_num}"}
    else:
        return {"status": "error", "message": f"All attempts failed. Last error: {last_error_msg}"}
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