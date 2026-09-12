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


# Tray dependencies
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
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
# Without this, Windows spawns a new visible console for each subprocess
# even though this app itself has no console attached.
if sys.platform == 'win32':
    SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    SUBPROCESS_FLAGS = 0

LOG_FILE = SCRIPT_DIR / "downloader.log"
CONFIG_FILE = SCRIPT_DIR / "config.ini"
FFMPEG_VERSION_FILE = SCRIPT_DIR / "ffmpeg.version.txt"

logging.basicConfig(
    filename=LOG_FILE,
    filemode='w',  # 'w' ensures the file is overwritten on each run
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
    """
    Safety net only. Windows gives a process a few seconds to react to
    CTRL_CLOSE_EVENT/CTRL_LOGOFF_EVENT/CTRL_SHUTDOWN_EVENT, but ultimately the
    real console window is owned by conhost.exe (a different process), so
    nothing this handler does can reliably keep that window itself open or
    guarantee the process survives clicking its "X". That's why the previous
    approach of hiding/subclassing the real console never worked consistently.
    We no longer route the app's UI through the real console at all (see the
    Tk-based log window below), so this handler just avoids an unexpected
    crash/exit path and logs what happened.
    """
    if ctrl_type in (CTRL_C_EVENT, CTRL_CLOSE_EVENT):
        terminal_log("[i] Console ctrl-event intercepted (app runs via tray/log-window regardless).")
        return True
    return False


def get_console_window():
    return ctypes.windll.kernel32.GetConsoleWindow()


def hide_console():
    """Hides the REAL OS console window once at startup. We never show the
    real console again -- the 'Open Console' tray action opens our own
    Tk log-viewer window instead, since that window is one we actually own
    and can reliably show/hide/minimize."""
    hwnd = get_console_window()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)


def setup_console_tray_behavior():
    """Hides the real console permanently and registers a ctrl-handler as a
    safety net against unexpected termination (e.g. taskkill/logoff)."""
    global _ctrl_handler_ref
    HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
    _ctrl_handler_ref = HANDLER_ROUTINE(console_ctrl_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)
    hide_console()


# ==============================================================================
# Tk-based "console" log viewer (replaces trying to manage the real console)
# ==============================================================================
# The real Windows console window belongs to conhost.exe, a separate process.
# That's why intercepting its minimize/close ever worked inconsistently: you
# cannot subclass or fully control a window you don't own. This Tk window,
# on the other hand, belongs to THIS process, so minimize/close on it can be
# trapped reliably every time.

tk_root = None
_log_queue = queue.Queue()
_MAX_LOG_LINES = 2000


def _on_log_window_unmap(event):
    """Fires when the log window is minimized (or withdrawn). If the user
    minimized it via the titlebar button, immediately hide it fully (send to
    tray) instead of leaving a minimized window/taskbar entry behind."""
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
    """Wraps a callback so it executes on the Tk mainloop thread. Tray menu
    callbacks run on pystray's own thread, and Tkinter is not safe to touch
    from multiple threads, so anything that touches tk_root/dialogs must be
    scheduled back onto the UI thread via after()."""
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
            # Trim to avoid unbounded growth
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
    """Creates the persistent (initially hidden) Tk root that doubles as our
    'console' log viewer and as the parent for dialogs (file picker, about
    box). This is a borderless (overrideredirect) window -- there's no native
    titlebar/minimize button for Windows to fight over, which was the root
    cause of the original bugs. It has its own custom titlebar with hide/close
    buttons, and can be dragged by clicking and holding that titlebar."""
    global tk_root
    tk_root = tk.Tk()
    tk_root.overrideredirect(True)
    tk_root.geometry("900x500+200+150")
    tk_root.configure(bg="#3c3c3c")  # thin border color around the content

    # Thin border frame (gives the borderless window a visible outline)
    border = tk.Frame(tk_root, bg="#3c3c3c")
    border.pack(fill='both', expand=True)

    content = tk.Frame(border, bg="#1e1e1e")
    content.pack(fill='both', expand=True, padx=1, pady=1)

    # --- Custom titlebar (draggable, with hide/close buttons) ---
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

    # Dragging: only from the titlebar itself, so selecting/copying log text
    # in the body below isn't accidentally treated as a drag.
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

    # A small bottom-right grip lets the window be resized despite having no
    # native border to drag from.
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

    # --- Log text body ---
    text_widget = scrolledtext.ScrolledText(
        content, state='disabled', wrap='word',
        bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
        font=("Consolas", 10), borderwidth=0, highlightthickness=0
    )
    text_widget.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    tk_root._log_text_widget = text_widget

    # Alt+F4 (or any programmatic close attempt) still just hides to tray
    tk_root.protocol("WM_DELETE_WINDOW", hide_log_window)
    # Safety net: if the window is ever iconified by some external action
    # (e.g. Win+D), fully hide it instead of leaving a stray minimized window.
    tk_root.bind("<Unmap>", _on_log_window_unmap)

    tk_root.withdraw()  # start hidden
    tk_root.after(200, _drain_log_queue)


