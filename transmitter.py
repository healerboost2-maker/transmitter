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
import webbrowser
from functools import partial
import tkinter as tk
from tkinter import messagebox

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
APP_VERSION = "2.1.1"

SUPPORTED_FORMATS = ["Opus", "AAC", "MP3", "WAV", "Raw PCM (s16le)"]
BLOCK_SIZE = 1024
MIN_DB = -60.0
MAX_DB = 0.0

APPDATA_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "GMA DAVAO AMFM Caster"
)
os.makedirs(APPDATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(APPDATA_DIR, "config_gma_caster.json")

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


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
        if not self.encoder or self.fmt == "Raw PCM (s16le)":
            return pcm_bytes

        try:
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).reshape(-1, self.channels)
            frame = av.AudioFrame.from_ndarray(audio_array.T, format='s16', layout='stereo' if self.channels == 2 else 'mono')
            frame.sample_rate = self.sample_rate

            out_bytes = bytearray()
            packets = self.encoder.encode(frame)
            for packet in packets:
                out_bytes.extend(packet.to_bytes())
            return bytes(out_bytes)
        except Exception:
            return pcm_bytes


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
    "minimize_to_tray": True,
    "auto_start_boot": False,
    "auto_start_connect": False,
    "window": {"width": 800, "height": 720, "x": None, "y": None},
    "stations": {
        "am": {
            "station_name": "GMA Super Radyo Davao (AM)",
            "server_url": "ws://localhost:10000/tx/am",
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
            "server_url": "ws://localhost:10000/tx/fm",
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
station_states = {
    "am": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100), "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300), "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0, "input_device_index": 0, "monitor_output_index": 0, "encoder": None
    },
    "fm": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100), "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300), "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0, "input_device_index": 0, "monitor_output_index": 0, "encoder": None
    }
}

ui_elements = {}
all_devices_cache = []
all_host_apis_cache = []
default_in_idx_cache = -1
default_out_idx_cache = -1


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"Error loading config: {e}")
    return cfg


def save_config(station_key=None, show_popup=False):
    if app is None:
        return

    try:
        app.update_idletasks()
        w_width = max(app.winfo_width(), DEFAULT_CONFIG["window"]["width"])
        w_height = max(app.winfo_height(), DEFAULT_CONFIG["window"]["height"])

        config_data = {
            "minimize_to_tray": tray_var.get() if 'tray_var' in globals() else DEFAULT_CONFIG["minimize_to_tray"],
            "auto_start_boot": autostart_var.get() if 'autostart_var' in globals() else DEFAULT_CONFIG["auto_start_boot"],
            "auto_start_connect": autostart_connect_var.get() if 'autostart_connect_var' in globals() else DEFAULT_CONFIG["auto_start_connect"],
            "window": {
                "width": int(w_width), "height": int(w_height),
                "x": int(app.winfo_x()), "y": int(app.winfo_y())
            },
            "stations": {}
        }

        for key, ui in ui_elements.items():
            in_selection = ui["in_device_combo"].get()
            in_idx = station_states[key]["input_device_index"]
            if in_selection and not in_selection.startswith("==="):
                try: in_idx = int(in_selection.split(":")[0])
                except Exception: pass

            out_selection = ui["out_device_combo"].get()
            out_idx = station_states[key]["monitor_output_index"]
            if out_selection and not out_selection.startswith("==="):
                try: out_idx = int(out_selection.split(":")[0])
                except Exception: pass

            config_data["stations"][key] = {
                "station_name": ui["station_name"],
                "server_url": ui["server_url"],
                "input_device_index": in_idx,
                "monitor_output_index": out_idx,
                "monitor_enabled": bool(ui["monitor_var"].get()),
                "format": ui["format_combo"].get(),
                "bitrate": ui["bitrate_combo"].get(),
                "sample_rate": int(ui["sr_combo"].get()),
                "gain_db": ui["dsp_data"]["gain_db"],
                "volume_db": ui["dsp_data"]["volume_db"],
                "noise_gate": ui["dsp_data"]["noise_gate"],
                "gate_enabled": ui["dsp_data"]["gate_enabled"],
                "compressor_db": ui["dsp_data"]["compressor_db"],
                "comp_enabled": ui["dsp_data"]["comp_enabled"],
                "limiter_db": ui["dsp_data"]["limiter_db"],
                "lim_enabled": ui["dsp_data"]["lim_enabled"],
                "hpf_enabled": ui["dsp_data"]["hpf_enabled"],
                "channel_mode": ui["dsp_data"]["channel_mode"],
                "agc_enabled": ui["dsp_data"]["agc_enabled"],
                "multiband": ui.get("multiband_data", DEFAULT_CONFIG["stations"]["am"]["multiband"])
            }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)

        log("[SYSTEM] Saved configuration settings.")
        if show_popup:
            messagebox.showinfo("Save Configuration", "Successfully saved configuration.")
    except Exception as e:
        log(f"Failed to save config: {e}")
        if show_popup:
            messagebox.showerror("Save Error", f"Failed to save configuration:\n{e}")


