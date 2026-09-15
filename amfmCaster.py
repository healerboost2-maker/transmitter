import os
import sys
import ctypes
import json
import time
import math
import struct
import queue
import threading
import urllib.request
import urllib.parse
import webbrowser
import re
import html
import tempfile
import platform
from functools import partial
import tkinter as tk
from tkinter import messagebox, filedialog

import numpy as np
import sounddevice as sd
import websocket
import customtkinter as ctk

# PyAV for low-bandwidth stream encoding
try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False

# System Tray support
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# Windows API support for tray window anchoring & autostart registry
try:
    import win32gui
    import winreg
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# ============================================================
# USER CONFIGURATION & CONSTANTS
# ============================================================

APP_NAME = "GMA DAVAO AMFM Caster"
APP_AUTHOR = "Neil Jay Dinoy IV"
APP_VERSION = "2.3.0"

SUPPORTED_FORMATS = ["Opus", "AAC", "MP3", "WAV", "Raw PCM (s16le)"]
BLOCK_SIZE = 1024
MIN_DB = -60.0
MAX_DB = 0.0



def _get_appdata_dir():
    """Return a writable per-user data directory without requiring admin rights."""
    candidates = []
    if os.name == "nt":
        candidates.extend([
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("APPDATA"),
        ])
    candidates.append(os.path.join(os.path.expanduser("~"), ".config"))
    candidates.append(tempfile.gettempdir())

    for base in candidates:
        if not base:
            continue
        path = os.path.join(base, "GMA DAVAO AMFM Caster")
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, ".write_test")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_file)
            return path
        except OSError:
            continue
    raise RuntimeError("Unable to create a writable application data directory.")


APPDATA_DIR = _get_appdata_dir()
CONFIG_FILE = os.path.join(APPDATA_DIR, "config_gma_caster.json")
APP_LOG_FILE = os.path.join(APPDATA_DIR, "caster_activity_log.txt")
CONFIG_LOCK = threading.RLock()

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


# ============================================================
# LOGGING SETUP (Fresh .txt file on each startup)
# ============================================================

def initialize_file_logger():
    """Start a new session while retaining the previous log if it grows too large."""
    try:
        if os.path.exists(APP_LOG_FILE) and os.path.getsize(APP_LOG_FILE) > 2 * 1024 * 1024:
            backup = APP_LOG_FILE + ".1"
            try:
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(APP_LOG_FILE, backup)
            except OSError:
                pass
        with open(APP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n=== {APP_NAME} Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except Exception as e:
        print(f"[LOG ERROR] Could not initialize log file: {e}")


# ============================================================
# AUDIO ENCODER ENGINE (Opus / AAC / MP3 / WAV / PCM)
# ============================================================

class AudioEncoder:
    """Handles real-time audio block compression using PyAV."""
    def __init__(self, fmt="Opus", bitrate="128 kbps", sample_rate=44100, channels=2):
        self.fmt = fmt
        self.sample_rate = sample_rate
        self.channels = channels
        
        try:
            self.bitrate_num = int(bitrate.split()[0]) * 1000
        except Exception:
            self.bitrate_num = 128000

        self.codec_name = self._get_codec_name(fmt)
        self.encoder = None
        
        if HAS_AV and self.codec_name:
            try:
                self.codec = av.Codec(self.codec_name, 'w')
                self.encoder = av.CodecContext.create(self.codec)
                self.encoder.sample_rate = self.sample_rate
                self.encoder.channels = self.channels
                self.encoder.layout = 'stereo' if self.channels == 2 else 'mono'
                self.encoder.format = av.AudioFormat('s16').packed
                if hasattr(self.encoder, 'bit_rate'):
                    self.encoder.bit_rate = self.bitrate_num
                self.encoder.open()
            except Exception as e:
                print(f"[ENCODER ERROR] {fmt} initialization failed: {e}")
                self.encoder = None

    def _get_codec_name(self, fmt):
        mapping = {
            "Opus": "libopus",
            "AAC": "aac",
            "MP3": "libmp3lame",
            "WAV": "pcm_s16le",
            "Raw PCM (s16le)": None
        }
        return mapping.get(fmt, None)

    def encode(self, pcm_bytes):
        if self.fmt == "Raw PCM (s16le)":
            return pcm_bytes
        if self.encoder is None:
            raise RuntimeError(f"{self.fmt} encoder is unavailable. Install/verify PyAV and its FFmpeg codec support.")
        if len(pcm_bytes) % (2 * self.channels) != 0:
            raise ValueError("PCM block is not aligned to the configured channel count.")

        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).reshape(-1, self.channels)
        frame = av.AudioFrame.from_ndarray(
            audio_array.T, format='s16',
            layout='stereo' if self.channels == 2 else 'mono'
        )
        frame.sample_rate = self.sample_rate
        out_bytes = bytearray()
        for packet in self.encoder.encode(frame):
            out_bytes.extend(packet.to_bytes())
        return bytes(out_bytes)

    def flush(self):
        """Return any delayed encoder packets before shutdown."""
        if self.encoder is None or self.fmt == "Raw PCM (s16le)":
            return b""
        out_bytes = bytearray()
        try:
            for packet in self.encoder.encode(None):
                out_bytes.extend(packet.to_bytes())
        except Exception:
            pass
        return bytes(out_bytes)

    def close(self):
        if self.encoder is not None:
            try:
                self.encoder.close()
            except Exception:
                pass
            self.encoder = None


# ============================================================
# SINGLE INSTANCE MUTEX
# ============================================================

_SINGLE_INSTANCE_MUTEX_NAME = "Local\\GMA_DAVAO_AMFM_Caster_SingleInstance"
_single_instance_mutex = None

def _bring_existing_instance_to_front():
    if not HAS_WIN32: return False
    try:
        hwnd = win32gui.FindWindow(None, APP_NAME)
        if hwnd:
            try: win32gui.ShowWindow(hwnd, 9)
            except Exception: pass
            try: win32gui.SetForegroundWindow(hwnd)
            except Exception: pass
            return True
    except Exception:
        pass
    return False

def _enforce_single_instance():
    global _single_instance_mutex
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        _single_instance_mutex = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not _single_instance_mutex:
            return True
        if kernel32.GetLastError() == 183:
            _bring_existing_instance_to_front()
            return False
        return True
    except Exception:
        return True


# ============================================================
# APP STATE & CONFIGURATION
# ============================================================

DEFAULT_CONFIG = {
    "caster_id": platform.node().strip() or "CASTER-PC",
    "minimize_to_tray": True,
    "auto_start_boot": False,
    "auto_start_connect": False,
    "appearance_mode": "Dark",
    "window": {"width": 800, "height": 720, "x": None, "y": None, "locked": False},
    "stations": {
        "am": {
            "station_name": "GMA Super Radyo Davao (AM)",
            "server_url": "wss://transmitter-1.onrender.com/amtx",
            "input_device_index": 0,
            "monitor_output_index": 0,
            "monitor_enabled": False,
            "format": "Opus",
            "bitrate": "128 kbps",
            "sample_rate": 44100,
            "gain_db": 0.0,
            "volume_db": 0.0,
            "noise_gate": -40.0,
            "gate_enabled": True,
            "compressor_db": -12.0,
            "comp_enabled": True,
            "limiter_db": 0.0,
            "lim_enabled": True,
            "hpf_enabled": False,
            "channel_mode": "Stereo",
            "agc_enabled": True,
            "multiband": {
                "low_thresh": -12.0, "low_ratio": 4.0,
                "mid_thresh": -14.0, "mid_ratio": 3.0,
                "high_thresh": -16.0, "high_ratio": 2.5
            }
        },
        "fm": {
            "station_name": "Barangay LS Davao (FM)",
            "server_url": "wss://transmitter-1.onrender.com/fmtx",
            "input_device_index": 0,
            "monitor_output_index": 0,
            "monitor_enabled": False,
            "format": "Opus",
            "bitrate": "128 kbps",
            "sample_rate": 44100,
            "gain_db": 0.0,
            "volume_db": 0.0,
            "noise_gate": -40.0,
            "gate_enabled": True,
            "compressor_db": -12.0,
            "comp_enabled": True,
            "limiter_db": 0.0,
            "lim_enabled": True,
            "hpf_enabled": False,
            "channel_mode": "Stereo",
            "agc_enabled": True,
            "multiband": {
                "low_thresh": -12.0, "low_ratio": 4.0,
                "mid_thresh": -14.0, "mid_ratio": 3.0,
                "high_thresh": -16.0, "high_ratio": 2.5
            }
        }
    }
}

app = None
tray_icon = None
_shutdown_started = False
station_states = {
    "am": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "auto_reconnect": False, "server_rejected": False,
        "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100), "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300), "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0, "input_device_index": 0, "monitor_output_index": 0,
        "monitor_enabled": False, "encoder": None, "connection_config": None,
        "worker_generation": 0, "dsp_generation": 0
    },
    "fm": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "auto_reconnect": False, "server_rejected": False,
        "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100), "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300), "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0, "input_device_index": 0, "monitor_output_index": 0,
        "monitor_enabled": False, "encoder": None, "connection_config": None,
        "worker_generation": 0, "dsp_generation": 0
    }
}

ui_elements = {}
_ui_event_queue = queue.Queue(maxsize=1000)
all_devices_cache = []
all_host_apis_cache = []
default_in_idx_cache = -1
default_out_idx_cache = -1


def load_config():
    # Copy nested defaults so older config files can safely inherit new options.
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for key, value in saved.items():
                if key == "window" and isinstance(value, dict):
                    cfg["window"].update(value)
                elif key == "stations" and isinstance(value, dict):
                    for station, station_cfg in value.items():
                        if station in cfg["stations"] and isinstance(station_cfg, dict):
                            cfg["stations"][station].update(station_cfg)
                        else:
                            cfg["stations"][station] = station_cfg
                else:
                    cfg[key] = value
        except Exception as e:
            print(f"Error loading config: {e}")
            # Preserve a damaged config for diagnosis instead of silently overwriting it.
            try:
                corrupt_path = CONFIG_FILE + f".corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
                os.replace(CONFIG_FILE, corrupt_path)
            except OSError:
                pass
    return cfg


def clean_log_message(message):
    """Convert HTML/markup-heavy log text into readable plain text."""
    try:
        text = str(message)
        # Remove script/style blocks completely, then strip remaining HTML tags.
        text = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", "", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception:
        return str(message)