# Global tray icon reference
tray_icon = None

# ==============================================================================
# 2. Terminal & Tray Logging Setup
# ==============================================================================
def terminal_log(msg):
    """Prints to stderr to avoid corrupting stdout native messaging stream,
    and forwards the line to the Tk log-window queue."""
    print(msg, file=sys.stderr, flush=True)
    logging.info(msg)
    try:
        _log_queue.put_nowait(msg)
    except Exception:
        pass

def update_status(msg):
    """Updates tray tooltip and prints to stderr."""
    print(f"Status: {msg}", file=sys.stderr, flush=True)
    global tray_icon
    if tray_icon:
        tray_icon.title = f"YT-DLP Manager: {msg}"

def format_size(bytes_size: int) -> str:
    """Format bytes into human-readable string."""
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
    """Must run on the Tk UI thread (see run_on_ui_thread) -- uses tk_root as
    the dialog's parent instead of spinning up a separate Tk() instance,
    since mixing multiple Tk instances across threads is unsafe."""
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
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
        terminal_log(f"[+] Download folder changed to: {folder}")

def show_about():
    """Must run on the Tk UI thread (see run_on_ui_thread)."""
    tk_root.attributes('-topmost', True)
    messagebox.showinfo(
        "About YT-DLP Manager",
        "YT-DLP Native Messaging Host & Tray Manager\n"
        "Version: 1.6.0\n\n"
        "Features:\n"
        "- 'Console' is now an in-app log window (reliable minimize/close-to-tray)\n"
        "- Metadata-driven FFmpeg update detection\n"
        "- Auto-updates yt-dlp, ffmpeg, and deno\n"
        "- 4-layer fallback download strategy, every attempt writes into your download folder\n\n"
        f"Config Path:\n{CONFIG_FILE}",
        parent=tk_root
    )
    tk_root.attributes('-topmost', False)

def exit_app():
    terminal_log("Exiting application from tray menu...")
    global tray_icon
    if tray_icon:
        tray_icon.stop() # This causes tray_icon.run() to return
    # Force exit to immediately kill any blocking background threads (like stdin read)
    os._exit(0)

def create_tray_icon_image():
    """Dynamically generates a 64x64 icon so no external .ico file is needed."""
    image = Image.new('RGB', (64, 64), color=(32, 33, 36)) # Dark background
    draw = ImageDraw.Draw(image)
    # Draw a light blue circle
    draw.ellipse([8, 8, 56, 56], fill=(138, 180, 248))
    # Draw a dark play button triangle in the center
    draw.polygon([(28, 20), (48, 32), (28, 44)], fill=(32, 33, 36))
    return image

# ==============================================================================
# 4. Integrated Updater Logic
# ==============================================================================
def update_ytdlp():
    terminal_log("[Updater] Checking yt-dlp...")
    local_version = None
    if YTDLP_PATH.exists():
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
                terminal_log(f"[Updater] Local yt-dlp binary version: {local_version}")
        except Exception as e:
            terminal_log(f"[Updater] Failed to query yt-dlp binary: {e}")
            
    if not local_version:
        version_file = SCRIPT_DIR / "yt-dlp.version.txt"
        if version_file.exists():
            local_version = version_file.read_text().strip()
            terminal_log(f"[Updater] Local yt-dlp text file version: {local_version}")

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
        version_file = SCRIPT_DIR / "yt-dlp.version.txt"
        version_file.write_text(latest_version)
        terminal_log(f"[+] yt-dlp updated successfully to {latest_version}.")
    except Exception as e:
        terminal_log(f"[X] yt-dlp download failed: {e}")
        if temp_exe.exists(): temp_exe.unlink()