def set_auto_start(enable):
    if not HAS_WIN32: return
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        if enable:
            exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
            log("[SYSTEM] Enabled auto-start on boot.")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                log("[SYSTEM] Disabled auto-start on boot.")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        log(f"[SYSTEM] Error setting autostart registry: {e}")


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

    ui["out_device_combo"].configure(values=formatted_outputs)
    if out_val_to_set: ui["out_device_combo"].set(out_val_to_set)


def log(message):
    text = f"[{time.strftime('%H:%M:%S')}] {message}\n"
    print(text, end="")
    if app is not None:
        try: app.after(0, lambda: write_log(text))
        except Exception: pass


def write_log(text):
    try:
        if 'log_box' in globals() and log_box.winfo_exists():
            log_box.configure(state="normal")
            log_box.insert("end", text)
            log_box.see("end")
            log_box.configure(state="disabled")
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
            ui["start_btn"].configure(text="START BROADCAST", state="normal", fg_color="#1f538d", hover_color="#14375e")
            ui["status_label"].configure(text="STANDBY", text_color="#888888")
            ui["conn_label"].configure(
                text="ONLINE" if st["connected"] else "OFFLINE",
                text_color="#1fcf71" if st["connected"] else "#e74c3c"
            )

    if app is not None:
        app.after(0, update)


# ============================================================
# WEBSOCKET TRANSMITTER & CONNECTION WORKERS (ADDED)
# ============================================================

def websocket_worker(station_key):
    st, ui = station_states[station_key], ui_elements[station_key]
    url = ui["server_url"]
    log(f"[{station_key.upper()}] Connecting to server at {url}...")
    
    st["connecting"] = True
    update_ui_states(station_key)

    try:
        ws = websocket.create_connection(url, timeout=5)
        st["ws"] = ws
        st["connected"] = True
        st["connecting"] = False
        st["pending_start"] = False
        st["transmitting"] = True
        update_ui_states(station_key)
        log(f"[{station_key.upper()}] Connected and transmitting on air!")

        reg_payload = {
            "type": "register-transmitter",
            "station": ui["station_name"] or f"Station {station_key.upper()}",
            "format": ui["format_combo"].get(),
            "bitrate": ui["bitrate_combo"].get(),
            "sampleRate": int(ui["sr_combo"].get())
        }
        ws.send(json.dumps(reg_payload))

        st["encoder"] = AudioEncoder(
            fmt=ui["format_combo"].get(),
            bitrate=ui["bitrate_combo"].get(),
            sample_rate=int(ui["sr_combo"].get()),
            channels=2
        )

        while not st["queue"].empty():
            try: st["queue"].get_nowait()
            except queue.Empty: break

        while st["connected"] and st["transmitting"]:
            try:
                raw_pcm = st["queue"].get(timeout=0.1)
            except queue.Empty:
                continue

            if st["encoder"]:
                encoded_data = st["encoder"].encode(raw_pcm)
            else:
                encoded_data = raw_pcm

            try:
                ws.send_binary(encoded_data)
            except Exception as e:
                log(f"[{station_key.upper()}] Send error: {e}")
                break

    except Exception as e:
        log(f"[{station_key.upper()}] Connection error: {e}")
    finally:
        st["connected"] = False
        st["connecting"] = False
        st["transmitting"] = False
        st["pending_start"] = False
        st["ws"] = None
        st["encoder"] = None
        update_ui_states(station_key)
        log(f"[{station_key.upper()}] Disconnected from server.")


def start_transmitter(station_key):
    st = station_states[station_key]
    if st["connected"] or st["transmitting"] or st["connecting"]:
        return
    st["pending_start"] = True
    update_ui_states(station_key)
    threading.Thread(target=websocket_worker, args=(station_key,), daemon=True).start()


def disconnect_server(station_key):
    st = station_states[station_key]
    st["transmitting"] = False
    st["connected"] = False
    st["connecting"] = False
    st["pending_start"] = False
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
        if not st["active"]: return
        if st["raw_input_queue"].full():
            try: st["raw_input_queue"].get_nowait()
            except queue.Empty: pass
        st["raw_input_queue"].put_nowait(indata.copy())
    return audio_callback