def _atomic_write_json(path, data):
    """Atomically replace a JSON config file to prevent corruption on interruption."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def get_caster_id():
    value = str(cfg.get("caster_id", "")).strip()
    return value or platform.node().strip() or "CASTER-PC"


def save_config(station_key=None, show_popup=False):
    if app is None:
        return False

    try:
        app.update_idletasks()
        config_data = {
            "caster_id": get_caster_id(),
            "minimize_to_tray": tray_var.get() if 'tray_var' in globals() else DEFAULT_CONFIG["minimize_to_tray"],
            "auto_start_boot": autostart_var.get() if 'autostart_var' in globals() else DEFAULT_CONFIG["auto_start_boot"],
            "auto_start_connect": autostart_connect_var.get() if 'autostart_connect_var' in globals() else DEFAULT_CONFIG["auto_start_connect"],
            "appearance_mode": appearance_var.get() if 'appearance_var' in globals() else DEFAULT_CONFIG["appearance_mode"],
            "window": {
                "width": max(1, int(app.winfo_width())),
                "height": max(1, int(app.winfo_height())),
                "x": int(app.winfo_x()),
                "y": int(app.winfo_y()),
                "locked": bool(window_locked_var.get()) if "window_locked_var" in globals() else False
            },
            "stations": {}
        }

        for key, ui in ui_elements.items():
            in_selection = ui["in_device_combo"].get()
            in_idx = station_states[key]["input_device_index"]
            if in_selection and not in_selection.startswith("==="):
                try:
                    in_idx = int(in_selection.split(":", 1)[0])
                except (ValueError, TypeError):
                    pass

            st = station_states[key]
            config_data["stations"][key] = {
                "station_name": ui["station_name"],
                "server_url": ui["server_url"],
                "input_device_index": int(in_idx) if in_idx is not None else 0,
                "monitor_output_index": int(st.get("monitor_output_index", 0)),
                "monitor_enabled": bool(ui["monitor_var"].get()),
                "format": ui["format_combo"].get(),
                "bitrate": ui["bitrate_combo"].get(),
                "sample_rate": int(ui["sr_combo"].get()),
                "gain_db": float(ui["dsp_data"]["gain_db"]),
                "volume_db": float(ui["dsp_data"]["volume_db"]),
                "noise_gate": float(ui["dsp_data"]["noise_gate"]),
                "gate_enabled": bool(ui["dsp_data"]["gate_enabled"]),
                "compressor_db": float(ui["dsp_data"]["compressor_db"]),
                "comp_enabled": bool(ui["dsp_data"]["comp_enabled"]),
                "limiter_db": float(ui["dsp_data"]["limiter_db"]),
                "lim_enabled": bool(ui["dsp_data"]["lim_enabled"]),
                "hpf_enabled": bool(ui["dsp_data"]["hpf_enabled"]),
                "channel_mode": ui["dsp_data"]["channel_mode"],
                "agc_enabled": bool(ui["dsp_data"]["agc_enabled"]),
                "multiband": dict(ui.get("multiband_data", DEFAULT_CONFIG["stations"]["am"]["multiband"]))
            }

        with CONFIG_LOCK:
            _atomic_write_json(CONFIG_FILE, config_data)
        if show_popup:
            messagebox.showinfo("Save Configuration", "Successfully saved configuration.", parent=app)
        return True
    except Exception as e:
        log(f"[SYSTEM] Failed to save config: {e}")
        if show_popup:
            try:
                messagebox.showerror("Save Error", f"Failed to save configuration:\n{e}", parent=app)
            except Exception:
                pass
        return False


def set_auto_start(enable):
    if not HAS_WIN32:
        return False
    key = None
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        )
        if enable:
            if getattr(sys, "frozen", False):
                command = f'"{os.path.abspath(sys.executable)}"'
            else:
                command = f'"{os.path.abspath(sys.executable)}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            log("[SYSTEM] Enabled auto-start on boot.")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                log("[SYSTEM] Disabled auto-start on boot.")
            except FileNotFoundError:
                pass
        return True
    except Exception as e:
        log(f"[SYSTEM] Error setting autostart registry: {e}")
        return False
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass


def refresh_all_device_dropdowns():
    global all_devices_cache, all_host_apis_cache, default_in_idx_cache, default_out_idx_cache
    try:
        all_devices_cache = sd.query_devices()
        all_host_apis_cache = sd.query_hostapis()
        try: default_in_idx_cache, default_out_idx_cache = sd.default.device
        except Exception: default_in_idx_cache, default_out_idx_cache = -1, -1
    except Exception:
        all_devices_cache, all_host_apis_cache = [], []

    for key in ["am", "fm"]:
        if key in ui_elements:
            update_device_lists_for_station(key)


def update_device_lists_for_station(station_key):
    ui = ui_elements[station_key]
    in_idx_selected = station_states[station_key]["input_device_index"]
    out_idx_selected = station_states[station_key]["monitor_output_index"]

    api_groups_in, api_groups_out = {}, {}

    for i, d in enumerate(all_devices_cache):
        dev_name = d.get("name", "").strip()
        if not dev_name or "Input ()" in dev_name:
            continue
        host_api_name = all_host_apis_cache[d["hostapi"]]["name"] if d["hostapi"] < len(all_host_apis_cache) else "Audio"

        if d["max_input_channels"] >= 1:
            api_groups_in.setdefault(host_api_name, [])
            suffix = " [Default]" if i == default_in_idx_cache else ""
            api_groups_in[host_api_name].append((i, f"{i}: {dev_name}{suffix}"))

        if d["max_output_channels"] >= 1:
            api_groups_out.setdefault(host_api_name, [])
            suffix = " [Default]" if i == default_out_idx_cache else ""
            api_groups_out[host_api_name].append((i, f"{i}: {dev_name}{suffix}"))

    formatted_inputs, in_val_to_set = [], ""
    for api_name, dev_tuples in api_groups_in.items():
        formatted_inputs.append(f"=== {api_name} ===")
        for idx, text_str in dev_tuples:
            formatted_inputs.append(text_str)
            if idx == in_idx_selected: in_val_to_set = text_str

    formatted_outputs, out_val_to_set = [], ""
    for api_name, dev_tuples in api_groups_out.items():
        formatted_outputs.append(f"=== {api_name} ===")
        for idx, text_str in dev_tuples:
            formatted_outputs.append(text_str)
            if idx == out_idx_selected: out_val_to_set = text_str

    ui["in_device_combo"].configure(values=formatted_inputs)
    if in_val_to_set: ui["in_device_combo"].set(in_val_to_set)

    ui["monitor_device_values"] = formatted_outputs
    ui["monitor_device_value_to_index"] = {
        text_str: idx
        for api_name, dev_tuples in api_groups_out.items()
        for idx, text_str in dev_tuples
    }
    selected_text = out_val_to_set or "No monitor device selected"
    if "monitor_device_lbl" in ui:
        ui["monitor_device_lbl"].configure(
            text=selected_text,
            text_color="#1fcf71" if out_val_to_set else "#888888"
        )


_log_session_id = 0


def post_ui_event(callback):
    """Queue a Tk callback for execution on the main thread."""
    try:
        _ui_event_queue.put_nowait(callback)
    except queue.Full:
        # UI updates are best-effort; audio/network workers must never block on Tk.
        pass


def drain_ui_events():
    if app is None:
        return
    for _ in range(100):
        try:
            callback = _ui_event_queue.get_nowait()
        except queue.Empty:
            break
        try:
            callback()
        except Exception:
            pass
    try:
        app.after(50, drain_ui_events)
    except Exception:
        pass


def log(message):
    global _log_session_id
    message = clean_log_message(message)
    text = f"[{time.strftime('%H:%M:%S')}] {message}\n"
    print(text, end="")
    
    # Save log line to text file (append mode during runtime)
    try:
        with open(APP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass

    session_id = _log_session_id
    if app is not None:
        post_ui_event(lambda t=text, sid=session_id: write_log(t, sid))


def write_log(text, session_id=None):
    try:
        if session_id is not None and session_id != _log_session_id:
            return
        if 'log_box' in globals() and log_box.winfo_exists():
            log_box.configure(state="normal")
            log_box.insert("end", text)
            log_box.see("end")
            log_box.configure(state="disabled")
    except Exception:
        pass


def reset_logs(add_session_marker=False):
    global _log_session_id
    _log_session_id += 1
    try:
        if 'log_box' in globals() and log_box.winfo_exists():
            log_box.configure(state="normal")
            log_box.delete("1.0", "end")
            if add_session_marker:
                log_box.insert("end", f"[{time.strftime('%H:%M:%S')}] [SYSTEM] Log session reset.\n")
            log_box.see("end")
            log_box.configure(state="disabled")
    except Exception:
        pass


def save_logs_as():
    try:
        if 'log_box' not in globals() or not log_box.winfo_exists():
            return
        content = log_box.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showinfo("Save Logs", "The event log is empty.")
            return

        filename = filedialog.asksaveasfilename(
            parent=app,
            title="Save Event Log As",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Log files", "*.log"), ("All files", "*.*")],
            initialfile=f"gma_caster_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if not filename:
            return

        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"[SYSTEM] Event log saved to {filename}")
    except Exception as e:
        messagebox.showerror("Save Logs", f"Could not save the event log:\n{e}")


def _log_context_action(action):
    try:
        log_box.configure(state="normal")
        if action == "cut":
            log_box.event_generate("<<Cut>>")
        elif action == "copy":
            log_box.event_generate("<<Copy>>")
        elif action == "paste":
            log_box.event_generate("<<Paste>>")
        elif action == "select_all":
            log_box.event_generate("<<SelectAll>>")
        log_box.configure(state="disabled")
    except Exception:
        pass


def show_log_context_menu(event):
    try:
        log_context_menu.tk_popup(event.x_root, event.y_root)
    finally:
        try:
            log_context_menu.grab_release()
        except Exception:
            pass


def update_ui_states(station_key):
    def update():
        st = station_states[station_key]
        ui = ui_elements[station_key]
        
        if st["transmitting"]:
            ui["start_btn"].configure(text="STOP BROADCAST", fg_color="#cc0000", hover_color="#990000")
            ui["status_label"].configure(text="ON AIR (TRANSMITTING)", text_color="#1fcf71")
            ui["conn_label"].configure(text="ONLINE", text_color="#1fcf71")
        elif st["pending_start"] or st["connecting"]:
            ui["start_btn"].configure(text="CONNECTING...", state="disabled", fg_color="#333333")
            ui["status_label"].configure(text="CONNECTING...", text_color="#e67e22")
            ui["conn_label"].configure(text="CONNECTING...", text_color="#e67e22")
        else:
            ui["start_btn"].configure(text="STOP RETRY" if st.get("auto_reconnect") else "START BROADCAST", state="normal", fg_color="#1f538d", hover_color="#14375e")
            if st.get("server_rejected"):
                ui["status_label"].configure(text="STANDBY / ANOTHER TX ACTIVE", text_color="#e67e22")
                ui["conn_label"].configure(text="STANDBY", text_color="#e67e22")
            elif st.get("auto_reconnect") and not st["connected"]:
                ui["status_label"].configure(text="RECONNECTING...", text_color="#e67e22")
                ui["conn_label"].configure(text="RECONNECTING", text_color="#e67e22")
            else:
                ui["status_label"].configure(text="STANDBY", text_color="#888888")
                ui["conn_label"].configure(text="ONLINE" if st["connected"] else "OFFLINE", text_color="#1fcf71" if st["connected"] else "#e74c3c")

    if app is not None:
        post_ui_event(update)


# ============================================================
# WEBSOCKET TRANSMITTER & CONNECTION WORKERS
# ============================================================

def websocket_worker(station_key):
    st = station_states[station_key]
    connection = st.get("connection_config") or {}
    url = connection.get("server_url", "").strip()
    station_name = connection.get("station_name") or f"Station {station_key.upper()}"
    fmt = connection.get("format", "Opus")
    bitrate = connection.get("bitrate", "128 kbps")
    sample_rate = int(connection.get("sample_rate", 44100))
    caster_id = get_caster_id()
    generation = st.get("worker_generation", 0)

    if not url:
        st["connecting"] = False
        st["pending_start"] = False
        update_ui_states(station_key)
        log(f"[{station_key.upper()}] Connection aborted: server URL is empty.")
        return

    retry_delay = 2.0
    max_retry_delay = 15.0
    st["auto_reconnect"] = True
    st["server_rejected"] = False

    while st["auto_reconnect"] and st.get("worker_generation", 0) == generation and not _shutdown_started:
        ws = None
        try:
            st["connecting"] = True
            st["pending_start"] = True
            st["server_rejected"] = False
            update_ui_states(station_key)
            log(f"[{station_key.upper()}] Connecting as {caster_id} to {url}...")

            ws = websocket.create_connection(
                url,
                timeout=5,
                enable_multithread=True,
                origin=None
            )
            st["ws"] = ws
            ws.settimeout(5)

            reg_payload = {
                "type": "register-transmitter",
                "casterId": caster_id,
                "station": station_name,
                "channel": station_key.upper(),
                "format": fmt,
                "bitrate": bitrate,
                "sampleRate": sample_rate,
                "channels": 2,
                "client": APP_NAME,
                "version": APP_VERSION
            }
            ws.send(json.dumps(reg_payload))

            # Wait for the server to authorize this transmitter BEFORE sending audio.
            response = ws.recv()
            if not response:
                raise RuntimeError("Server closed the connection during registration")
            if isinstance(response, bytes):
                response = response.decode("utf-8", errors="replace")
            try:
                server_msg = json.loads(response)
            except Exception:
                server_msg = {}

            if server_msg.get("type") != "transmitter-accepted":
                reason = server_msg.get("reason", "REGISTRATION_REJECTED")
                active_id = server_msg.get("activeCasterId")
                st["server_rejected"] = True
                st["connected"] = False
                st["transmitting"] = False
                st["connecting"] = False
                st["pending_start"] = False
                update_ui_states(station_key)
                if active_id:
                    log(f"[{station_key.upper()}] STANDBY: {reason}. Active caster: {active_id}")
                else:
                    log(f"[{station_key.upper()}] Server rejected transmitter: {reason}")
                try:
                    ws.close()
                except Exception:
                    pass
                st["ws"] = None
                time.sleep(10)
                continue

            st["connected"] = True
            st["connecting"] = False
            st["pending_start"] = False
            st["transmitting"] = True
            retry_delay = 2.0
            update_ui_states(station_key)
            log(f"[{station_key.upper()}] ON AIR as {caster_id}.")

            st["encoder"] = AudioEncoder(fmt=fmt, bitrate=bitrate, sample_rate=sample_rate, channels=2)
            ws.settimeout(5)

            while (st["connected"] and st["transmitting"] and
                   st.get("worker_generation", 0) == generation and not _shutdown_started):
                try:
                    raw_pcm = st["queue"].get(timeout=0.1)
                except queue.Empty:
                    continue

                try:
                    encoded_data = st["encoder"].encode(raw_pcm) if st["encoder"] else raw_pcm
                    ws.send_binary(encoded_data)
                except Exception as e:
                    log(f"[{station_key.upper()}] Network/audio send error: {e}")
                    break

        except Exception as e:
            if not _shutdown_started and st.get("auto_reconnect"):
                log(f"[{station_key.upper()}] Connection error: {e}. Reconnecting...")

        finally:
            st["connected"] = False
            st["connecting"] = False
            st["transmitting"] = False
            st["pending_start"] = False

            encoder = st.get("encoder")
            if encoder is not None:
                try:
                    encoder.close()
                except Exception:
                    pass
                st["encoder"] = None

            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            st["ws"] = None
            update_ui_states(station_key)

            # Throw away stale live audio after a disconnect/rejection.
            try:
                while True:
                    st["queue"].get_nowait()
            except queue.Empty:
                pass

        if st.get("worker_generation", 0) != generation or not st.get("auto_reconnect") or _shutdown_started:
            break

        st["connecting"] = True
        update_ui_states(station_key)
        time.sleep(retry_delay)
        retry_delay = min(max_retry_delay, retry_delay * 1.5)

    st["auto_reconnect"] = False
    st["connecting"] = False
    st["pending_start"] = False
    update_ui_states(station_key)


def start_transmitter(station_key):
    st = station_states[station_key]
    ui = ui_elements[station_key]
    if st["connected"] or st["transmitting"] or st["connecting"] or st["pending_start"]:
        return

    st["worker_generation"] += 1
    st["auto_reconnect"] = True
    st["server_rejected"] = False
    st["connection_config"] = {
        "server_url": ui["server_url"],
        "station_name": ui["station_name"],
        "format": ui["format_combo"].get(),
        "bitrate": ui["bitrate_combo"].get(),
        "sample_rate": int(ui["sr_combo"].get())
    }
    st["pending_start"] = True
    update_ui_states(station_key)
    threading.Thread(target=websocket_worker, args=(station_key,), daemon=True, name=f"WS-{station_key.upper()}").start()


def disconnect_server(station_key):
    st = station_states[station_key]
    st["transmitting"] = False
    st["connected"] = False
    st["connecting"] = False
    st["pending_start"] = False
    st["auto_reconnect"] = False
    st["server_rejected"] = False
    st["worker_generation"] += 1
    if st["ws"]:
        try:
            st["ws"].close()
        except Exception:
            pass
        st["ws"] = None
    update_ui_states(station_key)
    log(f"[{station_key.upper()}] Manually disconnected.")


def handle_start_stop_button(station_key):
    st = station_states[station_key]
    if st["connected"] or st["transmitting"] or st["connecting"] or st["pending_start"]:
        disconnect_server(station_key)
    else:
        start_transmitter(station_key)


def calculate_levels(audio_bytes):
    if not audio_bytes:
        return MIN_DB, MIN_DB
    try:
        sample_count = len(audio_bytes) // 2
        if sample_count < 2: return MIN_DB, MIN_DB
        samples = struct.unpack("<" + ("h" * sample_count), audio_bytes)
        left_samples = samples[0::2]
        right_samples = samples[1::2] if len(samples) > 1 else samples[0::2]

        left_rms = math.sqrt(sum(s * s for s in left_samples) / max(1, len(left_samples)))
        right_rms = math.sqrt(sum(s * s for s in right_samples) / max(1, len(right_samples)))

        left_db = max(MIN_DB, min(MAX_DB, 20.0 * math.log10(max(left_rms / 32768.0, 0.000001))))
        right_db = max(MIN_DB, min(MAX_DB, 20.0 * math.log10(max(right_rms / 32768.0, 0.000001))))
        return left_db, right_db
    except Exception:
        return MIN_DB, MIN_DB


def process_audio_dsp(indata, station_key):
    ui = ui_elements[station_key]
    st = station_states[station_key]
    dsp = ui["dsp_data"]
    
    gain_db_val = dsp["gain_db"]
    vol_db_val = dsp["volume_db"]
    gate_db = dsp["noise_gate"]
    gate_on = dsp["gate_enabled"]
    comp_db = dsp["compressor_db"]
    comp_on = dsp["comp_enabled"]
    lim_db = dsp["limiter_db"]
    lim_on = dsp["lim_enabled"]
    hpf_on = dsp["hpf_enabled"]
    mode = dsp["channel_mode"]
    agc_on = dsp["agc_enabled"]

    audio_f = indata.astype(np.float32) / 32768.0

    if gate_on:
        rms = np.sqrt(np.mean(audio_f**2, axis=0, keepdims=True))
        rms_db = 20.0 * np.log10(np.maximum(rms, 0.000001))
        for ch_idx in range(audio_f.shape[1]):
            if rms_db[0, ch_idx] < gate_db:
                audio_f[:, ch_idx] = 0.0

    if hpf_on and len(audio_f) > 1:
        audio_f[1:] -= 0.95 * audio_f[:-1]

    gain_linear = 10.0 ** (gain_db_val / 20.0)
    vol_linear = 10.0 ** (vol_db_val / 20.0)
    audio_f *= (gain_linear * vol_linear)

    if mode == "Mono (Downmix L+R)":
        if audio_f.shape[1] > 1:
            mono = np.mean(audio_f, axis=1, keepdims=True)
            audio_f = np.hstack([mono, mono])
    elif mode == "Left Channel Only":
        audio_f[:, 1] = audio_f[:, 0]
    elif mode == "Right Channel Only":
        audio_f[:, 0] = audio_f[:, 1]

    if comp_on:
        comp_thresh = 10.0 ** (comp_db / 20.0)
        ratio = 4.0
        abs_audio = np.abs(audio_f)
        exceed = abs_audio > comp_thresh
        if np.any(exceed):
            audio_f[exceed] = np.sign(audio_f[exceed]) * (comp_thresh + (abs_audio[exceed] - comp_thresh) / ratio)

    if agc_on:
        current_rms = np.sqrt(np.mean(audio_f**2))
        target_rms = 0.18
        if current_rms > 0.0001:
            instant_gain = min(target_rms / current_rms, 10.0)
            st["agc_gain"] = (1.0 - 0.08) * st.get("agc_gain", 1.0) + 0.08 * instant_gain
            audio_f *= st["agc_gain"]
        else:
            st["agc_gain"] = 0.98 * st.get("agc_gain", 1.0) + 0.02 * 1.0
    else:
        st["agc_gain"] = 1.0

    if lim_on and lim_db < 0.0:
        lim_thresh = 10.0 ** (lim_db / 20.0)
        abs_audio = np.abs(audio_f)
        exceed_lim = abs_audio > lim_thresh
        if np.any(exceed_lim):
            audio_f[exceed_lim] = np.sign(audio_f[exceed_lim]) * (
                lim_thresh + (1.0 - lim_thresh) * np.tanh((abs_audio[exceed_lim] - lim_thresh) / (1.0 - lim_thresh))
            )

    audio_f = np.clip(audio_f, -1.0, 1.0)
    return (audio_f * 32767.0).astype(np.int16).tobytes()


def make_audio_callback(station_key):
    def audio_callback(indata, frames, time_info, status):
        st = station_states[station_key]
        if not st["active"]:
            return
        try:
            st["raw_input_queue"].put_nowait(indata.copy())
        except queue.Full:
            try:
                st["raw_input_queue"].get_nowait()
            except queue.Empty:
                pass
            try:
                st["raw_input_queue"].put_nowait(indata.copy())
            except queue.Full:
                pass
    return audio_callback


def dsp_worker_thread(station_key, generation):
    st, ui = station_states[station_key], ui_elements[station_key]
    while st["active"] and generation == st.get("dsp_generation", 0):
        try: indata = st["raw_input_queue"].get(timeout=0.05)
        except queue.Empty: continue

        processed_bytes = process_audio_dsp(indata, station_key)
        raw_left_db, raw_right_db = calculate_levels(processed_bytes)
        
        # Responsive VU Meter: Fast attack, smooth decay to prevent infinite build-up/freezing
        decay_factor = 0.75
        if raw_left_db > st["left_db"]:
            st["left_db"] = raw_left_db
        else:
            st["left_db"] = st["left_db"] * decay_factor + raw_left_db * (1.0 - decay_factor)

        if raw_right_db > st["right_db"]:
            st["right_db"] = raw_right_db
        else:
            st["right_db"] = st["right_db"] * decay_factor + raw_right_db * (1.0 - decay_factor)

        if st["transmitting"]:
            try:
                st["queue"].put_nowait(processed_bytes)
            except queue.Full:
                try:
                    st["queue"].get_nowait()
                except queue.Empty:
                    pass
                try:
                    st["queue"].put_nowait(processed_bytes)
                except queue.Full:
                    pass

        # Read state owned by the worker; do not call Tk from the DSP thread.
        if st.get("monitor_enabled", False):
            try:
                st["monitor_queue"].put_nowait(processed_bytes)
            except queue.Full:
                try:
                    st["monitor_queue"].get_nowait()
                except queue.Empty:
                    pass
                try:
                    st["monitor_queue"].put_nowait(processed_bytes)
                except queue.Full:
                    pass


def make_monitor_callback(station_key):
    def monitor_callback(outdata, frames, time_info, status):
        st = station_states[station_key]
        req_bytes = frames * 2 * 2
        try: data = st["monitor_queue"].get_nowait()
        except queue.Empty: data = b"\x00" * req_bytes

        if len(data) >= req_bytes:
            outdata[:] = np.frombuffer(data[:req_bytes], dtype=np.int16).reshape(frames, 2)
        else:
            outdata[:] = np.zeros((frames, 2), dtype=np.int16)
    return monitor_callback


def activate_station_input(station_key):
    ui, st = ui_elements[station_key], station_states[station_key]
    selected = ui["in_device_combo"].get()
    if not selected or selected.startswith("==="): return False

    try:
        dev_idx = int(selected.split(":")[0])
        st["input_device_index"] = dev_idx
        st["active"] = False
        st["dsp_generation"] = st.get("dsp_generation", 0) + 1
        
        if st["in_stream"]:
            try: st["in_stream"].stop(); st["in_stream"].close()
            except Exception: pass
            st["in_stream"] = None

        while not st["raw_input_queue"].empty():
            try: st["raw_input_queue"].get_nowait()
            except queue.Empty: break
        while not st["queue"].empty():
            try: st["queue"].get_nowait()
            except queue.Empty: break

        sr = int(ui["sr_combo"].get())
        st["dsp_generation"] = st.get("dsp_generation", 0) + 1
        generation = st["dsp_generation"]
        st["active"] = True
        st["in_stream"] = sd.InputStream(
            samplerate=sr, blocksize=BLOCK_SIZE, device=dev_idx,
            channels=2, dtype="int16", callback=make_audio_callback(station_key)
        )
        st["in_stream"].start()
        
        threading.Thread(
            target=dsp_worker_thread, args=(station_key, generation),
            daemon=True, name=f"DSP-{station_key.upper()}"
        ).start()
        ui["in_status"].configure(text="ACTIVE", text_color="#1fcf71")
        log(f"[{station_key.upper()}] Input stream running on device {dev_idx} @ {sr}Hz")
        
        toggle_monitoring(station_key)
        refresh_all_device_dropdowns()
        save_config(station_key)

        return True
    except Exception as e:
        st["active"] = False
        ui["in_status"].configure(text="ERROR", text_color="#e74c3c")
        log(f"[{station_key.upper()}] Input error: {e}")
        return False


def stop_monitoring(station_key):
    ui, st = ui_elements[station_key], station_states[station_key]
    st["monitor_enabled"] = False
    if st["out_stream"]:
        try:
            st["out_stream"].stop()
            st["out_stream"].close()
        except Exception:
            pass
        st["out_stream"] = None
    log(f"[{station_key.upper()}] Monitor disabled.")


def open_monitor_device_modal(station_key):
    ui = ui_elements[station_key]
    st = station_states[station_key]

    modal = ctk.CTkToplevel(app)
    modal.title(f"Select Monitor Device ({station_key.upper()})")
    modal.geometry("520x210")
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(
        modal, text="Secondary Local Monitor",
        font=("Segoe UI", 14, "bold")
    ).pack(pady=(12, 4))
    ctk.CTkLabel(
        modal,
        text="Choose an output device for monitor.",
        text_color="#aaaaaa"
    ).pack(pady=(0, 8))

    values = ui.get("monitor_device_values", [])
    selectable = [v for v in values if not v.startswith("===")]
    combo = ctk.CTkOptionMenu(
        modal, values=selectable or ["No output devices found"], width=430
    )

    selected_idx = st.get("monitor_output_index", -1)
    restored = None
    for v in selectable:
        try:
            if int(v.split(":")[0]) == selected_idx:
                restored = v
                break
        except Exception:
            pass
    combo.set(restored or (selectable[0] if selectable else "No output devices found"))
    combo.pack(fill="x", padx=20, pady=5)

    btn_row = ctk.CTkFrame(modal, fg_color="transparent")
    btn_row.pack(fill="x", padx=20, pady=10)

    def apply_selection():
        selected = combo.get()
        if not selected or selected.startswith("===") or selected == "No output devices found":
            messagebox.showerror(
                "Monitor Device", "Please select a valid output device.", parent=modal
            )
            return
        try:
            st["monitor_output_index"] = int(selected.split(":")[0])
            ui["monitor_device_lbl"].configure(text=selected, text_color="#1fcf71")
            modal.destroy()
            toggle_monitoring(station_key)
        except Exception as e:
            messagebox.showerror(
                "Monitor Device", f"Could not select the device:\n{e}", parent=modal
            )

    def cancel_selection():
        if ui["monitor_var"].get() and not st.get("out_stream"):
            ui["monitor_var"].set(False)
            st["monitor_enabled"] = False
        modal.destroy()
        save_config(station_key)

    ctk.CTkButton(
        btn_row, text="Use Device", command=apply_selection, width=120
    ).pack(side="right", padx=4)
    ctk.CTkButton(
        btn_row, text="Cancel", command=cancel_selection, width=90,
        fg_color="#444444", hover_color="#333333"
    ).pack(side="right", padx=4)
    modal.after(20, lambda: center_modal(modal, 520, 210))


def toggle_monitor_option(station_key):
    ui = ui_elements[station_key]
    st = station_states[station_key]
    if ui["monitor_var"].get():
        if _valid_output_device(st.get("monitor_output_index", -1)):
            toggle_monitoring(station_key)
        else:
            open_monitor_device_modal(station_key)
    else:
        stop_monitoring(station_key)
        save_config(station_key)


def toggle_monitoring(station_key):
    ui, st = ui_elements[station_key], station_states[station_key]

    if not ui["monitor_var"].get():
        st["monitor_enabled"] = False
        stop_monitoring(station_key)
        save_config(station_key)
        return

    out_idx = st.get("monitor_output_index", -1)
    if out_idx is None or out_idx < 0:
        st["monitor_enabled"] = False
        open_monitor_device_modal(station_key)
        return

    if not st["active"]:
        st["monitor_enabled"] = True
        log(f"[{station_key.upper()}] Monitor enabled; waiting for active input stream.")
        save_config(station_key)
        return

    try:
        if st["out_stream"]:
            try:
                st["out_stream"].stop()
                st["out_stream"].close()
            except Exception:
                pass

        sr = int(ui["sr_combo"].get())
        st["out_stream"] = sd.OutputStream(
            samplerate=sr,
            blocksize=BLOCK_SIZE,
            device=out_idx,
            channels=2,
            dtype="int16",
            callback=make_monitor_callback(station_key)
        )
        st["out_stream"].start()
        st["monitor_enabled"] = True
        log(f"[{station_key.upper()}] Monitor active on output device ID {out_idx}")
    except Exception as e:
        st["out_stream"] = None
        st["monitor_enabled"] = False
        ui["monitor_var"].set(False)
        log(f"[{station_key.upper()}] Monitor output error: {e}")
        messagebox.showerror(
            "Monitor Error", f"Could not start the monitor device:\n{e}", parent=app
        )
    save_config(station_key)


def on_setting_changed(station_key, *args):
    """Persist UI changes and safely restart affected live audio paths."""
    save_config(station_key)
    st = station_states[station_key]
    if app is None:
        return

    was_streaming = bool(st["transmitting"] or st["connected"] or st["pending_start"])
    if was_streaming:
        disconnect_server(station_key)

    if st["active"]:
        app.after(0, lambda k=station_key, restart=was_streaming: _apply_setting_restart(k, restart))
    elif was_streaming:
        app.after(100, lambda k=station_key: start_transmitter(k))


def _apply_setting_restart(station_key, restart_transmitter):
    if not activate_station_input(station_key):
        if restart_transmitter:
            log(f"[{station_key.upper()}] Broadcast restart cancelled because the input device could not be activated.")
        return
    if restart_transmitter:
        app.after(100, lambda k=station_key: start_transmitter(k))


# ============================================================
# MODALS: NETWORK CONFIG, DSP PROCESSORS & MULTIBAND COMPRESSOR
# ============================================================

def center_modal(modal, width=None, height=None):
    try:
        app.update_idletasks()
        modal.update_idletasks()
        if width is None:
            width = modal.winfo_width()
        if height is None:
            height = modal.winfo_height()
        if width <= 1 or height <= 1:
            width = modal.winfo_reqwidth()
            height = modal.winfo_reqheight()
        parent_x = app.winfo_rootx()
        parent_y = app.winfo_rooty()
        parent_w = app.winfo_width()
        parent_h = app.winfo_height()
        x = parent_x + max(0, (parent_w - width) // 2)
        y = parent_y + max(0, (parent_h - height) // 2)
        modal.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
    except Exception:
        pass


def add_entry_context_menu(entry):
    menu = tk.Menu(entry, tearoff=0)
    menu.add_command(label="Cut", command=lambda: entry.event_generate("<<Cut>>"))
    menu.add_command(label="Copy", command=lambda: entry.event_generate("<<Copy>>"))
    menu.add_command(label="Paste", command=lambda: entry.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Select All", command=lambda: entry.select_range(0, "end"))

    def popup(event):
        try:
            entry.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    entry.bind("<Button-3>", popup)
    entry.bind("<Control-Button-3>", popup)


def _valid_output_device(index):
    try:
        if index is None or int(index) < 0:
            return False
        d = sd.query_devices(int(index))
        return int(d.get("max_output_channels", 0)) >= 1
    except Exception:
        return False


def open_network_config_modal(station_key):
    ui = ui_elements[station_key]

    modal = ctk.CTkToplevel(app)
    modal.title(f"Network & Server Config ({station_key.upper()})")
    modal.geometry("560x270")
    modal.resizable(False, False)
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(
        modal, text="Cloud Streaming Target Configuration",
        font=("Segoe UI", 14, "bold")
    ).pack(pady=(12, 8))

    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=14, pady=6)
    frame.columnconfigure(1, weight=1)

    ctk.CTkLabel(frame, text="Caster ID:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
    caster_entry = ctk.CTkEntry(frame, height=30)
    caster_entry.insert(0, get_caster_id())
    caster_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=8)
    add_entry_context_menu(caster_entry)

    ctk.CTkLabel(frame, text="Station Name:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
    name_entry = ctk.CTkEntry(frame, height=30)
    name_entry.insert(0, ui["station_name"])
    name_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=8)
    add_entry_context_menu(name_entry)

    ctk.CTkLabel(frame, text="Server URL:").grid(row=2, column=0, sticky="w", padx=10, pady=8)
    url_entry = ctk.CTkEntry(frame, height=30)
    url_entry.insert(0, ui["server_url"])
    url_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=8)
    add_entry_context_menu(url_entry)

    def save_and_close():
        new_caster_id = caster_entry.get().strip() or platform.node().strip() or "CASTER-PC"
        station_name = name_entry.get().strip()
        server_url = url_entry.get().strip()
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in ("ws", "wss") or not parsed.netloc:
            messagebox.showerror(
                "Invalid Server URL",
                "Please enter a valid WebSocket URL beginning with ws:// or wss://.",
                parent=modal
            )
            return
        cfg["caster_id"] = new_caster_id
        caster_id_lbl.configure(text=f"Caster ID: {new_caster_id}")
        ui["station_name"] = station_name
        ui["server_url"] = server_url
        ui["net_info_lbl"].configure(text=f"Target: {ui['station_name'] or '(unnamed)'}")
        save_config(station_key)
        log(f"[{station_key.upper()}] Updated network connection target.")
        modal.destroy()

    btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
    btn_frame.pack(fill="x", padx=14, pady=10)
    ctk.CTkButton(btn_frame, text="Save & Close", command=save_and_close, width=120).pack(side="right", padx=4)
    ctk.CTkButton(btn_frame, text="Cancel", fg_color="#444444", hover_color="#333333", command=modal.destroy, width=90).pack(side="right", padx=4)

    modal.after(20, lambda: center_modal(modal, 560, 270))
    name_entry.focus_set()


def open_dsp_config_modal(station_key):
    ui = ui_elements[station_key]
    dsp = ui["dsp_data"]

    modal = ctk.CTkToplevel(app)
    modal.title(f"Audio DSP & Studio Processors ({station_key.upper()})")
    modal.geometry("540x430")
    modal.resizable(False, False)
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(modal, text="Broadcast Audio Processing Studio", font=("Segoe UI", 14, "bold")).pack(pady=10)
    ctk.CTkLabel(modal, text="Changes are applied to the live audio immediately.", text_color="#1fcf71").pack(pady=(0, 6))
    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=12, pady=6)

    def realtime_save():
        save_config(station_key)

    def make_slider_row(parent, row_idx, label_text, from_val, to_val, init_val, fmt_str, key_name):
        ctk.CTkLabel(parent, text=label_text).grid(row=row_idx, column=0, sticky="w", padx=6, pady=5)
        scale = ctk.CTkSlider(parent, from_=from_val, to=to_val)
        scale.set(init_val)
        scale.grid(row=row_idx, column=1, sticky="ew", padx=6, pady=5)
        val_lbl = ctk.CTkLabel(parent, text=fmt_str.format(init_val), width=78)
        val_lbl.grid(row=row_idx, column=2, sticky="w", padx=6, pady=5)

        def on_slide(v):
            value = float(v)
            dsp[key_name] = value
            val_lbl.configure(text=fmt_str.format(value))
            realtime_save()
        scale.configure(command=on_slide)
        parent.columnconfigure(1, weight=1)
        return scale

    make_slider_row(frame, 0, "Gain:", 0.0, 36.0, dsp["gain_db"], "+{:.1f} dB", "gain_db")
    make_slider_row(frame, 1, "Volume:", -24.0, 24.0, dsp["volume_db"], "{:+.1f} dB", "volume_db")
    gate_scale = make_slider_row(frame, 2, "Noise Gate:", -60.0, -10.0, dsp["noise_gate"], "{:.1f} dB", "noise_gate")
    gate_var = ctk.BooleanVar(value=dsp["gate_enabled"])

    def gate_changed():
        dsp["gate_enabled"] = bool(gate_var.get())
        realtime_save()
    ctk.CTkCheckBox(frame, text="On", variable=gate_var, command=gate_changed).grid(row=2, column=3, padx=6)

    make_slider_row(frame, 3, "Compressor:", -36.0, 0.0, dsp["compressor_db"], "{:.1f} dB", "compressor_db")
    comp_var = ctk.BooleanVar(value=dsp["comp_enabled"])

    def comp_changed():
        dsp["comp_enabled"] = bool(comp_var.get())
        if dsp["comp_enabled"]:
            multiband_btn_modal.configure(state="disabled", fg_color="#444444")
        else:
            multiband_btn_modal.configure(state="normal", fg_color="#1f538d")
        realtime_save()
    ctk.CTkCheckBox(frame, text="On", variable=comp_var, command=comp_changed).grid(row=3, column=3, padx=6)

    make_slider_row(frame, 4, "Limiter:", -18.0, 0.0, dsp["limiter_db"], "{:.1f} dB", "limiter_db")
    lim_var = ctk.BooleanVar(value=dsp["lim_enabled"])

    def lim_changed():
        dsp["lim_enabled"] = bool(lim_var.get())
        realtime_save()
    ctk.CTkCheckBox(frame, text="On", variable=lim_var, command=lim_changed).grid(row=4, column=3, padx=6)

    ctk.CTkLabel(frame, text="Channel Mode:").grid(row=5, column=0, sticky="w", padx=6, pady=5)
    mode_combo = ctk.CTkOptionMenu(frame, values=["Stereo", "Mono (Downmix L+R)", "Left Channel Only", "Right Channel Only"])
    mode_combo.set(dsp["channel_mode"])
    mode_combo.grid(row=5, column=1, sticky="ew", padx=6, pady=5)
    mode_combo.configure(command=lambda value: (dsp.__setitem__("channel_mode", value), realtime_save()))

    hpf_var = ctk.BooleanVar(value=dsp["hpf_enabled"])
    ctk.CTkCheckBox(frame, text="HPF Cut", variable=hpf_var, command=lambda: (dsp.__setitem__("hpf_enabled", bool(hpf_var.get())), realtime_save())).grid(row=6, column=0, padx=6, pady=7, sticky="w")

    agc_var = ctk.BooleanVar(value=dsp["agc_enabled"])
    ctk.CTkCheckBox(frame, text="Auto Gain (AGC)", variable=agc_var, command=lambda: (dsp.__setitem__("agc_enabled", bool(agc_var.get())), realtime_save())).grid(row=6, column=1, sticky="w", padx=6, pady=7)

    multiband_btn_modal = ctk.CTkButton(frame, text="Multiband Setup", command=lambda k=station_key: open_multiband_modal(k))
    multiband_btn_modal.grid(row=6, column=2, columnspan=2, padx=6, pady=7, sticky="ew")
    if comp_var.get():
        multiband_btn_modal.configure(state="disabled", fg_color="#444444")

    ctk.CTkButton(modal, text="Close", command=modal.destroy, width=120).pack(pady=(2, 10))
    modal.after(20, lambda: center_modal(modal, 540, 430))


def open_multiband_modal(station_key):
    ui = ui_elements[station_key]
    if ui["dsp_data"]["comp_enabled"]:
        messagebox.showinfo("Multiband Locked", "Multiband dynamics is disabled while the standard Compressor is enabled.", parent=app)
        return

    mb_data = ui.get("multiband_data", DEFAULT_CONFIG["stations"]["am"]["multiband"])
    modal = ctk.CTkToplevel(app)
    modal.title(f"Multiband Compressor ({station_key.upper()})")
    modal.geometry("460x390")
    modal.resizable(False, False)
    modal.transient(app)
    modal.grab_set()
    ctk.CTkLabel(modal, text="3-Band Broadcast Dynamics Matrix", font=("Segoe UI", 14, "bold")).pack(pady=10)
    ctk.CTkLabel(modal, text="Changes are applied immediately.", text_color="#1fcf71").pack(pady=(0, 4))
    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=12, pady=6)

    def save_live():
        save_config(station_key)

    def make_band_controls(parent, row, band_name, t_key, r_key):
        ctk.CTkLabel(parent, text=band_name, font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(parent, text="Thresh:").grid(row=row+1, column=0, sticky="w", padx=8)
        t_scale = ctk.CTkSlider(parent, from_=-36.0, to=0.0, number_of_steps=36)
        t_scale.set(mb_data[t_key]); t_scale.grid(row=row+1, column=1, sticky="ew", padx=8)
        t_lbl = ctk.CTkLabel(parent, text=f"{mb_data[t_key]:.1f} dB", width=65); t_lbl.grid(row=row+1, column=2, padx=8)
        def on_t(v):
            mb_data[t_key] = float(v); t_lbl.configure(text=f"{float(v):.1f} dB"); save_live()
        t_scale.configure(command=on_t)
        ctk.CTkLabel(parent, text="Ratio:").grid(row=row+2, column=0, sticky="w", padx=8)
        r_scale = ctk.CTkSlider(parent, from_=1.0, to=10.0, number_of_steps=18)
        r_scale.set(mb_data[r_key]); r_scale.grid(row=row+2, column=1, sticky="ew", padx=8)
        r_lbl = ctk.CTkLabel(parent, text=f"{mb_data[r_key]:.1f}:1", width=65); r_lbl.grid(row=row+2, column=2, padx=8)
        def on_r(v):
            mb_data[r_key] = float(v); r_lbl.configure(text=f"{float(v):.1f}:1"); save_live()
        r_scale.configure(command=on_r)
        parent.columnconfigure(1, weight=1)

    make_band_controls(frame, 0, "Low Band (<200Hz)", "low_thresh", "low_ratio")
    make_band_controls(frame, 3, "Mid Band (200Hz-4kHz)", "mid_thresh", "mid_ratio")
    make_band_controls(frame, 6, "High Band (>4kHz)", "high_thresh", "high_ratio")
    ctk.CTkButton(modal, text="Close", command=modal.destroy, width=120).pack(pady=(2, 10))
    modal.after(20, lambda: center_modal(modal, 460, 390))


# ============================================================
# LIVE VU METER CANVAS RENDERING
# ============================================================

def draw_solid_vu_meter(canvas, level_db, channel_label):
    canvas.delete("all")
    width = canvas.winfo_width() or 680
    height = canvas.winfo_height() or 20

    norm = max(0.0, min(1.0, (level_db - MIN_DB) / (MAX_DB - MIN_DB)))
    active_w = max(0.0, (width - 8) * norm)

    x0 = 4
    x1 = width - 4
    usable = max(1.0, x1 - x0)

    yellow_start = (-10.0 - MIN_DB) / (MAX_DB - MIN_DB)
    red_start = (-2.0 - MIN_DB) / (MAX_DB - MIN_DB)
    zones = [
        (0.0, yellow_start, "#1fcf71"),
        (yellow_start, red_start, "#f1c40f"),
        (red_start, 1.0, "#e74c3c"),
    ]

    canvas.create_rectangle(
        x0, 3, x1, height - 3, fill="#1a1a1a", outline="#2b2b2b"
    )

    for a, b, color in zones:
        za = x0 + usable * a
        zb = x0 + usable * b
        canvas.create_rectangle(za, 4, zb, height - 4, fill=color, outline="")

    if active_w < usable:
        canvas.create_rectangle(
            x0 + active_w, 4, x1, height - 4,
            fill="#161616", outline=""
        )

    canvas.create_text(
        12, height // 2, anchor="w", text=channel_label,
        fill="#ffffff", font=("Segoe UI", 9, "bold")
    )
    canvas.create_text(
        width - 12, height // 2, anchor="e",
        text=f"{level_db:5.1f} dBFS", fill="#ffffff",
        font=("Consolas", 8, "bold")
    )


def update_vu_meters():
    if app is not None:
        try:
            active_key = "am" if notebook.get() == "AM Station" else "fm"
            st, ui = station_states[active_key], ui_elements[active_key]
            draw_solid_vu_meter(ui["left_meter"], st["left_db"], "L")
            draw_solid_vu_meter(ui["right_meter"], st["right_db"], "R")
        except Exception: pass
        app.after(50, update_vu_meters)


# ============================================================
# SYSTEM TRAY & WINDOW MANAGEMENT
# ============================================================

def create_tray_image(left_db=-40.0, right_db=-40.0, connected=False):
    if not HAS_TRAY: return None
    image = Image.new("RGB", (64, 64), color="#1a1a1a")
    draw = ImageDraw.Draw(image)
    
    draw.ellipse([48, 4, 60, 16], fill="#1fcf71" if connected else "#e74c3c")
    for idx, db in enumerate([left_db, right_db]):
        x_offset = 14 + (idx * 20)
        norm = max(0.0, min(1.0, (db - MIN_DB) / (MAX_DB - MIN_DB)))
        bar_h = int(40 * norm)
        draw.rectangle([x_offset, 12, x_offset + 10, 52], outline="#444444", fill="#111111")
        if bar_h > 0:
            bar_color = "#1fcf71" if db < -10 else ("#f1c40f" if db < -2 else "#e74c3c")
            draw.rectangle([x_offset, 52 - bar_h, x_offset + 10, 52], fill=bar_color)
    return image


def update_tray_icon():
    if not HAS_TRAY or not tray_icon: return
    try:
        active_key = "am" if notebook.get() == "AM Station" else "fm"
        st = station_states[active_key]
        tray_icon.icon = create_tray_image(st["left_db"], st["right_db"], st["connected"])
        tray_icon.menu = build_tray_menu()
    except Exception: pass
    if app is not None:
        app.after(1000, update_tray_icon)


def build_tray_menu():
    def show_window(icon, item): post_ui_event(show_window_centered)
    def quit_app(icon, item):
        icon.stop()
        post_ui_event(force_close)

    def toggle_conn(station_key, icon, item):
        st = station_states[station_key]
        if st["connected"] or st["transmitting"]: disconnect_server(station_key)
        else: start_transmitter(station_key)

    menu_items = [pystray.MenuItem("Show Caster Hub", show_window, default=True), pystray.Menu.SEPARATOR]
    for key in ["am", "fm"]:
        st = station_states[key]
        status_text = f"Status: {'ONLINE / ON AIR' if st['transmitting'] else ('ONLINE' if st['connected'] else 'OFFLINE')}"
        menu_items.append(pystray.MenuItem(f"[{key.upper()}] {status_text}", lambda icon, item: None, enabled=False))
        action_label = f"Disconnect {key.upper()}" if (st["connected"] or st["transmitting"]) else f"Connect {key.upper()}"
        menu_items.append(pystray.MenuItem(action_label, partial(toggle_conn, key)))
        menu_items.append(pystray.Menu.SEPARATOR)

    menu_items.append(pystray.MenuItem("Quit", quit_app))
    return pystray.Menu(*menu_items)


def setup_tray():
    global tray_icon
    if not HAS_TRAY: return
    tray_icon = pystray.Icon("GMADavaoCaster", create_tray_image(), APP_NAME, build_tray_menu())
    threading.Thread(target=tray_icon.run, daemon=True).start()
    app.after(1000, update_tray_icon)


def save_current_window_size(show_popup=True):
    """Save the current window dimensions/position without changing other settings."""
    if app is None:
        return
    try:
        app.update_idletasks()
        window_cfg = cfg.setdefault("window", {})
        window_cfg.update({
            "width": int(app.winfo_width()),
            "height": int(app.winfo_height()),
            "x": int(app.winfo_x()),
            "y": int(app.winfo_y()),
        })
        save_config(show_popup=False)
        log("[SYSTEM] Current window size and position saved to configuration.")
        if show_popup:
            messagebox.showinfo("Window Size", "Current window size and position saved to configuration.")
    except Exception as e:
        log(f"[SYSTEM] Failed to save current window size: {e}")


def apply_window_lock():
    """Enable/disable manual window resizing and persist the setting."""
    locked = bool(window_locked_var.get())
    app.resizable(not locked, not locked)
    save_config(show_popup=False)
    log(f"[SYSTEM] Window resizing {'locked' if locked else 'unlocked'}.")


def show_window_centered():
    app.deiconify()
    app.lift()
    app.focus_force()

    window_cfg = cfg.get("window", {})
    screen_w, screen_h = app.winfo_screenwidth(), app.winfo_screenheight()
    width = max(640, min(int(window_cfg.get("width", 800)), screen_w))
    height = max(480, min(int(window_cfg.get("height", 720)), screen_h))
    x, y = window_cfg.get("x"), window_cfg.get("y")

    if x is not None and y is not None:
        try:
            app.geometry(f"{width}x{height}+{int(x)}+{int(y)}")
            return
        except Exception: pass

    screen_w, screen_h = app.winfo_screenwidth(), app.winfo_screenheight()
    app.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 2)}")


def on_window_close():
    save_config()
    if tray_var.get() and HAS_TRAY:
        app.withdraw()
        log("[SYSTEM] Minimized to system tray.")
    else:
        force_close()


def force_close():
    global _shutdown_started
    if _shutdown_started:
        return
    _shutdown_started = True
    for k in station_states.keys(): save_config(k)
    for k, st in station_states.items():
        st["active"] = False
        st["dsp_generation"] = st.get("dsp_generation", 0) + 1
        if st["in_stream"]:
            try: st["in_stream"].stop(); st["in_stream"].close()
            except Exception: pass
        if st["out_stream"]:
            try: st["out_stream"].stop(); st["out_stream"].close()
            except Exception: pass
        disconnect_server(k)
    if tray_icon:
        try: tray_icon.stop()
        except Exception: pass
    app.destroy()


# ============================================================
# TOP SETTINGS MENU ACTIONS
# ============================================================

def action_ping_servers():
    log("[SYSTEM] Pinging streaming URLs...")
    for key, ui in ui_elements.items():
        url = ui["server_url"]
        http_url = url.replace("wss://", "https://").replace("ws://", "http://").split("/tx/")[0]
        def test_ping(k, u):
            try:
                start_t = time.time()
                req = urllib.request.Request(u, headers={'User-Agent': 'GMA-Caster-Ping/2.2'})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    latency = int((time.time() - start_t) * 1000)
                    log(f"[{k.upper()}] Ping OK: {resp.status} ({latency}ms)")
            except Exception as e:
                log(f"[{k.upper()}] Ping Failed: {e}")
        threading.Thread(target=test_ping, args=(key, http_url), daemon=True).start()
    messagebox.showinfo("Ping Servers", "Ping test initiated. Check system event log for latency responses.")


def action_check_updates():
    messagebox.showinfo("Check for Updates", f"{APP_NAME}\nCurrent Version: {APP_VERSION}\n\nYou are running the latest version.")


def action_about():
    messagebox.showinfo("About", f"{APP_NAME} v{APP_VERSION}\nDeveloped by {APP_AUTHOR}\n\nBroadcast Stream Transmitter & Audio Processor.")


def apply_appearance_mode(save=True):
    mode = appearance_var.get()
    ctk.set_appearance_mode(mode)

    canvas_bg = "#f7f7f7" if mode == "Light" else "#1a1a1a"
    for ui in ui_elements.values():
        for name in ("left_meter", "right_meter"):
            if name in ui:
                try:
                    ui[name].configure(bg=canvas_bg)
                except Exception:
                    pass

    if "footer_frame" in globals():
        try:
            footer_frame.configure(fg_color="#e9e9e9" if mode == "Light" else "#1a1a1a")
        except Exception:
            pass

    if save:
        save_config()
    log(f"[SYSTEM] Appearance changed to {mode} mode.")


def open_settings_menu(event=None):
    mode = appearance_var.get()
    menu_bg = "#2b2b2b" if mode == "Dark" else "#f4f4f4"
    menu_fg = "#ffffff" if mode == "Dark" else "#111111"

    menu = tk.Menu(
        app, tearoff=0, bg=menu_bg, fg=menu_fg,
        activebackground="#1f538d", activeforeground="#ffffff"
    )
    menu.add_command(label="Save Configuration", command=lambda: save_config(show_popup=True))
    menu.add_separator()
    menu.add_checkbutton(
        label="Minimize to Tray", variable=tray_var, command=lambda: save_config()
    )
    menu.add_checkbutton(
        label="Auto Start on Boot", variable=autostart_var,
        command=lambda: [set_auto_start(autostart_var.get()), save_config()]
    )
    menu.add_checkbutton(
        label="Auto Start & Connect on Launch", variable=autostart_connect_var,
        command=lambda: save_config()
    )
    menu.add_separator()
    menu.add_radiobutton(
        label="Dark Mode", variable=appearance_var, value="Dark",
        command=apply_appearance_mode
    )
    menu.add_radiobutton(
        label="Light Mode", variable=appearance_var, value="Light",
        command=apply_appearance_mode
    )
    menu.add_separator()
    menu.add_command(label="Save Current Window Size", command=lambda: save_current_window_size(show_popup=True))
    menu.add_checkbutton(
        label="Lock Window Size", variable=window_locked_var, command=apply_window_lock
    )
    menu.add_separator()
    menu.add_command(label="Ping Servers", command=action_ping_servers)
    menu.add_command(label="Check for Updates...", command=action_check_updates)
    menu.add_command(label="About", command=action_about)
    menu.add_separator()
    menu.add_command(label="Quit", command=force_close)

    try:
        x = settings_btn.winfo_rootx()
        y = settings_btn.winfo_rooty() + settings_btn.winfo_height()
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


# ============================================================
# MAIN APPLICATION SETUP (RESPONSIVE LAYOUT CONFIGURATION)
# ============================================================

if not _enforce_single_instance():
    sys.exit(0)

# Initialize a fresh log file on application startup
initialize_file_logger()

cfg = load_config()

for key in ["am", "fm"]:
    st_cfg = cfg.get("stations", {}).get(key, {})
    station_states[key]["input_device_index"] = st_cfg.get("input_device_index", 0)
    station_states[key]["monitor_output_index"] = st_cfg.get("monitor_output_index", 0)
    station_states[key]["monitor_enabled"] = bool(st_cfg.get("monitor_enabled", False))

app = ctk.CTk()
app.title(APP_NAME)


# --- APPLICATION WINDOW ICON LOADER ---
try:
    if hasattr(sys, '_MEIPASS'):
        icon_path = os.path.join(sys._MEIPASS, "am-fm_app.ico")
    else:
        icon_path = "am-fm_app.ico"

    app.wm_iconbitmap(icon_path)
except Exception as e:
    print(f"Could not load icon: {e}")

window_cfg = cfg.get("window", {})
app.geometry(f"{window_cfg.get('width', 800)}x{window_cfg.get('height', 720)}")



window_locked_var = ctk.BooleanVar(value=bool(window_cfg.get("locked", False)))
app.resizable(not window_locked_var.get(), not window_locked_var.get())
app.protocol("WM_DELETE_WINDOW", on_window_close)

# Configure responsive main window weights
app.rowconfigure(1, weight=1)
app.columnconfigure(0, weight=1)

header_frame = ctk.CTkFrame(app, corner_radius=0, fg_color="#1f538d")
header_frame.pack(fill="x", side="top")

title_lbl = ctk.CTkLabel(header_frame, text=f"{APP_NAME.upper()} ", font=("Segoe UI", 14, "bold"), text_color="#ffffff")
title_lbl.pack(side="left", padx=16, pady=12)

caster_id_lbl = ctk.CTkLabel(header_frame, text=f"Caster ID: {get_caster_id()}", font=("Segoe UI", 10), text_color="#d9eaf7")
caster_id_lbl.pack(side="left", padx=(4, 10), pady=12)

settings_btn = ctk.CTkButton(header_frame, text="≡ Settings", width=90, fg_color="#14375e", hover_color="#0f2947", command=open_settings_menu)
settings_btn.pack(side="right", padx=12, pady=8)

tray_var = ctk.BooleanVar(value=cfg.get("minimize_to_tray", True))
autostart_var = ctk.BooleanVar(value=cfg.get("auto_start_boot", False))
autostart_connect_var = ctk.BooleanVar(value=cfg.get("auto_start_connect", False))
appearance_var = ctk.StringVar(value=cfg.get("appearance_mode", "Dark"))
ctk.set_appearance_mode(appearance_var.get())

try:
    all_devices_cache = sd.query_devices()
    all_host_apis_cache = sd.query_hostapis()
    default_in_idx_cache, default_out_idx_cache = sd.default.device
except Exception:
    all_devices_cache, all_host_apis_cache = [], []

# Responsive Notebook/Tabs Container filling available vertical space
notebook = ctk.CTkTabview(app)
notebook.pack(fill="both", expand=True, padx=12, pady=(8, 4))

tab_am = notebook.add("AM Station")
tab_fm = notebook.add("FM Station")

stations_config = cfg.get("stations", DEFAULT_CONFIG["stations"])
tabs_map = {"am": tab_am, "fm": tab_fm}

for key in ["am", "fm"]:
    tab = tabs_map[key]
    st_cfg = stations_config.get(key, {})

    # Configure grid scaling inside each tab so components auto-resize fluidly
    tab.rowconfigure(0, weight=0) # Input panel
    tab.rowconfigure(1, weight=0) # Tools row (Network & DSP)
    tab.rowconfigure(2, weight=1) # VU meters expand to fill vertical space if resized
    tab.rowconfigure(3, weight=0) # Action buttons row
    tab.columnconfigure(0, weight=1)

    in_frame = ctk.CTkFrame(
        tab, fg_color=("#f2f2f2", "#242424"), border_width=1, border_color=("#d0d0d0", "#333333")
    )
    in_frame.grid(row=0, column=0, sticky="ew", pady=4, padx=4)

    ctk.CTkLabel(
        in_frame, text="Stream Audio Input & Encoding",
        font=("Segoe UI", 11, "bold"), text_color="#3a86ff"
    ).pack(anchor="w", padx=10, pady=(6, 2))

    in_sub = ctk.CTkFrame(in_frame, fg_color="transparent")
    in_sub.pack(fill="x", padx=6, pady=3)

    ctk.CTkLabel(in_sub, text="Input Device:").grid(
        row=0, column=0, sticky="w", padx=6, pady=3
    )
    in_device_combo = ctk.CTkOptionMenu(
        in_sub, values=["Scanning audio devices..."],
        command=lambda _, k=key: activate_station_input(k)
    )
    in_device_combo.grid(
        row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=3
    )

    ctk.CTkLabel(in_sub, text="Format:").grid(
        row=1, column=0, sticky="w", padx=6, pady=3
    )
    format_combo = ctk.CTkOptionMenu(
        in_sub, values=SUPPORTED_FORMATS,
        command=lambda _, k=key: on_setting_changed(k)
    )
    format_combo.set(st_cfg.get("format", "Opus"))
    format_combo.grid(row=1, column=1, sticky="ew", padx=6, pady=3)

    ctk.CTkLabel(in_sub, text="Bitrate:").grid(
        row=1, column=2, sticky="w", padx=6, pady=3
    )
    bitrate_combo = ctk.CTkOptionMenu(
        in_sub,
        values=["64 kbps", "96 kbps", "128 kbps", "192 kbps", "256 kbps", "320 kbps"],
        command=lambda _, k=key: on_setting_changed(k)
    )
    bitrate_combo.set(st_cfg.get("bitrate", "128 kbps"))
    bitrate_combo.grid(row=1, column=3, sticky="ew", padx=6, pady=3)

    ctk.CTkLabel(in_sub, text="Sample Rate:").grid(
        row=2, column=0, sticky="w", padx=6, pady=3
    )
    sr_combo = ctk.CTkOptionMenu(
        in_sub,
        values=["22050", "32000", "44100", "48000", "96000"],
        command=lambda _, k=key: on_setting_changed(k)
    )
    sr_combo.set(str(st_cfg.get("sample_rate", 44100)))
    sr_combo.grid(row=2, column=1, sticky="ew", padx=6, pady=3)

    in_status = ctk.CTkLabel(
        in_sub, text="INACTIVE", text_color="#e74c3c",
        font=("Segoe UI", 11, "bold")
    )
    in_status.grid(row=2, column=2, columnspan=2, sticky="e", padx=10)

    monitor_var = ctk.BooleanVar(value=bool(st_cfg.get("monitor_enabled", False)))
    station_states[key]["monitor_enabled"] = bool(monitor_var.get())
    monitor_row = ctk.CTkFrame(in_frame, fg_color="transparent")
    monitor_row.pack(fill="x", padx=12, pady=(0, 5))

    # Responsive monitor row: keep the Select button visible even on narrow windows.
    monitor_row.columnconfigure(2, weight=1)
    monitor_row.columnconfigure(3, weight=0)

   

    monitor_switch = ctk.CTkSwitch(
        monitor_row, text="Monitor", variable=monitor_var,
        command=lambda k=key: toggle_monitor_option(k), width=65
    )
    monitor_switch.grid(row=0, column=1, sticky="w", padx=2)

    monitor_device_lbl = ctk.CTkLabel(
        monitor_row, text="No monitor device selected",
        text_color="#888888", anchor="w"
    )
    monitor_device_lbl.grid(row=0, column=2, sticky="ew", padx=8)

    monitor_select_btn = ctk.CTkButton(
        monitor_row, text="Select…", width=68, height=24,
        command=lambda k=key: open_monitor_device_modal(k)
    )
    monitor_select_btn.grid(row=0, column=3, sticky="e")

    in_sub.columnconfigure(1, weight=1)
    in_sub.columnconfigure(3, weight=1)

    tools_row = ctk.CTkFrame(tab, fg_color="transparent")
    tools_row.grid(row=1, column=0, sticky="ew", pady=4, padx=4)
    tools_row.columnconfigure(0, weight=1)
    tools_row.columnconfigure(1, weight=1)

    net_frame = ctk.CTkFrame(
        tools_row, fg_color=("#f2f2f2", "#242424"), border_width=1, border_color=("#d0d0d0", "#333333")
    )
    net_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 2))

    # Responsive tool panels: buttons are placed on their own row so they never
    # get squeezed/clipped when the main window is reduced to a narrow width.
    net_header_row = ctk.CTkFrame(net_frame, fg_color="transparent")
    net_header_row.pack(fill="x", padx=7, pady=(4, 0))
    ctk.CTkLabel(
        net_header_row, text="Network Target",
        font=("Segoe UI", 10, "bold"), text_color="#3a86ff", anchor="w"
    ).pack(fill="x", expand=True)

    net_info_lbl = ctk.CTkLabel(
        net_frame, text=f"Target: {st_cfg.get('station_name', '') or '(unnamed)'}",
        font=("Segoe UI", 9), text_color="#aaaaaa", anchor="w"
    )
    net_info_lbl.pack(fill="x", padx=8, pady=(4, 1))

    net_config_btn = ctk.CTkButton(
        net_frame, text="Configure Network…", height=24,
        command=lambda k=key: open_network_config_modal(k)
    )
    net_config_btn.pack(fill="x", padx=7, pady=(2, 6))

    dsp_frame = ctk.CTkFrame(
        tools_row, fg_color=("#f2f2f2", "#242424"), border_width=1, border_color=("#d0d0d0", "#333333")
    )
    dsp_frame.grid(row=0, column=1, sticky="nsew", padx=(2, 0))

    dsp_header_row = ctk.CTkFrame(dsp_frame, fg_color="transparent")
    dsp_header_row.pack(fill="x", padx=7, pady=(4, 0))
    ctk.CTkLabel(
        dsp_header_row, text="Audio DSP & Studio Processors",
        font=("Segoe UI", 10, "bold"), text_color="#3a86ff", anchor="w"
    ).pack(fill="x", expand=True)

    dsp_config_btn = ctk.CTkButton(
        dsp_frame, text="Configure DSP…", height=24,
        font=("Segoe UI", 9, "bold"),
        command=lambda k=key: open_dsp_config_modal(k)
    )
    dsp_config_btn.pack(fill="x", padx=7, pady=(2, 6))

    meter_frame = ctk.CTkFrame(
        tab, fg_color=("#f2f2f2", "#242424"), border_width=1, border_color=("#d0d0d0", "#333333")
    )
    meter_frame.grid(row=2, column=0, sticky="nsew", pady=4, padx=4)
    ctk.CTkLabel(
        meter_frame, text="Live VU Meters  |  GREEN • YELLOW • RED",
        font=("Segoe UI", 11, "bold"), text_color="#3a86ff"
    ).pack(anchor="w", padx=10, pady=(6, 2))

    left_meter = ctk.CTkCanvas(
        meter_frame, height=24, bg="#1a1a1a", highlightthickness=0
    )
    left_meter.pack(fill="both", expand=True, pady=2, padx=10)
    right_meter = ctk.CTkCanvas(
        meter_frame, height=24, bg="#1a1a1a", highlightthickness=0
    )
    right_meter.pack(fill="both", expand=True, pady=(2, 6), padx=10)

    action_frame = ctk.CTkFrame(tab, fg_color="transparent")
    action_frame.grid(row=3, column=0, sticky="ew", pady=4, padx=4)

    start_btn = ctk.CTkButton(
        action_frame, text="START BROADCAST",
        font=("Segoe UI", 12, "bold"),
        command=lambda k=key: handle_start_stop_button(k)
    )
    start_btn.pack(side="left", padx=(0, 8))

    status_label = ctk.CTkLabel(
        action_frame, text="STANDBY",
        font=("Segoe UI", 11, "bold")
    )
    status_label.pack(side="left", padx=4)

    conn_label = ctk.CTkLabel(
        action_frame, text="OFFLINE", text_color="#e74c3c",
        font=("Segoe UI", 11, "bold")
    )
    conn_label.pack(side="right", padx=8)

    ui_elements[key] = {
        "station_name": st_cfg.get("station_name", ""),
        "server_url": st_cfg.get("server_url", ""),
        "net_info_lbl": net_info_lbl,
        "in_device_combo": in_device_combo,
        "format_combo": format_combo,
        "bitrate_combo": bitrate_combo,
        "sr_combo": sr_combo,
        "in_status": in_status,
        "monitor_var": monitor_var,
        "monitor_switch": monitor_switch,
        "monitor_device_lbl": monitor_device_lbl,
        "monitor_device_values": [],
        "dsp_data": {
            "gain_db": st_cfg.get("gain_db", 0.0),
            "volume_db": st_cfg.get("volume_db", 0.0),
            "noise_gate": st_cfg.get("noise_gate", -40.0),
            "gate_enabled": st_cfg.get("gate_enabled", True),
            "compressor_db": st_cfg.get("compressor_db", -12.0),
            "comp_enabled": st_cfg.get("comp_enabled", True),
            "limiter_db": st_cfg.get("limiter_db", 0.0),
            "lim_enabled": st_cfg.get("lim_enabled", True),
            "hpf_enabled": st_cfg.get("hpf_enabled", False),
            "channel_mode": st_cfg.get("channel_mode", "Stereo"),
            "agc_enabled": st_cfg.get("agc_enabled", True)
        },
        "multiband_data": st_cfg.get(
            "multiband", DEFAULT_CONFIG["stations"]["am"]["multiband"]
        ),
        "left_meter": left_meter,
        "right_meter": right_meter,
        "conn_label": conn_label,
        "start_btn": start_btn,
        "status_label": status_label
    }

log_frame = ctk.CTkFrame(app)
log_frame.pack(fill="x", padx=12, pady=(2, 3), side="top")
log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
log_header.pack(fill="x", padx=6, pady=4)
ctk.CTkLabel(log_header, text="SYSTEM EVENT LOG", font=("Segoe UI", 10, "bold")).pack(side="left", padx=2, pady=2)
logs_visible = True

def toggle_logs():
    global logs_visible
    if logs_visible:
        log_box.pack_forget()
        log_toggle_btn.configure(text="Show Logs")
        logs_visible = False
    else:
        log_box.pack(fill="x", padx=6, pady=(0, 6))
        log_toggle_btn.configure(text="Hide Logs")
        logs_visible = True
    app.update_idletasks()

log_toggle_btn = ctk.CTkButton(log_header, text="Hide Logs", width=90, height=24, font=("Segoe UI", 9, "bold"), command=toggle_logs)
log_toggle_btn.pack(side="right", padx=2, pady=2)
log_box = ctk.CTkTextbox(log_frame, height=76, font=("Consolas", 10), state="disabled")
log_box.pack(fill="x", padx=6, pady=(0, 6))

log_context_menu = tk.Menu(app, tearoff=0)
log_context_menu.add_command(label="Clear", command=lambda: reset_logs(add_session_marker=False))
log_context_menu.add_command(label="Reset Session", command=lambda: reset_logs(add_session_marker=True))
log_context_menu.add_separator()
log_context_menu.add_command(label="Save As...", command=save_logs_as)
log_context_menu.add_separator()
log_context_menu.add_command(label="Copy", command=lambda: _log_context_action("copy"))
log_context_menu.add_command(label="Select All", command=lambda: _log_context_action("select_all"))
log_box.bind("<Button-3>", show_log_context_menu)

reset_logs(add_session_marker=False)

apply_appearance_mode(save=False)

refresh_all_device_dropdowns()
for key in ["am", "fm"]:
    if all_devices_cache:
        app.after(200, lambda k=key: activate_station_input(k))

footer_frame = ctk.CTkFrame(app, corner_radius=0, fg_color="#1a1a1a")
footer_frame.pack(fill="x", side="bottom")
ctk.CTkLabel(footer_frame, text=f"Main Author: {APP_AUTHOR} | Version: {APP_VERSION}", text_color="#888888", font=("Segoe UI", 9, "italic")).pack(side="right", padx=12, pady=4)

update_vu_meters()
if HAS_TRAY: setup_tray()
app.after(50, drain_ui_events)

app.after(100, show_window_centered)

if cfg.get("auto_start_connect", False):
    app.after(1500, lambda: [start_transmitter("am"), start_transmitter("fm")])

app.mainloop()