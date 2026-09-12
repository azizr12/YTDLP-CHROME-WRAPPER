#!/usr/bin/env python3
import sys
import json
import struct
import subprocess
import os
import pathlib
import logging
import configparser
import shlex
import threading
import time
import queue
import datetime
import urllib.request
import zipfile
import shutil
import ctypes
import ctypes.wintypes
import winreg
import tempfile

# Tray dependencies
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog
from PIL import Image, ImageDraw
import pystray

# ==============================================================================
# 1. Path Resolution (PyInstaller Compatible)
# ==============================================================================
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = pathlib.Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()

# Suppress console windows for all child processes (yt-dlp, ffmpeg, deno).
if sys.platform == 'win32':
    SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    SUBPROCESS_FLAGS = 0

LOG_FILE = SCRIPT_DIR / "downloader.log"
CONFIG_FILE = SCRIPT_DIR / "config.ini"

logging.basicConfig(
    filename=LOG_FILE,
    filemode='w',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

YTDLP_PATH = SCRIPT_DIR / "yt-dlp.exe"
FFMPEG_PATH = SCRIPT_DIR / "ffmpeg.exe"
DENO_PATH = SCRIPT_DIR / "deno.exe"
COOKIES_FILE_PATH = SCRIPT_DIR / "cookies.txt"

# Ctrl handler event constants
CTRL_C_EVENT = 0
CTRL_CLOSE_EVENT = 2
SW_HIDE = 0

# Global reference to prevent garbage collection of the ctrl handler callback
_ctrl_handler_ref = None


def console_ctrl_handler(ctrl_type):
    if ctrl_type in (CTRL_C_EVENT, CTRL_CLOSE_EVENT):
        terminal_log("[i] Console ctrl-event intercepted (app runs via tray/log-window regardless).")
        return True
    return False


def get_console_window():
    return ctypes.windll.kernel32.GetConsoleWindow()


def hide_console():
    hwnd = get_console_window()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)


def setup_console_tray_behavior():
    global _ctrl_handler_ref
    HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
    _ctrl_handler_ref = HANDLER_ROUTINE(console_ctrl_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)
    hide_console()


# ==============================================================================
# Tk-based "console" log viewer
# ==============================================================================
tk_root = None
_log_queue = queue.Queue()
_MAX_LOG_LINES = 2000


def _on_log_window_unmap(event):
    global tk_root
    if tk_root is None or event.widget is not tk_root:
        return
    try:
        if tk_root.state() == 'iconic':
            tk_root.withdraw()
    except Exception:
        pass


def hide_log_window():
    if tk_root is not None:
        try:
            tk_root.withdraw()
        except Exception:
            pass


def show_log_window():
    if tk_root is not None:
        try:
            tk_root.deiconify()
            tk_root.lift()
            tk_root.focus_force()
        except Exception:
            pass


def run_on_ui_thread(func):
    def wrapper(*args, **kwargs):
        if tk_root is not None:
            tk_root.after(0, lambda: func(*args, **kwargs))
    return wrapper


def _drain_log_queue():
    if tk_root is None:
        return
    try:
        text_widget = tk_root._log_text_widget
        appended = False
        while True:
            try:
                msg = _log_queue.get_nowait()
            except queue.Empty:
                break
            text_widget.configure(state='normal')
            text_widget.insert('end', msg + "\n")
            appended = True
        if appended:
            line_count = int(text_widget.index('end-1c').split('.')[0])
            if line_count > _MAX_LOG_LINES:
                text_widget.delete('1.0', f"{line_count - _MAX_LOG_LINES}.0")
            text_widget.see('end')
            text_widget.configure(state='disabled')
    except Exception:
        pass
    finally:
        if tk_root is not None:
            tk_root.after(200, _drain_log_queue)