def dsp_worker_thread(station_key):
    st, ui = station_states[station_key], ui_elements[station_key]
    while st["active"]:
        try: indata = st["raw_input_queue"].get(timeout=0.05)
        except queue.Empty: continue

        processed_bytes = process_audio_dsp(indata, station_key)
        left_db, right_db = calculate_levels(processed_bytes)
        st["left_db"], st["right_db"] = left_db, right_db

        if st["transmitting"]:
            if st["queue"].full():
                try: st["queue"].get_nowait()
                except queue.Empty: pass
            st["queue"].put_nowait(processed_bytes)

        if ui["monitor_var"].get():
            if st["monitor_queue"].full():
                try: st["monitor_queue"].get_nowait()
                except queue.Empty: pass
            st["monitor_queue"].put_nowait(processed_bytes)


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
        
        if st["in_stream"]:
            try: st["in_stream"].stop(); st["in_stream"].close()
            except Exception: pass

        sr = int(ui["sr_combo"].get())
        st["active"] = True
        st["in_stream"] = sd.InputStream(
            samplerate=sr, blocksize=BLOCK_SIZE, device=dev_idx,
            channels=2, dtype="int16", callback=make_audio_callback(station_key)
        )
        st["in_stream"].start()
        
        threading.Thread(target=dsp_worker_thread, args=(station_key,), daemon=True).start()
        ui["in_status"].configure(text="ACTIVE", text_color="#1fcf71")
        log(f"[{station_key.upper()}] Input stream running on device {dev_idx} @ {sr}Hz")
        
        toggle_monitoring(station_key)
        refresh_all_device_dropdowns()
        save_config(station_key)

        if st["connected"] and st["ws"]:
            threading.Thread(target=lambda: send_format_update(station_key), daemon=True).start()
        return True
    except Exception as e:
        st["active"] = False
        ui["in_status"].configure(text="ERROR", text_color="#e74c3c")
        log(f"[{station_key.upper()}] Input error: {e}")
        return False


def send_format_update(station_key):
    st, ui = station_states[station_key], ui_elements[station_key]
    if st["ws"] and st["connected"]:
        try:
            reg_payload = {
                "type": "register-transmitter",
                "station": ui["station_name"] or f"Station {station_key.upper()}",
                "format": ui["format_combo"].get(),
                "bitrate": ui["bitrate_combo"].get(),
                "sampleRate": int(ui["sr_combo"].get())
            }
            st["ws"].send(json.dumps(reg_payload))
        except Exception:
            pass


def toggle_monitoring(station_key):
    ui, st = ui_elements[station_key], station_states[station_key]
    selected_out = ui["out_device_combo"].get()
    if selected_out and not selected_out.startswith("==="):
        try: st["monitor_output_index"] = int(selected_out.split(":")[0])
        except Exception: pass

    if ui["monitor_var"].get() and st["active"]:
        if not selected_out or selected_out.startswith("==="): return
        try:
            out_idx = st["monitor_output_index"]
            if st["out_stream"]:
                try: st["out_stream"].stop(); st["out_stream"].close()
                except Exception: pass
            
            sr = int(ui["sr_combo"].get())
            st["out_stream"] = sd.OutputStream(
                samplerate=sr, blocksize=BLOCK_SIZE, device=out_idx,
                channels=2, dtype="int16", callback=make_monitor_callback(station_key)
            )
            st["out_stream"].start()
            log(f"[{station_key.upper()}] Monitor active on output device ID {out_idx}")
        except Exception as e:
            log(f"[{station_key.upper()}] Monitor output error: {e}")
    else:
        if st["out_stream"]:
            try: st["out_stream"].stop(); st["out_stream"].close()
            except Exception: pass
            st["out_stream"] = None

    refresh_all_device_dropdowns()
    save_config(station_key)


def on_setting_changed(station_key, *args):
    save_config(station_key)
    st = station_states[station_key]
    if st["active"]:
        threading.Thread(target=lambda: activate_station_input(station_key), daemon=True).start()


# ============================================================
# MODALS: NETWORK CONFIG, DSP PROCESSORS & MULTIBAND COMPRESSOR
# ============================================================

def open_network_config_modal(station_key):
    ui = ui_elements[station_key]

    modal = ctk.CTkToplevel(app)
    modal.title(f"Network & Server Config ({station_key.upper()})")
    modal.geometry("460x220")
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(modal, text="Cloud Streaming Target Configuration", font=("Segoe UI", 14, "bold")).pack(pady=12)
    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=12, pady=6)

    ctk.CTkLabel(frame, text="Station Name:").grid(row=0, column=0, sticky="w", padx=12, pady=8)
    name_entry = ctk.CTkEntry(frame, width=280)
    name_entry.insert(0, ui["station_name"])
    name_entry.grid(row=0, column=1, sticky="ew", padx=12, pady=8)

    ctk.CTkLabel(frame, text="Server URL:").grid(row=1, column=0, sticky="w", padx=12, pady=8)
    url_entry = ctk.CTkEntry(frame, width=280)
    url_entry.insert(0, ui["server_url"])
    url_entry.grid(row=1, column=1, sticky="ew", padx=12, pady=8)
    frame.columnconfigure(1, weight=1)

    def save_and_close():
        ui["station_name"] = name_entry.get().strip()
        ui["server_url"] = url_entry.get().strip()
        ui["net_info_lbl"].configure(text=f"Target Name: {ui['station_name']}   |   URL: {ui['server_url']}")
        save_config(station_key)
        log(f"[{station_key.upper()}] Updated network connection target.")
        modal.destroy()

    btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
    btn_frame.pack(fill="x", padx=12, pady=12)
    ctk.CTkButton(btn_frame, text="Save & Close", command=save_and_close).pack(side="right", padx=4)
    ctk.CTkButton(btn_frame, text="Cancel", fg_color="#444444", hover_color="#333333", command=modal.destroy).pack(side="right", padx=4)


