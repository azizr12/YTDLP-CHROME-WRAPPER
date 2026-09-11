import sys
import json
import struct
import subprocess
import tempfile
import os
import pathlib
import logging
import configparser
import shlex

# ==============================================================================
# 1. Path Resolution & Logging Setup
# ==============================================================================
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
LOG_FILE = SCRIPT_DIR / "downloader.log"
CONFIG_FILE = SCRIPT_DIR / "config.ini"

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
# 2. Configuration Management (INI File)
# ==============================================================================
def load_config():
    """Loads or creates the config.ini file for persistent settings."""
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        logging.info(f"Creating default configuration file: {CONFIG_FILE}")
        config['Settings'] = {
            'debug': 'False',
            'default_options': '--no-playlist'
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
    else:
        config.read(CONFIG_FILE, encoding='utf-8')
    return config

# ==============================================================================
# 3. Native Messaging Protocol Handlers
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
# 4. Core Execution Logic (Live Streaming + Custom Options + 4-Layer Fallback)
# ==============================================================================
def process_download_request(request, config):
    url = request.get('url')
    cookies_data = request.get('cookies')
    
    # 1. Parse Custom Options from Extension and INI
    ext_options_raw = request.get('options', [])
    ext_options = shlex.split(ext_options_raw) if isinstance(ext_options_raw, str) else ext_options_raw
    
    ini_options_str = config.get('Settings', 'default_options', fallback='')
    ini_options = shlex.split(ini_options_str) if ini_options_str else []
    
    custom_options = ini_options + ext_options
    
    debug_mode = config.getboolean('Settings', 'debug', fallback=False)
    
    logging.info(f"Received request for URL: {url}")
    if debug_mode:
        print(f"[DEBUG] URL: {url}", file=sys.stderr)
        print(f"[DEBUG] Custom Options: {custom_options}", file=sys.stderr)

    if not url:
        logging.error("No URL provided in request.")
        return {"status": "error", "message": "No URL provided in request"}

    base_args = [str(YTDLP_PATH)]

    cookies_file_ready = False
    if cookies_data:
        try:
            with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(cookies_data)
            cookies_file_ready = True
            logging.info(f"Cookies successfully written to {COOKIES_FILE_PATH}")
        except Exception as e:
            logging.error(f"Failed to write cookies.txt: {e}")

    # 4-Layer Fallback Strategy
    variations = [
        {"desc": "Standard (No cookies)", "args": [], "needs_cookies": False},
        {"desc": "Standard (With cookies)", "args": ['--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True},
        {"desc": "ID-only filename (No cookies)", "args": ['-o', '%(id)s.%(ext)s'], "needs_cookies": False},
        {"desc": "ID-only filename (With cookies)", "args": ['-o', '%(id)s.%(ext)s', '--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True}
    ]

    attempt_num = 0
    last_error_msg = "Unknown error"
    success = False

    for variation in variations:
        if variation["needs_cookies"] and not cookies_file_ready:
            continue
            
        attempt_num += 1
        
        # Construct final command: yt-dlp + [Custom Options] + [Variation Args] + [URL]
        command = base_args.copy()
        command.extend(custom_options)
        command.extend(variation["args"])
        command.append(str(url))
        
        logging.info(f"[ATTEMPT {attempt_num}] Strategy: {variation['desc']}")
        logging.info(f"Executing command: {' '.join(command)}")
        
        if debug_mode:
            print(f"\n[DEBUG] === ATTEMPT {attempt_num} ===", file=sys.stderr)
            print(f"[DEBUG] Command: {' '.join(command)}", file=sys.stderr)

        # LIVE STREAMING EXECUTION
        # We use Popen instead of run to read output line-by-line as it happens
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        logging.info(f"--- yt-dlp Attempt {attempt_num} Output Start ---")
        output_lines = []
        
        for line in process.stdout:
            clean_line = line.strip()
            if clean_line:
                output_lines.append(clean_line)
                logging.info(f"yt-dlp: {clean_line}")
                
                # Print live to the terminal safely via sys.stderr if debug is ON
                if debug_mode:
                    print(f"[DEBUG] {clean_line}", file=sys.stderr)
                    
        logging.info(f"--- yt-dlp Attempt {attempt_num} Output End ---")
        
        process.wait()
        returncode = process.returncode

        if returncode == 0:
            logging.info(f"Download completed successfully on attempt {attempt_num}.")
            success = True
            break
        else:
            logging.warning(f"Attempt {attempt_num} failed with return code {returncode}.")
            if output_lines:
                last_error_msg = output_lines[-1]

    # Cleanup cookies
    if os.path.exists(COOKIES_FILE_PATH):
        try:
            os.remove(COOKIES_FILE_PATH)
            logging.info("Cleaned up cookies.txt successfully.")
        except Exception as e:
            logging.error(f"Failed to delete cookies.txt: {e}")

    if success:
        return {"status": "success", "message": f"Download completed on attempt {attempt_num}"}
    else:
        return {"status": "error", "message": f"All attempts failed. Last error: {last_error_msg}"}

# ==============================================================================
# 5. Main Event Loop
# ==============================================================================
def main():
    config = load_config()
    debug_mode = config.getboolean('Settings', 'debug', fallback=False)
    logging.info(f"Configuration loaded. Debug mode: {debug_mode}")
    
    if debug_mode:
        print("[DEBUG] Host Started. Debug mode is ON.", file=sys.stderr)

    while True:
        try:
            request = read_message()
            response = process_download_request(request, config)
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