def update_deno():
    terminal_log("[Updater] Checking deno...")
    local_version = None
    if DENO_PATH.exists():
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
                terminal_log(f"[Updater] Local deno binary version: {local_version}")
        except Exception as e:
            terminal_log(f"[Updater] Failed to query deno binary: {e}")
            
    if not local_version:
        version_file = SCRIPT_DIR / "deno.version.txt"
        if version_file.exists():
            local_version = version_file.read_text().strip().lstrip("v")
            terminal_log(f"[Updater] Local deno text file version: {local_version}")

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
        version_file = SCRIPT_DIR / "deno.version.txt"
        version_file.write_text(latest_version)
        terminal_log(f"[+] deno updated successfully to {latest_version}.")
    except Exception as e:
        terminal_log(f"[X] deno download failed: {e}")
        if temp_zip.exists(): temp_zip.unlink()
        if temp_exe.exists(): temp_exe.unlink()

def update_ffmpeg():
    terminal_log("[Updater] Checking ffmpeg...")
    
    # 1. Get latest release from GitHub API
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
    
    # 2. Find matching asset
    asset = None
    for a in release.get("assets", []):
        if a["name"].startswith("ffmpeg-master-latest-win64-gpl") and a["name"].endswith(".zip"):
            asset = a
            break
    
    if not asset:
        terminal_log("[Updater] No matching ffmpeg asset found in release.")
        return False
        
    latest_asset_name = asset["name"]
    release_id = release.get("id")
    author_id = release.get("author", {}).get("id")
    
    # 3. Load local metadata
    local_meta = {}
    if FFMPEG_VERSION_FILE.exists():
        try:
            content = FFMPEG_VERSION_FILE.read_text().strip()
            if content.startswith("{"):
                local_meta = json.loads(content)
            else:
                # Legacy: just asset name
                local_meta = {"asset_name": content}
        except Exception:
            local_meta = {}
            
    local_asset_name = local_meta.get("asset_name", "None")
    terminal_log(f"[Updater] Latest: {latest_asset_name}")
    terminal_log(f"[Updater] Local:  {local_asset_name}")
    terminal_log(f"[Updater] Release ID: {release_id} | Author ID: {author_id}")
    
    # 4. Check if update is needed
    needs_update = False
    if not local_meta:
        needs_update = True
    elif local_meta.get("release_id") != release_id:
        needs_update = True
    elif local_meta.get("asset_name") != latest_asset_name:
        needs_update = True
    elif local_meta.get("author_id") != author_id:
        needs_update = True
    elif not FFMPEG_PATH.exists():
        needs_update = True
        
    if not needs_update:
        terminal_log("[+] ffmpeg is already up to date.")
        return True
        
    # 5. Download
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
        
    # 6. Extract
    terminal_log("[Updater] Extracting ffmpeg...")
    try:
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall(SCRIPT_DIR)
        temp_zip.unlink()
        
        # Find extracted root folder (e.g., "ffmpeg-master-latest-win64-gpl")
        extracted_root = None
        for item in SCRIPT_DIR.iterdir():
            if item.is_dir() and item.name.startswith("ffmpeg-") and (item / "bin").exists():
                extracted_root = item
                break
                
        if extracted_root:
            bin_dir = extracted_root / "bin"
            # Move ALL files from the bin directory (includes .exe and required .dll files)
            for file_path in bin_dir.iterdir():
                if file_path.is_file():
                    dst = SCRIPT_DIR / file_path.name
                    if dst.exists():
                        dst.unlink()
                    shutil.move(str(file_path), str(dst))
            
            # Clean up the extracted root folder (doc, presets, and the now-empty bin folder)
            shutil.rmtree(extracted_root, ignore_errors=True)
            terminal_log("[+] ffmpeg and its dependencies extracted successfully.")
        else:
            terminal_log("[!] Warning: Could not locate extracted bin/ directory")
            return False
            
    except Exception as e:
        terminal_log(f"[X] Extraction error: {e}")
        if temp_zip.exists(): temp_zip.unlink()
        return False
        
    # 7. Verify and Save Metadata
    if not FFMPEG_PATH.exists():
        terminal_log("[X] ffmpeg.exe not found after extraction")
        return False
        
    metadata = {
        "release_id": release_id,
        "author_id": author_id,
        "asset_name": latest_asset_name,
        "updated_at": release.get("updated_at")
    }
    FFMPEG_VERSION_FILE.write_text(json.dumps(metadata, indent=2))
    
    # 8. Show version
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
    
    # FFmpeg: Always check (uses smart metadata to skip if truly up to date)
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
    
    # 1. Parse Custom Options from Extension and INI
    ext_options_raw = request.get('options', [])
    ext_options = shlex.split(ext_options_raw) if isinstance(ext_options_raw, str) else ext_options_raw
    
    ini_options_str = config.get('Settings', 'default_options', fallback='')
    ini_options = shlex.split(ini_options_str) if ini_options_str else []
    
    custom_options = ini_options + ext_options
    debug_mode = config.getboolean('Settings', 'debug', fallback=False)
    
    # 2. Define Export and Temp Directories
    export_dir = config.get('Settings', 'download_dir', fallback=get_default_download_dir())
    export_path = pathlib.Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    
    temp_dir = SCRIPT_DIR / "temp_downloads"
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

    # 3. Setup yt-dlp arguments with built-in temp-to-export path handling
    # yt-dlp will download to 'temp' and automatically move to 'home' (export) on success
    base_args = [
        str(YTDLP_PATH),
        '-P', f'temp:{temp_dir},home:{export_path}',
        '--newline'  # Ensures line-by-line output for live logging
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

    # Ensure subprocess can find ffmpeg/yt-dlp in SCRIPT_DIR
    env = os.environ.copy()
    env["PATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PATH", "")

    for variation in variations:
        if variation["needs_cookies"] and not cookies_file_ready:
            continue
            
        attempt_num += 1
        
        # Construct final command
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
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=SUBPROCESS_FLAGS  # Keeps execution silent on Windows
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



def load_config():
    config = configparser.ConfigParser()
    default_dir = get_default_download_dir()
    
    if not CONFIG_FILE.exists():
        config['Settings'] = {
            'debug': 'True', 
            'default_options': '--no-playlist',
            'download_dir': default_dir
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f: 
            config.write(f)
    else:
        config.read(CONFIG_FILE, encoding='utf-8')
        if 'download_dir' not in config['Settings']:
            config['Settings']['download_dir'] = default_dir
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)
    return config

# ==============================================================================
# 7. Main Event Loop & Dual-Mode Initialization
# ==============================================================================
def native_messaging_loop():
    """Runs in a background thread to handle browser requests without blocking the tray/terminal."""
    config = load_config()
    while True:
        try:
            request = read_message()
            response = process_download_request(request, config)
            send_message(response)
        except EOFError:
            terminal_log("Browser closed the connection. Tray app and terminal remain active.")
            break # Break loop, but app stays alive because tray_icon.run() blocks main thread
        except Exception as e:
            terminal_log(f"Host error: {str(e)}")
            # Continue loop to accept new connections if browser restarts

def main():
    global tray_icon

    # Real OS console is hidden immediately and permanently. All app "console"
    # output/interaction now goes through our own Tk log window instead, since
    # the real console is owned by a separate process (conhost.exe) and can't
    # be reliably managed (that mismatch was the root cause of the old
    # minimize/close-to-tray bugs).
    create_log_window()
    setup_console_tray_behavior()

    terminal_log("="*60)
    terminal_log("YT-DLP Manager started (System Tray + in-app log window)")
    terminal_log("Use Tray -> 'Open Console' to view logs, or 'Exit' to quit.")
    terminal_log("="*60)

    # Start Daily Updater in a background thread
    threading.Thread(target=updater_loop, daemon=True).start()

    # Start Native Messaging listener in a background thread
    threading.Thread(target=native_messaging_loop, daemon=True).start()

    icon_image = create_tray_icon_image()

    # Menu callbacks that touch Tkinter must be scheduled onto the UI thread,
    # since pystray invokes them from its own thread.
    menu = pystray.Menu(
        pystray.MenuItem('Open Console', run_on_ui_thread(show_log_window)),
        pystray.MenuItem('Close to Tray', run_on_ui_thread(hide_log_window)),
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

    # Run the tray icon in its own background thread (it manages its own
    # window/message loop independent of Tk) and keep Tk's mainloop on the
    # main thread, which Tkinter requires.
    threading.Thread(target=tray_icon.run, daemon=True).start()

    terminal_log("System tray icon initialized. Waiting for requests...")
    tk_root.mainloop()

if __name__ == '__main__':
    main()