def open_dsp_config_modal(station_key):
    ui = ui_elements[station_key]
    dsp = ui["dsp_data"]

    modal = ctk.CTkToplevel(app)
    modal.title(f"Audio DSP & Studio Processors ({station_key.upper()})")
    modal.geometry("520x480")
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(modal, text="Broadcast Audio Processing Studio", font=("Segoe UI", 14, "bold")).pack(pady=10)
    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=12, pady=6)

    def make_slider_row(parent, row_idx, label_text, from_val, to_val, init_val, fmt_str):
        lbl = ctk.CTkLabel(parent, text=label_text)
        lbl.grid(row=row_idx, column=0, sticky="w", padx=6, pady=4)

        scale = ctk.CTkSlider(parent, from_=from_val, to=to_val)
        scale.set(init_val)
        scale.grid(row=row_idx, column=1, sticky="ew", padx=6, pady=4)

        val_lbl = ctk.CTkLabel(parent, text=fmt_str.format(init_val), width=75)
        val_lbl.grid(row=row_idx, column=2, sticky="w", padx=6, pady=4)

        def on_slide(v): val_lbl.configure(text=fmt_str.format(float(v)))
        scale.configure(command=on_slide)
        parent.columnconfigure(1, weight=1)
        return scale

    gain_scale = make_slider_row(frame, 0, "Gain:", 0.0, 36.0, dsp["gain_db"], "+{:.1f} dB")
    vol_scale = make_slider_row(frame, 1, "Volume:", -24.0, 24.0, dsp["volume_db"], "{:+.1f} dB")
    gate_scale = make_slider_row(frame, 2, "Noise Gate:", -60.0, -10.0, dsp["noise_gate"], "{:.1f} dB")
    
    gate_var = ctk.BooleanVar(value=dsp["gate_enabled"])
    ctk.CTkCheckBox(frame, text="On", variable=gate_var).grid(row=2, column=3, padx=6)

    comp_scale = make_slider_row(frame, 3, "Compressor:", -36.0, 0.0, dsp["compressor_db"], "{:.1f} dB")
    comp_var = ctk.BooleanVar(value=dsp["comp_enabled"])
    
    def on_comp_modal_toggle():
        if comp_var.get():
            multiband_btn_modal.configure(state="disabled", fg_color="#444444")
        else:
            multiband_btn_modal.configure(state="normal", fg_color="#1f538d")

    ctk.CTkCheckBox(frame, text="On", variable=comp_var, command=on_comp_modal_toggle).grid(row=3, column=3, padx=6)

    lim_scale = make_slider_row(frame, 4, "Limiter:", -18.0, 0.0, dsp["limiter_db"], "{:.1f} dB")
    lim_var = ctk.BooleanVar(value=dsp["lim_enabled"])
    ctk.CTkCheckBox(frame, text="On", variable=lim_var).grid(row=4, column=3, padx=6)

    ctk.CTkLabel(frame, text="Channel Mode:").grid(row=5, column=0, sticky="w", padx=6, pady=4)
    mode_combo = ctk.CTkOptionMenu(frame, values=["Stereo", "Mono (Downmix L+R)", "Left Channel Only", "Right Channel Only"])
    mode_combo.set(dsp["channel_mode"])
    mode_combo.grid(row=5, column=1, sticky="ew", padx=6, pady=4)

    hpf_var = ctk.BooleanVar(value=dsp["hpf_enabled"])
    ctk.CTkCheckBox(frame, text="HPF Cut", variable=hpf_var).grid(row=6, column=0, padx=6, pady=6, sticky="w")

    agc_var = ctk.BooleanVar(value=dsp["agc_enabled"])
    ctk.CTkCheckBox(frame, text="Auto Gain (AGC)", variable=agc_var).grid(row=6, column=1, sticky="w", padx=6, pady=6)

    multiband_btn_modal = ctk.CTkButton(frame, text="Multiband Setup", command=lambda k=station_key: open_multiband_modal(k))
    multiband_btn_modal.grid(row=6, column=2, columnspan=2, padx=6, pady=6, sticky="ew")

    if comp_var.get():
        multiband_btn_modal.configure(state="disabled", fg_color="#444444")

    def save_and_close():
        dsp["gain_db"] = gain_scale.get()
        dsp["volume_db"] = vol_scale.get()
        dsp["noise_gate"] = gate_scale.get()
        dsp["gate_enabled"] = gate_var.get()
        dsp["compressor_db"] = comp_scale.get()
        dsp["comp_enabled"] = comp_var.get()
        dsp["limiter_db"] = lim_scale.get()
        dsp["lim_enabled"] = lim_var.get()
        dsp["channel_mode"] = mode_combo.get()
        dsp["hpf_enabled"] = hpf_var.get()
        dsp["agc_enabled"] = agc_var.get()

        if "multiband_btn" in ui:
            if dsp["comp_enabled"]:
                ui["multiband_btn"].configure(state="disabled", fg_color="#444444")
            else:
                ui["multiband_btn"].configure(state="normal", fg_color="#1f538d")

        on_setting_changed(station_key)
        log(f"[{station_key.upper()}] Updated DSP & Studio Processor settings.")
        modal.destroy()

    btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
    btn_frame.pack(fill="x", padx=12, pady=10)
    ctk.CTkButton(btn_frame, text="Save & Close", command=save_and_close).pack(side="right", padx=4)
    ctk.CTkButton(btn_frame, text="Cancel", fg_color="#444444", hover_color="#333333", command=modal.destroy).pack(side="right", padx=4)