def create_log_window():
    global tk_root
    tk_root = tk.Tk()
    tk_root.overrideredirect(True)
    tk_root.geometry("900x500+200+150")
    tk_root.configure(bg="#3c3c3c")

    border = tk.Frame(tk_root, bg="#3c3c3c")
    border.pack(fill='both', expand=True)

    content = tk.Frame(border, bg="#1e1e1e")
    content.pack(fill='both', expand=True, padx=1, pady=1)

    titlebar = tk.Frame(content, bg="#2d2d30", height=32)
    titlebar.pack(fill='x', side='top')
    titlebar.pack_propagate(False)

    title_label = tk.Label(
        titlebar, text="YT-DLP Manager — Console",
        bg="#2d2d30", fg="#d4d4d4", font=("Segoe UI", 10)
    )
    title_label.pack(side='left', padx=10)

    close_btn = tk.Label(
        titlebar, text="✕", bg="#2d2d30", fg="#d4d4d4",
        font=("Segoe UI", 11), cursor="hand2", padx=10
    )
    close_btn.pack(side='right', fill='y')
    close_btn.bind("<Button-1>", lambda e: hide_log_window())
    close_btn.bind("<Enter>", lambda e: close_btn.configure(bg="#e81123", fg="white"))
    close_btn.bind("<Leave>", lambda e: close_btn.configure(bg="#2d2d30", fg="#d4d4d4"))

    hide_btn = tk.Label(
        titlebar, text="—", bg="#2d2d30", fg="#d4d4d4",
        font=("Segoe UI", 11), cursor="hand2", padx=10
    )
    hide_btn.pack(side='right', fill='y')
    hide_btn.bind("<Button-1>", lambda e: hide_log_window())
    hide_btn.bind("<Enter>", lambda e: hide_btn.configure(bg="#3f3f46"))
    hide_btn.bind("<Leave>", lambda e: hide_btn.configure(bg="#2d2d30"))

    def _start_drag(event):
        tk_root._drag_offset_x = event.x_root - tk_root.winfo_x()
        tk_root._drag_offset_y = event.y_root - tk_root.winfo_y()

    def _do_drag(event):
        x = event.x_root - tk_root._drag_offset_x
        y = event.y_root - tk_root._drag_offset_y
        tk_root.geometry(f"+{x}+{y}")

    for widget in (titlebar, title_label):
        widget.bind("<ButtonPress-1>", _start_drag)
        widget.bind("<B1-Motion>", _do_drag)

    grip = tk.Label(content, text="◢", bg="#1e1e1e", fg="#5a5a5a", cursor="size_nw_se")
    grip.place(relx=1.0, rely=1.0, anchor='se')

    def _start_resize(event):
        tk_root._resize_start_w = tk_root.winfo_width()
        tk_root._resize_start_h = tk_root.winfo_height()
        tk_root._resize_start_x = event.x_root
        tk_root._resize_start_y = event.y_root

    def _do_resize(event):
        new_w = max(400, tk_root._resize_start_w + (event.x_root - tk_root._resize_start_x))
        new_h = max(250, tk_root._resize_start_h + (event.y_root - tk_root._resize_start_y))
        tk_root.geometry(f"{new_w}x{new_h}")

    grip.bind("<ButtonPress-1>", _start_resize)
    grip.bind("<B1-Motion>", _do_resize)

    text_widget = scrolledtext.ScrolledText(
        content, state='disabled', wrap='word',
        bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
        font=("Consolas", 10), borderwidth=0, highlightthickness=0
    )
    text_widget.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    tk_root._log_text_widget = text_widget

    tk_root.protocol("WM_DELETE_WINDOW", hide_log_window)
    tk_root.bind("<Unmap>", _on_log_window_unmap)

    tk_root.withdraw()
    tk_root.after(200, _drain_log_queue)


# Global tray icon reference
tray_icon = None

# ==============================================================================
# 2. Terminal & Tray Logging Setup
# ==============================================================================
def terminal_log(msg):
    print(msg, file=sys.stderr, flush=True)
    logging.info(msg)
    try:
        _log_queue.put_nowait(msg)
    except Exception:
        pass