def open_multiband_modal(station_key):
    ui = ui_elements[station_key]
    if ui["dsp_data"]["comp_enabled"]:
        messagebox.showinfo("Multiband Locked", "Multiband dynamics is disabled while the standard Compressor is enabled.")
        return

    mb_data = ui.get("multiband_data", DEFAULT_CONFIG["stations"]["am"]["multiband"])

    modal = ctk.CTkToplevel(app)
    modal.title(f"Multiband Compressor ({station_key.upper()})")
    modal.geometry("440x380")
    modal.transient(app)
    modal.grab_set()

    ctk.CTkLabel(modal, text="3-Band Broadcast Dynamics Matrix", font=("Segoe UI", 14, "bold")).pack(pady=12)
    frame = ctk.CTkFrame(modal)
    frame.pack(fill="both", expand=True, padx=12, pady=8)

    def make_band_controls(parent, row, band_name, t_val, r_val):
        ctk.CTkLabel(parent, text=band_name, font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(parent, text="Thresh:").grid(row=row+1, column=0, sticky="w", padx=8)
        t_scale = ctk.CTkSlider(parent, from_=-36.0, to=0.0, number_of_steps=36)
        t_scale.set(t_val)
        t_scale.grid(row=row+1, column=1, sticky="ew", padx=8)
        t_lbl = ctk.CTkLabel(parent, text=f"{t_val:.1f} dB", width=60)
        t_lbl.grid(row=row+1, column=2, sticky="w", padx=8)
        t_scale.configure(command=lambda v: t_lbl.configure(text=f"{float(v):.1f} dB"))

        ctk.CTkLabel(parent, text="Ratio:").grid(row=row+2, column=0, sticky="w", padx=8)
        r_scale = ctk.CTkSlider(parent, from_=1.0, to=10.0, number_of_steps=18)
        r_scale.set(r_val)
        r_scale.grid(row=row+2, column=1, sticky="ew", padx=8)
        r_lbl = ctk.CTkLabel(parent, text=f"{r_val:.1f}:1", width=60)
        r_lbl.grid(row=row+2, column=2, sticky="w", padx=8)
        r_scale.configure(command=lambda v: r_lbl.configure(text=f"{float(v):.1f}:1"))

        parent.columnconfigure(1, weight=1)
        return t_scale, r_scale

    t_low, r_low = make_band_controls(frame, 0, "Low Band (<200Hz)", mb_data["low_thresh"], mb_data["low_ratio"])
    t_mid, r_mid = make_band_controls(frame, 3, "Mid Band (200Hz-4kHz)", mb_data["mid_thresh"], mb_data["mid_ratio"])
    t_high, r_high = make_band_controls(frame, 6, "High Band (>4kHz)", mb_data["high_thresh"], mb_data["high_ratio"])

    def save_and_close():
        ui["multiband_data"] = {
            "low_thresh": t_low.get(), "low_ratio": r_low.get(),
            "mid_thresh": t_mid.get(), "mid_ratio": r_mid.get(),
            "high_thresh": t_high.get(), "high_ratio": r_high.get()
        }
        save_config(station_key)
        log(f"[{station_key.upper()}] Updated multiband dynamics settings.")
        modal.destroy()

    btn_frame = ctk.CTkFrame(modal, fg_color="transparent")
    btn_frame.pack(fill="x", padx=12, pady=12)
    ctk.CTkButton(btn_frame, text="Apply & Close", command=save_and_close).pack(side="right", padx=4)
    ctk.CTkButton(btn_frame, text="Cancel", fg_color="#444444", hover_color="#333333", command=modal.destroy).pack(side="right", padx=4)


# ============================================================
# LIVE VU METER CANVAS RENDERING
# ============================================================

def draw_solid_vu_meter(canvas, level_db, channel_label):
    canvas.delete("all")
    width = canvas.winfo_width() or 680
    height = canvas.winfo_height() or 20

    norm = max(0.0, min(1.0, (level_db - MIN_DB) / (MAX_DB - MIN_DB)))
    active_w = max(0, (width - 8) * norm)

    canvas.create_rectangle(4, 3, width - 4, height - 3, fill="#1a1a1a", outline="#2b2b2b")
    if active_w > 0:
        bar_color = "#1fcf71" if level_db < -10.0 else ("#f1c40f" if level_db < -2.0 else "#e74c3c")
        canvas.create_rectangle(4, 4, 4 + active_w, height - 4, fill=bar_color, outline="")

    canvas.create_text(12, height // 2, anchor="w", text=channel_label, fill="#ffffff", font=("Segoe UI", 9, "bold"))
    canvas.create_text(width - 12, height // 2, anchor="e", text=f"{level_db:5.1f} dBFS", fill="#ffffff", font=("Consolas", 8, "bold"))


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
    def show_window(icon, item): app.after(0, show_window_centered)
    def quit_app(icon, item): icon.stop(); app.after(0, force_close)

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


def show_window_centered():
    app.deiconify()
    app.lift()
    app.focus_force()

    window_cfg = cfg.get("window", {})
    width = int(window_cfg.get("width", 800))
    height = int(window_cfg.get("height", 720))
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
    for k in station_states.keys(): save_config(k)
    for k, st in station_states.items():
        st["active"] = False
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
                req = urllib.request.Request(u, headers={'User-Agent': 'GMA-Caster-Ping/2.1'})
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


def open_settings_menu(event=None):
    menu = tk.Menu(app, tearoff=0, bg="#2b2b2b", fg="#ffffff", activebackground="#1f538d", activeforeground="#ffffff")
    menu.add_command(label="Save Configuration", command=lambda: save_config(show_popup=True))
    menu.add_separator()
    menu.add_checkbutton(label="Minimize to Tray", variable=tray_var, command=lambda: save_config())
    menu.add_checkbutton(label="Auto Start on Boot", variable=autostart_var, command=lambda: [set_auto_start(autostart_var.get()), save_config()])
    menu.add_checkbutton(label="Auto Start & Connect on Launch", variable=autostart_connect_var, command=lambda: save_config())
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
# MAIN APPLICATION SETUP
# ============================================================

if not _enforce_single_instance():
    sys.exit(0)

cfg = load_config()

for key in ["am", "fm"]:
    st_cfg = cfg.get("stations", {}).get(key, {})
    station_states[key]["input_device_index"] = st_cfg.get("input_device_index", 0)
    station_states[key]["monitor_output_index"] = st_cfg.get("monitor_output_index", 0)

app = ctk.CTk()
app.title(APP_NAME)

window_cfg = cfg.get("window", {})
app.geometry(f"{window_cfg.get('width', 800)}x{window_cfg.get('height', 720)}")
app.protocol("WM_DELETE_WINDOW", on_window_close)

# --- Top Header Bar ---
header_frame = ctk.CTkFrame(app, corner_radius=0, fg_color="#1f538d")
header_frame.pack(fill="x")

title_lbl = ctk.CTkLabel(header_frame, text=APP_NAME.upper(), font=("Segoe UI", 14, "bold"), text_color="#ffffff")
title_lbl.pack(side="left", padx=16, pady=12)

# Settings Menu Dropdown Button
settings_btn = ctk.CTkButton(header_frame, text="≡ Settings", width=90, fg_color="#14375e", hover_color="#0f2947", command=open_settings_menu)
settings_btn.pack(side="right", padx=12, pady=8)

# --- Global Settings Options ---
tray_var = ctk.BooleanVar(value=cfg.get("minimize_to_tray", True))
autostart_var = ctk.BooleanVar(value=cfg.get("auto_start_boot", False))
autostart_connect_var = ctk.BooleanVar(value=cfg.get("auto_start_connect", False))

# Load device caches
try:
    all_devices_cache = sd.query_devices()
    all_host_apis_cache = sd.query_hostapis()
    default_in_idx_cache, default_out_idx_cache = sd.default.device
except Exception:
    all_devices_cache, all_host_apis_cache = [], []

# --- CustomTkinter TabView ---
notebook = ctk.CTkTabview(app)
notebook.pack(fill="both", expand=True, padx=12, pady=8)

tab_am = notebook.add("AM Station")
tab_fm = notebook.add("FM Station")

stations_config = cfg.get("stations", DEFAULT_CONFIG["stations"])
tabs_map = {"am": tab_am, "fm": tab_fm}

for key in ["am", "fm"]:
    tab = tabs_map[key]
    st_cfg = stations_config.get(key, {})

    # Divider Frame 1: Stream Audio Input & Encoding
    in_frame = ctk.CTkFrame(tab, fg_color="#242424", border_width=1, border_color="#333333")
    in_frame.pack(fill="x", pady=4, padx=4)
    ctk.CTkLabel(in_frame, text="Stream Audio Input & Encoding", font=("Segoe UI", 11, "bold"), text_color="#3a86ff").pack(anchor="w", padx=10, pady=(6, 2))

    in_sub = ctk.CTkFrame(in_frame, fg_color="transparent")
    in_sub.pack(fill="x", padx=6, pady=4)

    ctk.CTkLabel(in_sub, text="Input Device:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
    in_device_combo = ctk.CTkOptionMenu(in_sub, values=["Scanning audio devices..."], command=lambda _, k=key: activate_station_input(k))
    in_device_combo.grid(row=0, column=1, columnspan=3, sticky="ew", padx=6, pady=4)

    ctk.CTkLabel(in_sub, text="Format:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
    format_combo = ctk.CTkOptionMenu(in_sub, values=SUPPORTED_FORMATS, command=lambda _, k=key: on_setting_changed(k))
    format_combo.set(st_cfg.get("format", "Opus"))
    format_combo.grid(row=1, column=1, sticky="ew", padx=6, pady=4)

    ctk.CTkLabel(in_sub, text="Bitrate:").grid(row=1, column=2, sticky="w", padx=6, pady=4)
    bitrate_combo = ctk.CTkOptionMenu(in_sub, values=["64 kbps", "96 kbps", "128 kbps", "192 kbps", "256 kbps", "320 kbps"], command=lambda _, k=key: on_setting_changed(k))
    bitrate_combo.set(st_cfg.get("bitrate", "128 kbps"))
    bitrate_combo.grid(row=1, column=3, sticky="ew", padx=6, pady=4)

    ctk.CTkLabel(in_sub, text="Sample Rate:").grid(row=2, column=0, sticky="w", padx=6, pady=4)
    sr_combo = ctk.CTkOptionMenu(in_sub, values=["22050", "32000", "44100", "48000", "96000"], command=lambda _, k=key: on_setting_changed(k))
    sr_combo.set(str(st_cfg.get("sample_rate", 44100)))
    sr_combo.grid(row=2, column=1, sticky="ew", padx=6, pady=4)

    in_status = ctk.CTkLabel(in_sub, text="INACTIVE", text_color="#e74c3c", font=("Segoe UI", 11, "bold"))
    in_status.grid(row=2, column=2, columnspan=2, sticky="e", padx=10)
    in_sub.columnconfigure(1, weight=1)
    in_sub.columnconfigure(3, weight=1)

    # Divider Frame 2: Secondary Local Monitor (Pass-through)
    out_frame = ctk.CTkFrame(tab, fg_color="#242424", border_width=1, border_color="#333333")
    out_frame.pack(fill="x", pady=4, padx=4)
    ctk.CTkLabel(out_frame, text="Secondary Local Monitor (Pass-through)", font=("Segoe UI", 11, "bold"), text_color="#3a86ff").pack(anchor="w", padx=10, pady=(6, 2))

    out_sub = ctk.CTkFrame(out_frame, fg_color="transparent")
    out_sub.pack(fill="x", padx=6, pady=4)

    ctk.CTkLabel(out_sub, text="Monitor Device:").pack(side="left", padx=6, pady=4)
    out_device_combo = ctk.CTkOptionMenu(out_sub, values=["Scanning..."], command=lambda _, k=key: toggle_monitoring(k))
    out_device_combo.pack(side="left", fill="x", expand=True, padx=6, pady=4)

    monitor_var = ctk.BooleanVar(value=st_cfg.get("monitor_enabled", False))
    monitor_check = ctk.CTkCheckBox(out_sub, text="Monitor On", variable=monitor_var, command=lambda k=key: toggle_monitoring(k))
    monitor_check.pack(side="left", padx=8, pady=4)

    # Divider Frame 3: Network Target
    net_frame = ctk.CTkFrame(tab, fg_color="#242424", border_width=1, border_color="#333333")
    net_frame.pack(fill="x", pady=4, padx=4)
    
    net_header_row = ctk.CTkFrame(net_frame, fg_color="transparent")
    net_header_row.pack(fill="x", padx=10, pady=(6, 2))
    ctk.CTkLabel(net_header_row, text="Network Target", font=("Segoe UI", 11, "bold"), text_color="#3a86ff").pack(side="left")
    
    net_config_btn = ctk.CTkButton(net_header_row, text="Configure Network...", width=130, height=24, font=("Segoe UI", 10, "bold"), command=lambda k=key: open_network_config_modal(k))
    net_config_btn.pack(side="right")

    net_sub = ctk.CTkFrame(net_frame, fg_color="transparent")
    net_sub.pack(fill="x", padx=6, pady=4)
    
    station_name_val = st_cfg.get("station_name", "")
    server_url_val = st_cfg.get("server_url", "")
    
    net_info_lbl = ctk.CTkLabel(net_sub, text=f"Target Name: {station_name_val}   |   URL: {server_url_val}", font=("Segoe UI", 10), text_color="#bbbbbb")
    net_info_lbl.pack(anchor="w", padx=6, pady=4)

    # Divider Frame 4: Audio DSP & Studio Processors Modal Launcher
    dsp_frame = ctk.CTkFrame(tab, fg_color="#242424", border_width=1, border_color="#333333")
    dsp_frame.pack(fill="x", pady=4, padx=4)
    
    dsp_header_row = ctk.CTkFrame(dsp_frame, fg_color="transparent")
    dsp_header_row.pack(fill="x", padx=10, pady=(6, 6))
    ctk.CTkLabel(dsp_header_row, text="Audio DSP & Studio Processors", font=("Segoe UI", 11, "bold"), text_color="#3a86ff").pack(side="left")
    
    dsp_config_btn = ctk.CTkButton(dsp_header_row, text="Configure DSP Processors...", width=160, height=24, font=("Segoe UI", 10, "bold"), command=lambda k=key: open_dsp_config_modal(k))
    dsp_config_btn.pack(side="right")

    # Divider Frame 5: Live VU Meters
    meter_frame = ctk.CTkFrame(tab, fg_color="#242424", border_width=1, border_color="#333333")
    meter_frame.pack(fill="x", pady=4, padx=4)
    ctk.CTkLabel(meter_frame, text="Live VU Meters (Safe | Peak | Clip)", font=("Segoe UI", 11, "bold"), text_color="#3a86ff").pack(anchor="w", padx=10, pady=(6, 2))

    left_meter = ctk.CTkCanvas(meter_frame, height=20, bg="#1a1a1a", highlightthickness=0)
    left_meter.pack(fill="x", pady=2, padx=10)
    right_meter = ctk.CTkCanvas(meter_frame, height=20, bg="#1a1a1a", highlightthickness=0)
    right_meter.pack(fill="x", pady=(2, 6), padx=10)

    # Frame 6: Action Footer
    action_frame = ctk.CTkFrame(tab, fg_color="transparent")
    action_frame.pack(fill="x", pady=4, padx=4)

    conn_label = ctk.CTkLabel(action_frame, text="OFFLINE", text_color="#e74c3c", font=("Segoe UI", 11, "bold"))
    conn_label.pack(side="right", padx=8)

    start_btn = ctk.CTkButton(action_frame, text="START BROADCAST", font=("Segoe UI", 12, "bold"), command=lambda k=key: handle_start_stop_button(k))
    start_btn.pack(side="left", padx=4)

    status_label = ctk.CTkLabel(action_frame, text="STANDBY", font=("Segoe UI", 11, "bold"))
    status_label.pack(side="left", padx=12)

    ui_elements[key] = {
        "station_name": station_name_val, "server_url": server_url_val,
        "net_info_lbl": net_info_lbl,
        "in_device_combo": in_device_combo, "format_combo": format_combo,
        "bitrate_combo": bitrate_combo, "sr_combo": sr_combo,
        "in_status": in_status, "out_device_combo": out_device_combo,
        "monitor_var": monitor_var,
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
        "multiband_data": st_cfg.get("multiband", DEFAULT_CONFIG["stations"]["am"]["multiband"]),
        "left_meter": left_meter, "right_meter": right_meter,
        "conn_label": conn_label, "start_btn": start_btn,
        "status_label": status_label
    }

# Populate initial audio devices
refresh_all_device_dropdowns()
for key in ["am", "fm"]:
    if all_devices_cache:
        app.after(200, lambda k=key: activate_station_input(k))

# --- Console Log Frame ---
log_frame = ctk.CTkFrame(app)
log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 6))
ctk.CTkLabel(log_frame, text="SYSTEM EVENT LOG", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=2)

log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 10), state="disabled")
log_box.pack(fill="both", expand=True, padx=6, pady=4)

# --- Bottom Bar ---
footer_frame = ctk.CTkFrame(app, corner_radius=0, fg_color="#1a1a1a")
footer_frame.pack(fill="x", side="bottom")
ctk.CTkLabel(footer_frame, text=f"Main Author: {APP_AUTHOR} | Version: {APP_VERSION}", text_color="#888888", font=("Segoe UI", 9, "italic")).pack(side="right", padx=12, pady=4)

update_vu_meters()
if HAS_TRAY: setup_tray()

app.after(100, show_window_centered)

if cfg.get("auto_start_connect", False):
    app.after(1500, lambda: [start_transmitter("am"), start_transmitter("fm")])

app.mainloop()