def update_status(msg):
    print(f"Status: {msg}", file=sys.stderr, flush=True)
    global tray_icon
    if tray_icon:
        tray_icon.title = f"YT-DLP Manager: {msg}"

def format_size(bytes_size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"

# ==============================================================================
# 3. Tray Menu Actions
# ==============================================================================
def get_default_download_dir():
    return str(pathlib.Path.home() / "Downloads" / "YTDLP")

def open_download_folder():
    config = load_config()
    folder = config.get('Settings', 'download_dir', fallback=get_default_download_dir())
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    
    terminal_log(f"Opening download folder: {folder}")
    if sys.platform == 'win32':
        os.startfile(folder)
    elif sys.platform == 'darwin':
        subprocess.run(['open', folder], creationflags=SUBPROCESS_FLAGS)
    else:
        subprocess.run(['xdg-open', folder], creationflags=SUBPROCESS_FLAGS)

def change_download_folder():
    config = load_config()
    current_dir = config.get('Settings', 'download_dir', fallback=get_default_download_dir())

    tk_root.attributes('-topmost', True)
    folder = filedialog.askdirectory(
        parent=tk_root,
        title="Select Default Download Folder",
        initialdir=current_dir
    )
    tk_root.attributes('-topmost', False)

    if folder:
        config.set('Settings', 'download_dir', folder)
        save_config(config)
        terminal_log(f"[+] Download folder changed to: {folder}")

def show_about():
    tk_root.attributes('-topmost', True)
    messagebox.showinfo(
        "About YT-DLP Manager",
        "YT-DLP Native Messaging Host & Tray Manager\n"
        "Version: 1.6.0\n\n"
        "Features:\n"
        "- 'Console' is now an in-app log window (reliable minimize/close-to-tray)\n"
        "- Metadata-driven FFmpeg update detection\n"
        "- Auto-updates yt-dlp, ffmpeg, and deno\n"
        "- 4-layer fallback download strategy, temp files go to %TEMP%\n\n"
        f"Config Path:\n{CONFIG_FILE}",
        parent=tk_root
    )
    tk_root.attributes('-topmost', False)

def exit_app():
    terminal_log("Exiting application from tray menu...")
    global tray_icon
    if tray_icon:
        tray_icon.stop()
    os._exit(0)

def create_tray_icon_image():
    image = Image.new('RGB', (64, 64), color=(32, 33, 36))
    draw = ImageDraw.Draw(image)
    draw.ellipse([8, 8, 56, 56], fill=(138, 180, 248))
    draw.polygon([(28, 20), (48, 32), (28, 44)], fill=(32, 33, 36))
    return image

# ==============================================================================
# 4. Integrated Updater Logic
# ==============================================================================
def load_config():
    config = configparser.ConfigParser()
    default_dir = get_default_download_dir()
    
    if not CONFIG_FILE.exists():
        config['Settings'] = {
            'debug': 'True', 
            'default_options': '--no-playlist',
            'download_dir': default_dir
        }
        config['Versions'] = {
            'yt_dlp': '',
            'deno': '',
            'ffmpeg': ''
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: 
            config.write(f)
    else:
        config.read(CONFIG_FILE, encoding='utf-8')
        
        if 'Settings' not in config:
            config['Settings'] = {}
        if 'download_dir' not in config['Settings']:
            config['Settings']['download_dir'] = default_dir
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)
                
        if 'Versions' not in config:
            config['Versions'] = {
                'yt_dlp': '',
                'deno': '',
                'ffmpeg': ''
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)
                
    return config

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
    except Exception as e:
        terminal_log(f"[Config] Failed to save config: {e}")

def update_ytdlp():
    terminal_log("[Updater] Checking yt-dlp...")
    config = load_config()
    local_version = config.get('Versions', 'yt_dlp', fallback='').strip()
    
    if not local_version and YTDLP_PATH.exists():
        try:
            env = os.environ.copy()
            env["PATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                [str(YTDLP_PATH), "--version"], 
                capture_output=True, 
                text=True, 
                timeout=5, 
                env=env,
                creationflags=SUBPROCESS_FLAGS
            )
            if result.returncode == 0:
                local_version = result.stdout.strip()
                config.set('Versions', 'yt_dlp', local_version)
                save_config(config)
                terminal_log(f"[Updater] Local yt-dlp binary version: {local_version}")
        except Exception as e:
            terminal_log(f"[Updater] Failed to query yt-dlp binary: {e}")

    try:
        req = urllib.request.Request("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_version = data.get("tag_name")
            terminal_log(f"[Updater] Latest yt-dlp API version: {latest_version}")
    except Exception as e:
        terminal_log(f"[Updater] yt-dlp API error: {e}")
        return

    if latest_version and latest_version == local_version:
        terminal_log(f"[+] yt-dlp is already up to date ({latest_version}). Skipping download.")
        return

    terminal_log(f"[Updater] Version mismatch or missing. Downloading yt-dlp {latest_version}...")
    exe_asset = next((a for a in data.get("assets", []) if a["name"] == "yt-dlp.exe"), None)
    if not exe_asset:
        terminal_log("[X] yt-dlp.exe not found in release assets.")
        return

    temp_exe = SCRIPT_DIR / "yt-dlp.temp.exe"
    try:
        urllib.request.urlretrieve(exe_asset["browser_download_url"], temp_exe)
        if YTDLP_PATH.exists(): YTDLP_PATH.unlink()
        temp_exe.rename(YTDLP_PATH)
        config.set('Versions', 'yt_dlp', latest_version)
        save_config(config)
        terminal_log(f"[+] yt-dlp updated successfully to {latest_version}.")
    except Exception as e:
        terminal_log(f"[X] yt-dlp download failed: {e}")
        if temp_exe.exists(): temp_exe.unlink()

def update_deno():
    terminal_log("[Updater] Checking deno...")
    config = load_config()
    local_version = config.get('Versions', 'deno', fallback='').strip()
    
    if not local_version and DENO_PATH.exists():
        try:
            result = subprocess.run(
                [str(DENO_PATH), "--version"], 
                capture_output=True, 
                text=True, 
                timeout=5,
                creationflags=SUBPROCESS_FLAGS
            )
            if result.returncode == 0:
                first_line = result.stdout.splitlines()[0]
                local_version = first_line.split()[1] 
                config.set('Versions', 'deno', local_version)
                save_config(config)
                terminal_log(f"[Updater] Local deno binary version: {local_version}")
        except Exception as e:
            terminal_log(f"[Updater] Failed to query deno binary: {e}")

    try:
        req = urllib.request.Request("https://dl.deno.land/release-latest.txt", headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=15) as response:
            latest_version_raw = response.read().decode('utf-8').strip()
            latest_version = latest_version_raw.lstrip("v")
            terminal_log(f"[Updater] Latest deno API version: {latest_version}")
    except Exception as e:
        terminal_log(f"[Updater] deno API error: {e}")
        return

    if latest_version and latest_version == local_version:
        terminal_log(f"[+] deno is already up to date ({latest_version}). Skipping download.")
        return

    terminal_log(f"[Updater] Version mismatch or missing. Downloading deno {latest_version}...")
    url = f"https://github.com/denoland/deno/releases/download/v{latest_version}/deno-x86_64-pc-windows-msvc.zip"
    temp_zip = SCRIPT_DIR / "deno.temp.zip"
    temp_exe = SCRIPT_DIR / "deno.temp.exe"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=60) as response, open(temp_zip, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            with zip_ref.open('deno.exe') as src, open(temp_exe, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        temp_zip.unlink()
        if DENO_PATH.exists(): DENO_PATH.unlink()
        temp_exe.rename(DENO_PATH)
        config.set('Versions', 'deno', latest_version)
        save_config(config)
        terminal_log(f"[+] deno updated successfully to {latest_version}.")
    except Exception as e:
        terminal_log(f"[X] deno download failed: {e}")
        if temp_zip.exists(): temp_zip.unlink()
        if temp_exe.exists(): temp_exe.unlink()

def update_ffmpeg():
    terminal_log("[Updater] Checking ffmpeg...")
    config = load_config()
    local_asset_name = config.get('Versions', 'ffmpeg', fallback='').strip()
    
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest", 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            release = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        terminal_log(f"[Updater] ffmpeg API error: {e}")
        return False
    
    asset = None
    for a in release.get("assets", []):
        if a["name"].startswith("ffmpeg-master-latest-win64-gpl") and a["name"].endswith(".zip"):
            asset = a
            break
    
    if not asset:
        terminal_log("[Updater] No matching ffmpeg asset found in release.")
        return False
        
    latest_asset_name = asset["name"]
    
    terminal_log(f"[Updater] Latest: {latest_asset_name}")
    terminal_log(f"[Updater] Local:  {local_asset_name}")
    
    needs_update = False
    if not local_asset_name:
        needs_update = True
    elif local_asset_name != latest_asset_name:
        needs_update = True
    elif not FFMPEG_PATH.exists():
        needs_update = True
        
    if not needs_update:
        terminal_log("[+] ffmpeg is already up to date.")
        return True
        
    if size := asset.get("size"):
        terminal_log(f"[Updater] Download size: {format_size(size)}")
    terminal_log(f"[Updater] Downloading ffmpeg ({latest_asset_name})...")
    
    temp_zip = SCRIPT_DIR / "ffmpeg.temp.zip"
    try:
        req = urllib.request.Request(asset["browser_download_url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=120) as response, open(temp_zip, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        terminal_log("[+] Download complete.")
    except Exception as e:
        terminal_log(f"[X] ffmpeg download failed: {e}")
        if temp_zip.exists(): temp_zip.unlink()
        return False
        
    terminal_log("[Updater] Extracting ffmpeg...")
    try:
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall(SCRIPT_DIR)
        temp_zip.unlink()
        
        extracted_root = None
        for item in SCRIPT_DIR.iterdir():
            if item.is_dir() and item.name.startswith("ffmpeg-") and (item / "bin").exists():
                extracted_root = item
                break
                
        if extracted_root:
            bin_dir = extracted_root / "bin"
            for file_path in bin_dir.iterdir():
                if file_path.is_file():
                    dst = SCRIPT_DIR / file_path.name
                    if dst.exists():
                        dst.unlink()
                    shutil.move(str(file_path), str(dst))
            
            shutil.rmtree(extracted_root, ignore_errors=True)
            terminal_log("[+] ffmpeg and its dependencies extracted successfully.")
        else:
            terminal_log("[!] Warning: Could not locate extracted bin/ directory")
            return False
            
    except Exception as e:
        terminal_log(f"[X] Extraction error: {e}")
        if temp_zip.exists(): temp_zip.unlink()
        return False
        
    if not FFMPEG_PATH.exists():
        terminal_log("[X] ffmpeg.exe not found after extraction")
        return False
        
    config.set('Versions', 'ffmpeg', latest_asset_name)
    save_config(config)
    
    try:
        env = os.environ.copy()
        env["PATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            [str(FFMPEG_PATH), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            creationflags=SUBPROCESS_FLAGS
        )
        if result.returncode == 0:
            version = result.stdout.splitlines()[0].split(" Copyright")[0][:60]
            terminal_log(f"[+] Updated: {version}")
    except Exception:
        pass
        
    return True

def updater_loop():
    update_file = SCRIPT_DIR / "last_update.txt"
    today = datetime.date.today()
    
    update_ffmpeg()

    if update_file.exists():
        try:
            last_date = datetime.date.fromisoformat(update_file.read_text().strip())
            if (today - last_date).days < 1:
                terminal_log("[Updater] Daily checks for yt-dlp/deno already done today.")
                return
        except Exception:
            pass

    terminal_log("[Updater] Running daily checks for yt-dlp and deno...")
    update_ytdlp()
    update_deno()
    update_file.write_text(today.isoformat())
    terminal_log("[Updater] Daily checks complete.")
    update_status("Listening for browser requests...")

# ==============================================================================
# 5. Native Messaging Protocol Handlers
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
# 6. Core Execution Logic (4-Layer Fallback + Download Path)
# ==============================================================================
def process_download_request(request, config):
    url = request.get('url')
    cookies_data = request.get('cookies')
    
    ext_options_raw = request.get('options', [])
    ext_options = shlex.split(ext_options_raw) if isinstance(ext_options_raw, str) else ext_options_raw
    
    ini_options_str = config.get('Settings', 'default_options', fallback='')
    ini_options = shlex.split(ini_options_str) if ini_options_str else []
    
    custom_options = ini_options + ext_options
    debug_mode = config.getboolean('Settings', 'debug', fallback=False)
    
    # Export Directory (Finished Downloads)
    export_dir = config.get('Settings', 'download_dir', fallback=get_default_download_dir())
    export_path = pathlib.Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    
    # Temp Directory (System %TEMP%)
    system_temp = os.environ.get('TEMP') or os.environ.get('TMP') or tempfile.gettempdir()
    temp_dir = pathlib.Path(system_temp) / "ytdlp_temp_downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"Received request for URL: {url}")
    logging.info(f"Temp Directory: {temp_dir}")
    logging.info(f"Export Directory: {export_path}")
    
    if debug_mode:
        print(f"[DEBUG] URL: {url}", file=sys.stderr)
        print(f"[DEBUG] Custom Options: {custom_options}", file=sys.stderr)

    if not url:
        logging.error("No URL provided in request.")
        return {"status": "error", "message": "No URL provided in request"}
    base_args = [
        str(YTDLP_PATH),
        '-P', f'temp:{temp_dir}',
        '-P', f'home:{export_path}',
        '--newline'
    ]

    cookies_file_ready = False
    if cookies_data:
        try:
            with open(COOKIES_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write(cookies_data)
            cookies_file_ready = True
            logging.info(f"Cookies successfully written to {COOKIES_FILE_PATH}")
        except Exception as e:
            logging.error(f"Failed to write cookies.txt: {e}")

    variations = [
        {"desc": "Standard (No cookies)", "args": [], "needs_cookies": False},
        {"desc": "Standard (With cookies)", "args": ['--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True},
        {"desc": "ID-only filename (No cookies)", "args": ['-o', '%(id)s.%(ext)s'], "needs_cookies": False},
        {"desc": "ID-only filename (With cookies)", "args": ['-o', '%(id)s.%(ext)s', '--cookies', str(COOKIES_FILE_PATH)], "needs_cookies": True}
    ]

    attempt_num = 0
    last_error_msg = "Unknown error"
    success = False

    env = os.environ.copy()
    env["PATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PATH", "")

    for variation in variations:
        if variation["needs_cookies"] and not cookies_file_ready:
            continue
            
        attempt_num += 1
        
        command = base_args.copy()
        command.extend(custom_options)
        command.extend(variation["args"])
        command.append(str(url))
        
        logging.info(f"[ATTEMPT {attempt_num}] Strategy: {variation['desc']}")
        logging.info(f"Executing command: {' '.join(command)}")
        
        if debug_mode:
            print(f"\n[DEBUG] === ATTEMPT {attempt_num} ===", file=sys.stderr)
            print(f"[DEBUG] Command: {' '.join(command)}", file=sys.stderr)

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=SUBPROCESS_FLAGS
        )

        logging.info(f"--- yt-dlp Attempt {attempt_num} Output Start ---")
        output_lines = []
        
        for line in process.stdout:
            clean_line = line.strip()
            if clean_line:
                output_lines.append(clean_line)
                logging.info(f"yt-dlp: {clean_line}")
                
                if debug_mode:
                    print(f"[DEBUG] {clean_line}", file=sys.stderr)
                    
        logging.info(f"--- yt-dlp Attempt {attempt_num} Output End ---")
        
        process.wait()
        returncode = process.returncode

        if returncode == 0:
            logging.info(f"Download completed successfully on attempt {attempt_num}. File moved to export path.")
            success = True
            break
        else:
            logging.warning(f"Attempt {attempt_num} failed with return code {returncode}.")
            if output_lines:
                last_error_msg = output_lines[-1]

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
# 7. Main Event Loop & Dual-Mode Initialization
# ==============================================================================
def native_messaging_loop():
    config = load_config()
    while True:
        try:
            request = read_message()
            response = process_download_request(request, config)
            send_message(response)
        except EOFError:
            terminal_log("Browser closed the connection. Tray app and terminal remain active.")
            break
        except Exception as e:
            terminal_log(f"Host error: {str(e)}")

def setup_bridge():
    """Sets up Chrome Native Messaging Host (BRIDGE) in the current directory."""
    terminal_log("[BRIDGE] Setup initiated. Waiting for Extension ID input...")
    
    # Ensure the root window is visible so the dialog attaches properly and pops up as GUI
    was_withdrawn = False
    if tk_root is not None and tk_root.state() == 'withdrawn':
        tk_root.deiconify()
        was_withdrawn = True

    ext_id = simpledialog.askstring(
        "Chrome Extension ID", 
        "Paste your Chrome Extension ID here:\n(Leave blank to skip allowed_origins, but Chrome may block the host)",
        parent=tk_root
    )
    
    if was_withdrawn and tk_root is not None:
        tk_root.withdraw()
        
    if ext_id is None:
        terminal_log("[BRIDGE] Setup cancelled by user.")
        return
        
    ext_id = ext_id.strip()
    allowed_origins = [f"chrome-extension://{ext_id}/"] if ext_id else []
        
    HOST_NAME = "com.wrapper.ytdlp_host"
    MANIFEST_PATH = SCRIPT_DIR / f"{HOST_NAME}.json"
    REGISTRY_PATH = r"Software\Google\Chrome\NativeMessagingHosts"
    
    if getattr(sys, 'frozen', False):
        EXEC_PATH = str(sys.executable)
        terminal_log("[BRIDGE] Detected frozen executable. Pointing manifest directly to .exe")
    else:
        BAT_PATH = SCRIPT_DIR / "run_host.bat"
        bat_content = f'@echo off\n"{sys.executable}" "{pathlib.Path(__file__).resolve()}" %*\n'
        try:
            with open(BAT_PATH, 'w', encoding='utf-8') as f:
                f.write(bat_content)
            terminal_log(f"[BRIDGE] Created batch wrapper at: {BAT_PATH}")
        except Exception as e:
            terminal_log(f"[BRIDGE ERROR] Failed to write batch file: {e}")
            messagebox.showerror("Bridge Setup Failed", f"Failed to write batch wrapper:\n{e}", parent=tk_root)
            return
        EXEC_PATH = str(BAT_PATH)
        
    manifest_data = {
        "name": HOST_NAME,
        "description": "YT-DLP Native Messaging Host",
        "path": EXEC_PATH,
        "type": "stdio",
        "allowed_origins": allowed_origins
    }
    
    try:
        with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest_data, f, indent=2)
        terminal_log(f"[BRIDGE] Saved manifest to: {MANIFEST_PATH}")
    except Exception as e:
        terminal_log(f"[BRIDGE ERROR] Failed to write manifest: {e}")
        messagebox.showerror("Bridge Setup Failed", f"Failed to write manifest:\n{e}", parent=tk_root)
        return
        
    try:
        access_rights = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, access_rights)
        subkey = winreg.CreateKeyEx(key, HOST_NAME, 0, access_rights)
        
        winreg.SetValueEx(subkey, "", 0, winreg.REG_SZ, str(MANIFEST_PATH))
        
        winreg.CloseKey(subkey)
        winreg.CloseKey(key)
        terminal_log(f"[BRIDGE] Registry key created: HKCU\\{REGISTRY_PATH}\\{HOST_NAME}")
        
        messagebox.showinfo(
            "Bridge Setup Successful", 
            f"Chrome Native Messaging Host configured successfully!\n\n"
            f"Manifest: {MANIFEST_PATH}\n"
            f"Host Path: {EXEC_PATH}\n"
            f"Registry: HKCU\\{REGISTRY_PATH}\\{HOST_NAME}\n\n"
            f"CRITICAL: You MUST completely close Chrome (check system tray) and restart it for this to work.",
            parent=tk_root
        )
    except Exception as e:
        terminal_log(f"[BRIDGE ERROR] Failed to write to registry: {e}")
        messagebox.showerror(
            "Bridge Setup Failed", 
            f"Failed to write to registry:\n{e}\n\nPlease ensure you have the necessary permissions.", 
            parent=tk_root
        )

def ensure_binaries_exist():
    """Checks if core binaries exist. If missing, forces an immediate re-download."""
    terminal_log("[Startup] Verifying core binaries...")
    missing = []
    
    if not YTDLP_PATH.exists():
        missing.append("yt-dlp")
    if not DENO_PATH.exists():
        missing.append("deno")
    if not FFMPEG_PATH.exists():
        missing.append("ffmpeg")
        
    if missing:
        terminal_log(f"[Startup] Missing binaries detected: {', '.join(missing)}. Forcing download...")
        config = load_config()
        
        if "yt-dlp" in missing:
            config.set('Versions', 'yt_dlp', '') # Clear version to force update
            save_config(config)
            update_ytdlp()
            
        if "deno" in missing:
            config.set('Versions', 'deno', '')
            save_config(config)
            update_deno()
            
        if "ffmpeg" in missing:
            config.set('Versions', 'ffmpeg', '')
            save_config(config)
            update_ffmpeg()
            
        terminal_log("[Startup] Binary restoration complete.")
    else:
        terminal_log("[Startup] All core binaries are present.")

def main():
    global tray_icon

    create_log_window()
    setup_console_tray_behavior()
    ensure_binaries_exist()
    terminal_log("="*60)
    terminal_log("YT-DLP Manager started (System Tray + in-app log window)")
    terminal_log("Use Tray -> 'Open Console' to view logs, or 'Exit' to quit.")
    terminal_log("="*60)

    threading.Thread(target=updater_loop, daemon=True).start()
    threading.Thread(target=native_messaging_loop, daemon=True).start()

    icon_image = create_tray_icon_image()

    menu = pystray.Menu(
        pystray.MenuItem('Open Console', run_on_ui_thread(show_log_window)),
        pystray.MenuItem('Close to Tray', run_on_ui_thread(hide_log_window)),
        pystray.Menu.SEPARATOR,
        
        pystray.MenuItem('BRIDGE (Setup Chrome Host)', run_on_ui_thread(setup_bridge)),
        pystray.Menu.SEPARATOR,
        
        pystray.MenuItem('Open Download Folder', open_download_folder),
        pystray.MenuItem('Change Download Folder', run_on_ui_thread(change_download_folder)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('About', run_on_ui_thread(show_about)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit', exit_app)
    )

    tray_icon = pystray.Icon(
        "yt_dlp_manager",
        icon_image,
        "YT-DLP Manager: Ready",
        menu
    )

    threading.Thread(target=tray_icon.run, daemon=True).start()

    terminal_log("System tray icon initialized. Waiting for requests...")
    tk_root.mainloop()

if __name__ == '__main__':
    main()