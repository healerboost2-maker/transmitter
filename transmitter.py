# source: transmitter.py
import os
import sys
import ctypes
import json
import time
import math
import struct
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from functools import partial

import numpy as np
import sounddevice as sd
import websocket



# ============================================================
# USER CONFIGURATION
# ============================================================

APPDATA_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "GMA DAVAO AMFM Caster"
)

# Make sure the directory exists
os.makedirs(APPDATA_DIR, exist_ok=True)

# Writable user configuration file
CONFIG_FILE = os.path.join(
    APPDATA_DIR,
    "config_gma_caster.json"
)

print("========================================")
print("CONFIG FILE:")
print(CONFIG_FILE)
print("========================================")




# System Tray support
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# Windows API support for tray window anchoring
try:
    import win32gui
    HAS_WIN32GUI = True
except ImportError:
    HAS_WIN32GUI = False


APP_NAME = "GMA DAVAO AMFM Caster"
APP_AUTHOR = "Neil Jay Dinoy IV"
APP_VERSION = "1.0.0.1"


# ============================================================
# SINGLE INSTANCE PROTECTION
# ============================================================
# Keep one running instance of the application.  This works with
# both the Python source and the PyInstaller/ISS executable.
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\GMA_DAVAO_AMFM_Caster_SingleInstance"
_single_instance_mutex = None


def _bring_existing_instance_to_front():
    """Restore and focus the already-running Caster window."""
    try:
        hwnd = win32gui.FindWindow(None, APP_NAME)
        if hwnd:
            try:
                win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            except Exception:
                pass
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _enforce_single_instance():
    """Create a named Windows mutex and stop if another instance exists."""
    global _single_instance_mutex

    if os.name != "nt":
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        _single_instance_mutex = kernel32.CreateMutexW(
            None,
            False,
            _SINGLE_INSTANCE_MUTEX_NAME
        )

        if not _single_instance_mutex:
            # If mutex creation itself fails, do not prevent the app
            # from starting.
            return True

        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            _bring_existing_instance_to_front()
            return False

        return True
    except Exception:
        # Single-instance protection must never break the radio app.
        return True

DEFAULT_CONFIG = {
    "minimize_to_tray": True,
    "auto_start_boot": False,
    "auto_start_connect": False,

    # Main application window settings
    "window": {
        "width": 740,
        "height": 870,
        "x": None,
        "y": None
    },

    "stations": {
        "am": {
            "station_name": "GMA Super Radyo Davao (AM)",
            "server_url": "ws://localhost:10000/tx/am",
            "input_device_index": 0,
            "monitor_output_index": 0,
            "monitor_enabled": False,
            "format": "Raw PCM (s16le)",
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
            "format": "Raw PCM (s16le)",
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

BLOCK_SIZE = 1024
MIN_DB = -60.0
MAX_DB = 0.0

app = None
tray_icon = None
station_states = {
    "am": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100),
        "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300),
        "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0,
        "input_device_index": 0,
        "monitor_output_index": 0
    },
    "fm": {
        "ws": None, "connected": False, "connecting": False, "transmitting": False,
        "pending_start": False, "in_stream": None, "out_stream": None, "active": False,
        "raw_input_queue": queue.Queue(maxsize=100),
        "queue": queue.Queue(maxsize=300),
        "monitor_queue": queue.Queue(maxsize=300),
        "left_db": MIN_DB, "right_db": MIN_DB,
        "agc_gain": 1.0,
        "input_device_index": 0,
        "monitor_output_index": 0
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
        # Make sure Tkinter has updated the actual window geometry.
        app.update_idletasks()

        # Capture the current window size and position.
        window_width = app.winfo_width()
        window_height = app.winfo_height()
        window_x = app.winfo_x()
        window_y = app.winfo_y()

        # Safety fallbacks for the first few milliseconds of startup.
        if window_width <= 1:
            window_width = DEFAULT_CONFIG["window"]["width"]
        if window_height <= 1:
            window_height = DEFAULT_CONFIG["window"]["height"]

        config_data = {
            "minimize_to_tray": tray_var.get() if 'tray_var' in globals() else DEFAULT_CONFIG["minimize_to_tray"],
            "auto_start_boot": autostart_var.get() if 'autostart_var' in globals() else DEFAULT_CONFIG["auto_start_boot"],
            "auto_start_connect": autostart_connect_var.get() if 'autostart_connect_var' in globals() else DEFAULT_CONFIG["auto_start_connect"],

            # Remember window size and position.
            "window": {
                "width": int(window_width),
                "height": int(window_height),
                "x": int(window_x),
                "y": int(window_y)
            },

            "stations": {}
        }

        for key, ui in ui_elements.items():
            in_selection = ui["in_device_combo"].get()
            in_idx = station_states[key]["input_device_index"]
            if in_selection and not in_selection.startswith("==="):
                try:
                    in_idx = int(in_selection.split(":")[0])
                except Exception:
                    pass

            out_selection = ui["out_device_combo"].get()
            out_idx = station_states[key]["monitor_output_index"]
            if out_selection and not out_selection.startswith("==="):
                try:
                    out_idx = int(out_selection.split(":")[0])
                except Exception:
                    pass

            config_data["stations"][key] = {
                "station_name": ui["name_entry"].get().strip(),
                "server_url": ui["url_entry"].get().strip(),
                "input_device_index": in_idx,
                "monitor_output_index": out_idx,
                "monitor_enabled": ui["monitor_var"].get(),
                "format": "Raw PCM (s16le)",
                "bitrate": ui["bitrate_combo"].get(),
                "sample_rate": int(ui["sr_combo"].get()),
                "gain_db": ui["gain_scale"].get(),
                "volume_db": ui["vol_scale"].get(),
                "noise_gate": ui["gate_scale"].get(),
                "gate_enabled": ui["gate_var"].get(),
                "compressor_db": ui["comp_scale"].get(),
                "comp_enabled": ui["comp_var"].get(),
                "limiter_db": ui["lim_scale"].get(),
                "lim_enabled": ui["lim_var"].get(),
                "hpf_enabled": ui["hpf_var"].get(),
                "channel_mode": ui["mode_combo"].get(),
                "agc_enabled": ui["agc_var"].get(),
                "multiband": ui.get(
                    "multiband_data",
                    DEFAULT_CONFIG["stations"]["am"]["multiband"]
                )
            }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)

        log("[SYSTEM] Successfully saved configuration.")

        if show_popup:
            messagebox.showinfo("Save Configuration", "Successfully saved.")

    except Exception as e:
        log(f"Failed to save config: {e}")

        if show_popup:
            messagebox.showerror(
                "Save Error",
                f"Failed to save configuration:\n{e}"
            )


def refresh_all_device_dropdowns():
    global all_devices_cache, all_host_apis_cache, default_in_idx_cache, default_out_idx_cache
    try:
        all_devices_cache = sd.query_devices()
        all_host_apis_cache = sd.query_hostapis()
        try:
            default_in_idx_cache, default_out_idx_cache = sd.default.device
        except Exception:
            default_in_idx_cache, default_out_idx_cache = -1, -1
    except Exception:
        all_devices_cache = []
        all_host_apis_cache = []

    for key in ["am", "fm"]:
        if key in ui_elements:
            update_device_lists_for_station(key)


def update_device_lists_for_station(station_key):
    ui = ui_elements[station_key]
    
    current_in_sel = ui["in_device_combo"].get()
    current_out_sel = ui["out_device_combo"].get()
    
    in_idx_selected = station_states[station_key]["input_device_index"]
    if current_in_sel and not current_in_sel.startswith("==="):
        try:
            in_idx_selected = int(current_in_sel.split(":")[0])
        except Exception:
            pass

    out_idx_selected = station_states[station_key]["monitor_output_index"]
    if current_out_sel and not current_out_sel.startswith("==="):
        try:
            out_idx_selected = int(current_out_sel.split(":")[0])
        except Exception:
            pass

    api_groups_in = {}
    api_groups_out = {}

    for i, d in enumerate(all_devices_cache):
        dev_name = d.get("name", "").strip()
        if not dev_name or "Input ()" in dev_name or dev_name.startswith("Input ("):
            continue
            
        host_api_name = all_host_apis_cache[d["hostapi"]]["name"] if d["hostapi"] < len(all_host_apis_cache) else "Audio"

        if d["max_input_channels"] >= 1:
            if host_api_name not in api_groups_in:
                api_groups_in[host_api_name] = []
            
            indicators = []
            if i == default_in_idx_cache:
                indicators.append("[System Default]")
            
            for skey, st_data in station_states.items():
                if st_data["input_device_index"] == i:
                    indicators.append(f"<<Used for {skey.upper()}>>")
            
            suffix = " " + " ".join(indicators) if indicators else ""
            api_groups_in[host_api_name].append((i, f"{i}: {dev_name}{suffix}"))

        if d["max_output_channels"] >= 1:
            if host_api_name not in api_groups_out:
                api_groups_out[host_api_name] = []
            
            indicators = []
            if i == default_out_idx_cache:
                indicators.append("[System Default]")
            
            suffix = " " + " ".join(indicators) if indicators else ""
            api_groups_out[host_api_name].append((i, f"{i}: {dev_name}{suffix}"))

    formatted_inputs = []
    in_val_to_set = ""
    for api_name, dev_tuples in api_groups_in.items():
        formatted_inputs.append(f"=== {api_name} ===")
        for idx, text_str in dev_tuples:
            formatted_inputs.append(text_str)
            if idx == in_idx_selected:
                in_val_to_set = text_str

    formatted_outputs = []
    out_val_to_set = ""
    for api_name, dev_tuples in api_groups_out.items():
        formatted_outputs.append(f"=== {api_name} ===")
        for idx, text_str in dev_tuples:
            formatted_outputs.append(text_str)
            if idx == out_idx_selected:
                out_val_to_set = text_str

    ui["in_device_combo"]["values"] = formatted_inputs
    if in_val_to_set:
        ui["in_device_combo"].set(in_val_to_set)
    elif formatted_inputs:
        for val in formatted_inputs:
            if not val.startswith("==="):
                ui["in_device_combo"].set(val)
                break

    ui["out_device_combo"]["values"] = formatted_outputs
    if out_val_to_set:
        ui["out_device_combo"].set(out_val_to_set)
    elif formatted_outputs:
        for val in formatted_outputs:
            if not val.startswith("==="):
                ui["out_device_combo"].set(val)
                break


def log(message):
    timestamp = time.strftime("%H:%M:%S")
    text = f"[{timestamp}] {message}\n"
    print(text, end="")
    if app is not None:
        try:
            app.after(0, lambda t=text: write_log(t))
        except Exception:
            pass


def write_log(text):
    try:
        if 'log_box' in globals() and log_box.winfo_exists():
            log_box.insert("end", text)
            log_box.see("end")
    except Exception:
        pass


def update_ui_states(station_key):
    def update():
        st = station_states[station_key]
        ui = ui_elements[station_key]
        
        if st["transmitting"]:
            ui["start_btn"].config(text="STOP BROADCAST", state="normal", style="Danger.TButton")
            ui["status_label"].config(text="ON AIR (TRANSMITTING)", foreground="#003366")
            ui["conn_label"].config(text="ONLINE", foreground="#009933")
        elif st["pending_start"] or st["connecting"]:
            ui["start_btn"].config(text="CONNECTING...", state="disabled", style="TButton")
            ui["status_label"].config(text="CONNECTING...", foreground="#cc8800")
            ui["conn_label"].config(text="CONNECTING...", foreground="#cc8800")
        else:
            ui["start_btn"].config(text="START BROADCAST", state="normal", style="GMA.TButton")
            ui["status_label"].config(text="STANDBY", foreground="gray")
            if st["connected"]:
                ui["conn_label"].config(text="ONLINE", foreground="#009933")
            else:
                ui["conn_label"].config(text="OFFLINE", foreground="#cc0000")

    if app is not None:
        app.after(0, update)


def calculate_levels(audio_bytes):
    if not audio_bytes:
        return MIN_DB, MIN_DB
    try:
        sample_count = len(audio_bytes) // 2
        if sample_count < 2:
            return MIN_DB, MIN_DB
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
    
    gain_db_val = ui["gain_scale"].get()
    vol_db_val = ui["vol_scale"].get()
    
    gate_db = ui["gate_scale"].get()
    gate_on = ui["gate_var"].get()

    comp_db = ui["comp_scale"].get()
    comp_on = ui["comp_var"].get()

    lim_db = ui["lim_scale"].get()
    lim_on = ui["lim_var"].get()

    hpf_on = ui["hpf_var"].get()
    mode = ui["mode_combo"].get()
    agc_on = ui["agc_var"].get()

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
            audio_f[exceed] = np.sign(audio_f[exceed]) * (
                comp_thresh + (abs_audio[exceed] - comp_thresh) / ratio
            )

    if agc_on:
        current_rms = np.sqrt(np.mean(audio_f**2))
        target_rms = 0.18
        
        if current_rms > 0.0001:
            instant_gain = target_rms / current_rms
            instant_gain = min(instant_gain, 10.0)
            
            alpha = 0.08
            st["agc_gain"] = (1.0 - alpha) * st.get("agc_gain", 1.0) + alpha * instant_gain
            
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
        if st["raw_input_queue"].full():
            try:
                st["raw_input_queue"].get_nowait()
            except queue.Empty:
                pass
        st["raw_input_queue"].put_nowait(indata.copy())
    return audio_callback


def dsp_worker_thread(station_key):
    st = station_states[station_key]
    ui = ui_elements[station_key]
    while st["active"]:
        try:
            indata = st["raw_input_queue"].get(timeout=0.05)
        except queue.Empty:
            continue

        processed_bytes = process_audio_dsp(indata, station_key)
        left_db, right_db = calculate_levels(processed_bytes)

        st["left_db"] = left_db
        st["right_db"] = right_db

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
        try:
            data = st["monitor_queue"].get_nowait()
        except queue.Empty:
            data = b"\x00" * req_bytes

        if len(data) >= req_bytes:
            outdata[:] = np.frombuffer(data[:req_bytes], dtype=np.int16).reshape(frames, 2)
        else:
            outdata[:] = np.zeros((frames, 2), dtype=np.int16)
    return monitor_callback


def activate_station_input(station_key):
    ui = ui_elements[station_key]
    st = station_states[station_key]
    selected = ui["in_device_combo"].get()
    
    if not selected or selected.startswith("==="):
        return False

    try:
        dev_idx = int(selected.split(":")[0])
        st["input_device_index"] = dev_idx
        
        st["active"] = False
        if st["in_stream"]:
            try:
                st["in_stream"].stop()
                st["in_stream"].close()
            except Exception:
                pass

        sr = int(ui["sr_combo"].get())
        ch = 2

        while not st["raw_input_queue"].empty():
            try: st["raw_input_queue"].get_nowait()
            except queue.Empty: break
        while not st["queue"].empty():
            try: st["queue"].get_nowait()
            except queue.Empty: break
        while not st["monitor_queue"].empty():
            try: st["monitor_queue"].get_nowait()
            except queue.Empty: break

        st["active"] = True
        st["in_stream"] = sd.InputStream(
            samplerate=sr,
            blocksize=BLOCK_SIZE,
            device=dev_idx,
            channels=ch,
            dtype="int16",
            callback=make_audio_callback(station_key)
        )
        st["in_stream"].start()
        
        threading.Thread(target=dsp_worker_thread, args=(station_key,), daemon=True).start()

        ui["in_status"].config(text="ACTIVE", foreground="#009933")
        log(f"[{station_key.upper()}] Input stream running: {selected} @ {sr}Hz")
        
        toggle_monitoring(station_key)
        refresh_all_device_dropdowns()
        save_config(station_key)

        if st["connected"] and st["ws"]:
            threading.Thread(target=lambda: send_format_update(station_key), daemon=True).start()
        return True

    except Exception as e:
        st["active"] = False
        ui["in_status"].config(text="ERROR", foreground="#cc0000")
        log(f"[{station_key.upper()}] Input activation error: {e}")
        return False


def send_format_update(station_key):
    st = station_states[station_key]
    ui = ui_elements[station_key]
    if st["ws"] and st["connected"]:
        try:
            reg_payload = {
                "type": "register-transmitter",
                "station": ui["name_entry"].get().strip() or f"Station {station_key.upper()}",
                "format": "Raw PCM (s16le)",
                "bitrate": ui["bitrate_combo"].get(),
                "sampleRate": int(ui["sr_combo"].get())
            }
            st["ws"].send(json.dumps(reg_payload))
        except Exception:
            pass


def toggle_monitoring(station_key):
    ui = ui_elements[station_key]
    st = station_states[station_key]
    
    selected_out = ui["out_device_combo"].get()
    if selected_out and not selected_out.startswith("==="):
        try:
            out_idx = int(selected_out.split(":")[0])
            st["monitor_output_index"] = out_idx
        except Exception:
            pass

    if ui["monitor_var"].get() and st["active"]:
        if not selected_out or selected_out.startswith("==="):
            return
        try:
            out_idx = st["monitor_output_index"]
            if st["out_stream"]:
                st["out_stream"].stop()
                st["out_stream"].close()
            
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
            log(f"[{station_key.upper()}] Monitor Output Active on device ID {out_idx}")
        except Exception as e:
            log(f"[{station_key.upper()}] Monitor output error: {e}")
    else:
        if st["out_stream"]:
            try:
                st["out_stream"].stop()
                st["out_stream"].close()
            except Exception:
                pass
            st["out_stream"] = None
            
    refresh_all_device_dropdowns()
    save_config(station_key)


def on_format_changed(station_key):
    on_setting_changed(station_key)


def on_setting_changed(station_key, *args):
    save_config(station_key)
    st = station_states[station_key]
    if st["active"]:
        threading.Thread(target=lambda: activate_station_input(station_key), daemon=True).start()


def connect_server(station_key, on_complete=None):
    st = station_states[station_key]
    if st["connected"]:
        if on_complete:
            app.after(0, lambda: on_complete(True))
        return
    if st["connecting"]:
        return
        
    st["connecting"] = True
    update_ui_states(station_key)

    def background_connect():
        server_url = ui_elements[station_key]["url_entry"].get().strip()
        try:
            new_ws = websocket.create_connection(server_url, timeout=3)
            ui = ui_elements[station_key]
            reg_payload = {
                "type": "register-transmitter",
                "station": ui["name_entry"].get().strip() or f"Station {station_key.upper()}",
                "format": "Raw PCM (s16le)",
                "bitrate": ui["bitrate_combo"].get(),
                "sampleRate": int(ui["sr_combo"].get())
            }
            new_ws.send(json.dumps(reg_payload))

            st["ws"] = new_ws
            st["connected"] = True
            st["connecting"] = False

            update_ui_states(station_key)
            log(f"[{station_key.upper()}] Connected to server successfully.")
            threading.Thread(target=websocket_receive_worker, args=(station_key, new_ws), daemon=True).start()
            
            if on_complete:
                app.after(0, lambda: on_complete(True))
        except Exception as e:
            st["connected"] = False
            st["connecting"] = False
            update_ui_states(station_key)
            log(f"[{station_key.upper()}] Connection failed: {e}")
            if on_complete:
                app.after(0, lambda: on_complete(False))

    threading.Thread(target=background_connect, daemon=True).start()


def disconnect_server(station_key):
    st = station_states[station_key]
    st["pending_start"] = False
    stop_transmitter(station_key)
    if st["ws"]:
        try: st["ws"].close()
        except Exception: pass
        st["ws"] = None

    st["connected"] = False
    st["connecting"] = False
    update_ui_states(station_key)
    log(f"[{station_key.upper()}] Disconnected from server.")


def websocket_receive_worker(station_key, socket):
    socket.settimeout(5.0)
    while station_states[station_key]["connected"]:
        try:
            msg = socket.recv()
            if msg is None:
                break
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:
            disconnect_server(station_key)
            break


def sender_thread(station_key):
    st = station_states[station_key]
    while st["transmitting"]:
        try:
            raw_audio = st["queue"].get(timeout=0.1)
        except queue.Empty:
            continue

        curr_ws = st["ws"]
        if curr_ws and st["connected"]:
            try:
                curr_ws.send_binary(raw_audio)
            except Exception as e:
                log(f"[{station_key.upper()}] Transmission Error: {e}")
                disconnect_server(station_key)
                break


def handle_start_stop_button(station_key):
    st = station_states[station_key]
    if st["transmitting"]:
        stop_transmitter(station_key)
        disconnect_server(station_key)
    else:
        start_transmitter(station_key)


def start_transmitter(station_key):
    st = station_states[station_key]
    
    if not st["active"]:
        success = activate_station_input(station_key)
        if not success:
            messagebox.showwarning("Warning", f"Could not activate input device for {station_key.upper()}.")
            return

    if st["connected"]:
        _kickoff_transmission(station_key)
    elif st["connecting"]:
        st["pending_start"] = True
        update_ui_states(station_key)
        log(f"[{station_key.upper()}] Connecting... Transmission queued to start automatically.")
    else:
        st["pending_start"] = True
        update_ui_states(station_key)
        connect_server(station_key, on_complete=lambda success: _on_start_connected(station_key, success))


def _on_start_connected(station_key, success):
    st = station_states[station_key]
    if success:
        if st.get("pending_start", False):
            _kickoff_transmission(station_key)
    else:
        st["pending_start"] = False
        update_ui_states(station_key)
        messagebox.showwarning("Warning", f"Could not connect to {station_key.upper()} server.")


def _kickoff_transmission(station_key):
    st = station_states[station_key]
    st["pending_start"] = False
    while not st["queue"].empty():
        try: st["queue"].get_nowait()
        except queue.Empty: break

    save_config(station_key)
    st["transmitting"] = True
    threading.Thread(target=sender_thread, args=(station_key,), daemon=True).start()
    update_ui_states(station_key)
    log(f"[{station_key.upper()}] Transmission started.")


def stop_transmitter(station_key):
    st = station_states[station_key]
    st["pending_start"] = False
    st["transmitting"] = False
    update_ui_states(station_key)
    log(f"[{station_key.upper()}] Transmission stopped.")


def open_multiband_modal(station_key):
    ui = ui_elements[station_key]
    mb_data = ui.get("multiband_data", DEFAULT_CONFIG["stations"]["am"]["multiband"])

    modal = tk.Toplevel(app)
    modal.title(f"Multiband Compressor Setup ({station_key.upper()})")
    modal.geometry("420x360")
    modal.transient(app)
    modal.grab_set()

    ttk.Label(modal, text="3-Band Broadcast Dynamics Matrix", font=("Segoe UI", 10, "bold")).pack(pady=8)

    frame = ttk.Frame(modal, padding=10)
    frame.pack(fill="both", expand=True)

    def make_band_controls(parent, row, band_name, t_val, r_val):
        ttk.Label(parent, text=f"{band_name} Band", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=4)
        
        ttk.Label(parent, text="Thresh:").grid(row=row+1, column=0, sticky="w")
        t_scale = ttk.Scale(parent, from_=-36.0, to=0.0, value=t_val, orient="horizontal")
        t_scale.grid(row=row+1, column=1, sticky="ew", padx=4)
        t_lbl = ttk.Label(parent, text=f"{t_val:.1f} dB", width=8, font=("Consolas", 8))
        t_lbl.grid(row=row+1, column=2, sticky="w")
        t_scale.config(command=lambda v: t_lbl.config(text=f"{float(v):.1f} dB"))

        ttk.Label(parent, text="Ratio:").grid(row=row+2, column=0, sticky="w")
        r_scale = ttk.Scale(parent, from_=1.0, to=10.0, value=r_val, orient="horizontal")
        r_scale.grid(row=row+2, column=1, sticky="ew", padx=4)
        r_lbl = ttk.Label(parent, text=f"{r_val:.1f}:1", width=8, font=("Consolas", 8))
        r_lbl.grid(row=row+2, column=2, sticky="w")
        r_scale.config(command=lambda v: r_lbl.config(text=f"{float(v):.1f}:1"))

        parent.columnconfigure(1, weight=1)
        return t_scale, r_scale

    t_low, r_low = make_band_controls(frame, 0, "Low (<200Hz)", mb_data["low_thresh"], mb_data["low_ratio"])
    t_mid, r_mid = make_band_controls(frame, 4, "Mid (200Hz-4kHz)", mb_data["mid_thresh"], mb_data["mid_ratio"])
    t_high, r_high = make_band_controls(frame, 8, "High (>4kHz)", mb_data["high_thresh"], mb_data["high_ratio"])

    def save_and_close():
        ui["multiband_data"] = {
            "low_thresh": t_low.get(), "low_ratio": r_low.get(),
            "mid_thresh": t_mid.get(), "mid_ratio": r_mid.get(),
            "high_thresh": t_high.get(), "high_ratio": r_high.get()
        }
        save_config(station_key)
        log(f"[{station_key.upper()}] Multiband compressor parameters updated successfully.")
        modal.destroy()

    btn_frame = ttk.Frame(modal, padding=8)
    btn_frame.pack(fill="x")
    ttk.Button(btn_frame, text="Apply & Close", command=save_and_close).pack(side="right", padx=4)
    ttk.Button(btn_frame, text="Cancel", command=modal.destroy).pack(side="right", padx=4)


def draw_solid_vu_meter(canvas, level_db, channel_label):
    canvas.delete("all")
    width = canvas.winfo_width() or 680
    height = canvas.winfo_height() or 18

    norm = max(0.0, min(1.0, (level_db - MIN_DB) / (MAX_DB - MIN_DB)))
    active_w = max(0, (width - 8) * norm)

    canvas.create_rectangle(4, 3, width - 4, height - 3, fill="#121212", outline="#333333")
    if active_w > 0:
        if level_db < -10.0:
            bar_color = "#009933"  # GMA Green/Safe
        elif level_db < -2.0:
            bar_color = "#ffcc00"  # GMA Yellow/Peak
        else:
            bar_color = "#cc0000"  # GMA Red/Clip

        canvas.create_rectangle(4, 4, 4 + active_w, height - 4, fill=bar_color, outline="")

    canvas.create_text(10, height // 2, anchor="w", text=channel_label, fill="#ffffff", font=("Segoe UI", 8, "bold"))
    canvas.create_text(width - 10, height // 2, anchor="e", text=f"{level_db:5.1f} dBFS", fill="#ffffff", font=("Consolas", 8, "bold"))


def update_vu_meters():
    if app is not None:
        try:
            current_tab = notebook.index(notebook.select())
            active_key = "am" if current_tab == 0 else "fm"
            st = station_states[active_key]
            ui = ui_elements[active_key]
            draw_solid_vu_meter(ui["left_meter"], st["left_db"], "L")
            draw_solid_vu_meter(ui["right_meter"], st["right_db"], "R")
        except Exception:
            pass
        app.after(50, update_vu_meters)


# --- System Tray Management ---
def create_tray_image(left_db=-40.0, right_db=-40.0, connected=False):
    if not HAS_TRAY:
        return None
    image = Image.new("RGB", (64, 64), color="#003366")
    draw = ImageDraw.Draw(image)
    
    status_color = "#009933" if connected else "#cc0000"
    draw.ellipse([48, 4, 60, 16], fill=status_color)
    
    for idx, db in enumerate([left_db, right_db]):
        x_offset = 14 + (idx * 20)
        norm = max(0.0, min(1.0, (db - MIN_DB) / (MAX_DB - MIN_DB)))
        bar_h = int(40 * norm)
        draw.rectangle([x_offset, 52 - 40, x_offset + 10, 52], outline="#ffffff", fill="#111111")
        if bar_h > 0:
            bar_color = "#009933" if db < -10 else ("#ffcc00" if db < -2 else "#cc0000")
            draw.rectangle([x_offset, 52 - bar_h, x_offset + 10, 52], fill=bar_color)
            
    return image


def update_tray_icon():
    if not HAS_TRAY or not tray_icon:
        return
    try:
        current_tab = notebook.index(notebook.select()) if 'notebook' in globals() else 0
        active_key = "am" if current_tab == 0 else "fm"
        st = station_states[active_key]
        tray_icon.icon = create_tray_image(st["left_db"], st["right_db"], st["connected"])
        tray_icon.menu = build_tray_menu()
    except Exception:
        pass
    if app is not None:
        app.after(1000, update_tray_icon)


def build_tray_menu():
    def show_window(icon, item):
        app.after(0, show_window_at_tray)

    def quit_app(icon, item):
        icon.stop()
        app.after(0, force_close)

    def toggle_conn(station_key, icon, item):
        st = station_states[station_key]
        if st["connected"] or st["transmitting"]:
            disconnect_server(station_key)
        else:
            start_transmitter(station_key)

    menu_items = [
        pystray.MenuItem("Show Caster Hub", show_window, default=True),
        pystray.Menu.SEPARATOR
    ]

    for key in ["am", "fm"]:
        st = station_states[key]
        
        status_text = f"Status: {'ONLINE / ON AIR' if st['transmitting'] else ('ONLINE' if st['connected'] else 'OFFLINE')}"
        menu_items.append(pystray.MenuItem(f"[{key.upper()}] {status_text}", lambda icon, item: None, enabled=False))

        vu_text = f"  L: {st['left_db']:.1f}dB | R: {st['right_db']:.1f}dB"
        menu_items.append(pystray.MenuItem(f"  {vu_text}", lambda icon, item: None, enabled=False))

        action_label = f"Disconnect {key.upper()}" if (st["connected"] or st["transmitting"]) else f"Connect {key.upper()}"
        menu_items.append(pystray.MenuItem(action_label, partial(toggle_conn, key)))
        menu_items.append(pystray.Menu.SEPARATOR)

    menu_items.append(pystray.MenuItem("Quit", quit_app))
    return pystray.Menu(*menu_items)


def setup_tray():
    global tray_icon
    if not HAS_TRAY:
        return

    image = create_tray_image()
    tray_icon = pystray.Icon("GMADavaoCaster", image, APP_NAME, build_tray_menu())
    threading.Thread(target=tray_icon.run, daemon=True).start()
    app.after(1000, update_tray_icon)


# --- Window Positioning Logic ---

def show_window_centered():
    """
    Restore the saved window size and position.

    On the first launch, when no position has been saved yet,
    the window is centered on the screen.
    """
    app.deiconify()
    app.lift()
    app.focus_force()

    window_cfg = cfg.get("window", {})

    try:
        width = int(window_cfg.get("width", 740))
        height = int(window_cfg.get("height", 870))
    except (TypeError, ValueError):
        width, height = 740, 870

    x = window_cfg.get("x")
    y = window_cfg.get("y")

    # Restore the exact saved position when available.
    if x is not None and y is not None:
        try:
            x = int(x)
            y = int(y)
            app.geometry(f"{width}x{height}+{x}+{y}")
            return
        except (TypeError, ValueError):
            pass

    # No saved position: center the window.
    screen_width = app.winfo_screenwidth()
    screen_height = app.winfo_screenheight()
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)

    app.geometry(f"{width}x{height}+{x}+{y}")


def show_window_at_tray():
    """
    Restore the existing window using its saved size and position.
    This keeps tray restore consistent with the normal application
    startup position.
    """
    app.deiconify()
    app.lift()
    app.focus_force()

    window_cfg = cfg.get("window", {})

    try:
        width = int(window_cfg.get("width", 740))
        height = int(window_cfg.get("height", 870))
    except (TypeError, ValueError):
        width, height = 740, 870

    x = window_cfg.get("x")
    y = window_cfg.get("y")

    if x is not None and y is not None:
        try:
            x = int(x)
            y = int(y)
            app.geometry(f"{width}x{height}+{x}+{y}")
            return
        except (TypeError, ValueError):
            pass

    # First launch / no saved position: center the window.
    screen_width = app.winfo_screenwidth()
    screen_height = app.winfo_screenheight()
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    app.geometry(f"{width}x{height}+{x}+{y}")


def on_window_close():
    # Save the current window size/position before hiding or closing.
    save_config()

    if tray_var.get() and HAS_TRAY:
        app.withdraw()
        log("[SYSTEM] Minimized to tray. Right-click tray icon to restore or quit.")
    else:
        force_close()


def force_close():
    for k in station_states.keys():
        save_config(k)
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


# --- Settings / Menu Actions ---
def on_autostart_toggle():
    save_config()
    enabled = autostart_var.get()
    log(f"[SYSTEM] Auto Start on Boot option set to: {enabled}")


def on_autostart_connect_toggle():
    save_config()
    enabled = autostart_connect_var.get()
    log(f"[SYSTEM] Auto Start & Connect on App Launch set to: {enabled}")


def manual_save_action():
    save_config(show_popup=True)


def ping_server_action():
    try:
        current_tab = notebook.index(notebook.select()) if 'notebook' in globals() else 0
        active_key = "am" if current_tab == 0 else "fm"
        ui = ui_elements[active_key]
        url = ui["url_entry"].get().strip()
        
        if not url:
            messagebox.showwarning("Ping Server", "Target server URL is empty.")
            return

        log(f"[{active_key.upper()}] Pinging server endpoint: {url}...")
        
        def run_ping():
            try:
                test_ws = websocket.create_connection(url, timeout=2.5)
                test_ws.close()
                app.after(0, lambda: messagebox.showinfo("Ping Success", f"Successfully reached and pinged server:\n{url}"))
                log(f"[{active_key.upper()}] Ping successful: Server reachable.")
            except Exception as e:
                app.after(0, lambda: messagebox.showerror("Ping Failed", f"Could not connect to server:\n{e}"))
                log(f"[{active_key.upper()}] Ping failed: {e}")

        threading.Thread(target=run_ping, daemon=True).start()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to initiate server ping: {e}")


def check_for_updates_action():
    log("[SYSTEM] Checking for updates...")
    messagebox.showinfo(
        "Check for Updates", 
        f"You are currently running {APP_NAME}\nVersion: {APP_VERSION}\n\nYou are on the latest test build."
    )


def show_about_action():
    about_text = (
        f"{APP_NAME}\n"
        f"Version: {APP_VERSION}\n\n"
        f"A professional broadcast audio streaming software, Developed for GMA DAVAO AM-FM radio operations.\n\n"
        f"Main Author: {APP_AUTHOR}"
    )
    messagebox.showinfo(f"About {APP_NAME}", about_text)


if not _enforce_single_instance():
    sys.exit(0)

cfg = load_config()

# Pre-load station initial device indexes from config before building UI
for key in ["am", "fm"]:
    st_cfg = cfg.get("stations", {}).get(key, {})
    station_states[key]["input_device_index"] = st_cfg.get("input_device_index", 0)
    station_states[key]["monitor_output_index"] = st_cfg.get("monitor_output_index", 0)

app = tk.Tk()
app.title(APP_NAME)

# Load saved window size/position.
window_cfg = cfg.get("window", {})
try:
    saved_width = int(window_cfg.get("width", 539))
    saved_height = int(window_cfg.get("height", 870))
except (TypeError, ValueError):
    saved_width, saved_height = 539, 870

saved_x = window_cfg.get("x")
saved_y = window_cfg.get("y")

if saved_x is not None and saved_y is not None:
    try:
        saved_x = int(saved_x)
        saved_y = int(saved_y)
        app.geometry(f"{saved_width}x{saved_height}+{saved_x}+{saved_y}")
    except (TypeError, ValueError):
        app.geometry(f"{saved_width}x{saved_height}")
else:
    app.geometry(f"{saved_width}x{saved_height}")

app.protocol("WM_DELETE_WINDOW", on_window_close)

style = ttk.Style()
style.theme_use("clam")
style.configure("Danger.TButton", foreground="white", background="#cc0000", font=("Segoe UI", 9, "bold"))
style.configure("GMA.TButton", foreground="white", background="#003366", font=("Segoe UI", 9, "bold"))

header_frame = tk.Frame(app, bg="#003366", padx=10, pady=8)
header_frame.pack(fill="x")

title_lbl = tk.Label(header_frame, text=APP_NAME.upper(), fg="white", bg="#003366", font=("Segoe UI", 11, "bold"))
title_lbl.pack(side="left")

settings_menu_btn = tk.Menubutton(header_frame, text=" ☰ Settings ", fg="white", bg="#002244", activebackground="#004488", activeforeground="white", relief="flat", font=("Segoe UI", 9, "bold"))
settings_menu_btn.pack(side="right", padx=4)

settings_dropdown = tk.Menu(settings_menu_btn, tearoff=0)
settings_menu_btn.config(menu=settings_dropdown)

tray_var = tk.BooleanVar(value=cfg.get("minimize_to_tray", True))
autostart_var = tk.BooleanVar(value=cfg.get("auto_start_boot", False))
autostart_connect_var = tk.BooleanVar(value=cfg.get("auto_start_connect", False))

settings_dropdown.add_command(label="Save Configuration", command=manual_save_action)
settings_dropdown.add_separator()
settings_dropdown.add_checkbutton(label="Minimize to Tray", variable=tray_var, command=lambda: save_config())
settings_dropdown.add_checkbutton(label="Auto Start on Boot Start Up", variable=autostart_var, command=on_autostart_toggle)
settings_dropdown.add_checkbutton(label="Auto Start & Connect on App Launch", variable=autostart_connect_var, command=on_autostart_connect_toggle)
settings_dropdown.add_separator()
settings_dropdown.add_command(label="Ping Server", command=ping_server_action)
settings_dropdown.add_command(label="Check for Updates...", command=check_for_updates_action)
settings_dropdown.add_command(label="About", command=show_about_action)
settings_dropdown.add_separator()
settings_dropdown.add_command(label="Quit", command=force_close)

# Initial query of all audio devices
try:
    all_devices_cache = sd.query_devices()
    all_host_apis_cache = sd.query_hostapis()
    try:
        default_in_idx_cache, default_out_idx_cache = sd.default.device
    except Exception:
        default_in_idx_cache, default_out_idx_cache = -1, -1
except Exception:
    all_devices_cache = []
    all_host_apis_cache = []

notebook = ttk.Notebook(app)
notebook.pack(fill="both", expand=True, padx=8, pady=4)

stations_config = cfg.get("stations", DEFAULT_CONFIG["stations"])

for key in ["am", "fm"]:
    tab = ttk.Frame(notebook, padding=6)
    notebook.add(tab, text=f" {key.upper()} Station ")
    st_cfg = stations_config.get(key, {})

    in_frame = ttk.LabelFrame(tab, text="Stream Audio Input & Encoding", padding=4)
    in_frame.pack(fill="x", pady=2)

    ttk.Label(in_frame, text="Input:").grid(row=0, column=0, sticky="w", padx=2)
    in_device_combo = ttk.Combobox(in_frame, values=[], state="readonly")
    in_device_combo.grid(row=0, column=1, columnspan=3, sticky="ew", padx=2, pady=1)
    in_device_combo.bind("<<ComboboxSelected>>", lambda e, k=key: activate_station_input(k))

    ttk.Label(in_frame, text="Fmt:").grid(row=1, column=0, sticky="w", padx=2)
    format_combo = ttk.Combobox(in_frame, state="readonly", values=["Raw PCM (s16le)"])
    format_combo.set("Raw PCM (s16le)")
    format_combo.grid(row=1, column=1, sticky="ew", padx=2, pady=1)
    format_combo.bind("<<ComboboxSelected>>", lambda e, k=key: on_format_changed(k))

    ttk.Label(in_frame, text="Bitrate:").grid(row=1, column=2, sticky="w", padx=2)
    bitrate_combo = ttk.Combobox(in_frame, state="readonly", values=["64 kbps", "96 kbps", "128 kbps", "192 kbps", "256 kbps", "320 kbps"])
    bitrate_combo.set(st_cfg.get("bitrate", "128 kbps"))
    bitrate_combo.grid(row=1, column=3, sticky="ew", padx=2, pady=1)
    bitrate_combo.bind("<<ComboboxSelected>>", lambda e, k=key: on_setting_changed(k))

    ttk.Label(in_frame, text="Rate:").grid(row=2, column=0, sticky="w", padx=2)
    sr_combo = ttk.Combobox(in_frame, state="readonly", values=["22050", "32000", "44100", "48000", "96000"])
    sr_combo.set(str(st_cfg.get("sample_rate", 44100)))
    sr_combo.grid(row=2, column=1, sticky="ew", padx=2, pady=1)
    sr_combo.bind("<<ComboboxSelected>>", lambda e, k=key: on_setting_changed(k))

    in_status = tk.Label(in_frame, text="INACTIVE", foreground="#cc0000", font=("Segoe UI", 8, "bold"))
    in_status.grid(row=2, column=2, columnspan=2, sticky="e", padx=4)
    in_frame.columnconfigure(1, weight=1)
    in_frame.columnconfigure(3, weight=1)

    out_frame = ttk.LabelFrame(tab, text="Secondary Local Monitor (Pass-through)", padding=4)
    out_frame.pack(fill="x", pady=2)

    out_device_combo = ttk.Combobox(out_frame, values=[], state="readonly")
    out_device_combo.pack(side="left", fill="x", expand=True, padx=(0, 4))
    out_device_combo.bind("<<ComboboxSelected>>", lambda e, k=key: toggle_monitoring(k))

    monitor_var = tk.BooleanVar(value=st_cfg.get("monitor_enabled", False))
    monitor_check = ttk.Checkbutton(out_frame, text="Monitor On", variable=monitor_var, command=lambda k=key: toggle_monitoring(k))
    monitor_check.pack(side="left", padx=2)

    net_frame = ttk.LabelFrame(tab, text="Network Target", padding=4)
    net_frame.pack(fill="x", pady=2)

    ttk.Label(net_frame, text="Name:").grid(row=0, column=0, sticky="w", padx=2)
    name_entry = ttk.Entry(net_frame, width=22)
    name_entry.insert(0, st_cfg.get("station_name", ""))
    name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=1)
    name_entry.bind("<KeyRelease>", lambda e, k=key: save_config(k))

    ttk.Label(net_frame, text="URL:").grid(row=0, column=2, sticky="w", padx=2)
    url_entry = ttk.Entry(net_frame, width=28)
    url_entry.insert(0, st_cfg.get("server_url", ""))
    url_entry.grid(row=0, column=3, sticky="ew", padx=2, pady=1)
    url_entry.bind("<KeyRelease>", lambda e, k=key: save_config(k))
    net_frame.columnconfigure(3, weight=1)

    dsp_frame = ttk.LabelFrame(tab, text="Audio DSP & Studio Processors (Double-click Compressor for Multiband)", padding=4)
    dsp_frame.pack(fill="x", pady=2)

    def make_slider_row(parent, row_idx, label_text, from_val, to_val, init_val, fmt_str):
        lbl = ttk.Label(parent, text=label_text)
        lbl.grid(row=row_idx, column=0, sticky="w", padx=2)
        
        scale = ttk.Scale(parent, from_=from_val, to=to_val, value=init_val, orient="horizontal")
        scale.grid(row=row_idx, column=1, sticky="ew", padx=2)
        
        val_lbl = ttk.Label(parent, text=fmt_str.format(init_val), width=14, font=("Consolas", 8))
        val_lbl.grid(row=row_idx, column=2, sticky="w", padx=2)
        
        def on_slide(v):
            val = float(v)
            if "Limiter" in label_text:
                if val >= 0.0:
                    mode_txt = "None (0 dB)"
                elif val >= -4.0:
                    mode_txt = f"Soft ({val:.1f} dB)"
                elif val >= -10.0:
                    mode_txt = f"Mid ({val:.1f} dB)"
                else:
                    mode_txt = f"High ({val:.1f} dB)"
                val_lbl.config(text=mode_txt)
            else:
                val_lbl.config(text=fmt_str.format(val))
            
        def on_slide_release(event):
            save_config(key)

        scale.config(command=on_slide)
        scale.bind("<ButtonRelease-1>", on_slide_release)
        parent.columnconfigure(1, weight=1)
        return scale, lbl

    gain_scale, _ = make_slider_row(dsp_frame, 0, "Gain (dB):", 0.0, 36.0, st_cfg.get("gain_db", 0.0), "+{:.1f} dB")
    vol_scale, _ = make_slider_row(dsp_frame, 1, "Volume (dB):", -24.0, 24.0, st_cfg.get("volume_db", 0.0), "{:+.1f} dB")
    
    gate_scale, _ = make_slider_row(dsp_frame, 2, "Gate (dB):", -60.0, -10.0, st_cfg.get("noise_gate", -40.0), "{:.1f} dB")
    gate_var = tk.BooleanVar(value=st_cfg.get("gate_enabled", True))
    gate_chk = ttk.Checkbutton(dsp_frame, text="On", variable=gate_var, command=lambda: on_setting_changed(key))
    gate_chk.grid(row=2, column=3, sticky="w", padx=2)

    comp_scale, comp_label = make_slider_row(dsp_frame, 3, "Compressor:", -36.0, 0.0, st_cfg.get("compressor_db", -12.0), "{:.1f} dB")
    comp_var = tk.BooleanVar(value=st_cfg.get("comp_enabled", True))
    comp_chk = ttk.Checkbutton(dsp_frame, text="On", variable=comp_var, command=lambda: on_setting_changed(key))
    comp_chk.grid(row=3, column=3, sticky="w", padx=2)
    
    comp_label.bind("<Double-Button-1>", lambda e, k=key: open_multiband_modal(k))
    comp_scale.bind("<Double-Button-1>", lambda e, k=key: open_multiband_modal(k))

    lim_scale, _ = make_slider_row(dsp_frame, 4, "Limiter Mode:", -18.0, 0.0, st_cfg.get("limiter_db", 0.0), "{:.1f} dB")
    lim_var = tk.BooleanVar(value=st_cfg.get("lim_enabled", True))
    lim_chk = ttk.Checkbutton(dsp_frame, text="On", variable=lim_var, command=lambda: on_setting_changed(key))
    lim_chk.grid(row=4, column=3, sticky="w", padx=2)

    ttk.Label(dsp_frame, text="Channel:").grid(row=5, column=0, sticky="w", padx=2)
    mode_combo = ttk.Combobox(dsp_frame, state="readonly", values=[
        "Stereo", "Mono (Downmix L+R)", "Left Channel Only", "Right Channel Only"
    ])
    mode_combo.set(st_cfg.get("channel_mode", "Stereo"))
    mode_combo.grid(row=5, column=1, sticky="ew", padx=2, pady=1)
    mode_combo.bind("<<ComboboxSelected>>", lambda e, k=key: on_setting_changed(k))

    hpf_var = tk.BooleanVar(value=st_cfg.get("hpf_enabled", False))
    hpf_check = ttk.Checkbutton(dsp_frame, text="HPF Rumble Cut", variable=hpf_var, command=lambda: on_setting_changed(key))
    hpf_check.grid(row=6, column=0, sticky="w", padx=2, pady=1)

    agc_var = tk.BooleanVar(value=st_cfg.get("agc_enabled", True))
    agc_check = ttk.Checkbutton(dsp_frame, text="AGC (Auto Gain)", variable=agc_var, command=lambda: on_setting_changed(key))
    agc_check.grid(row=6, column=1, sticky="w", padx=2, pady=1)

    meter_frame = ttk.LabelFrame(tab, text="Live VU Meters (Safe | Peak | Clip)", padding=2)
    meter_frame.pack(fill="x", pady=2)
    left_meter = tk.Canvas(meter_frame, height=16, background="#121212", highlightthickness=0)
    left_meter.pack(fill="x", pady=1)
    right_meter = tk.Canvas(meter_frame, height=16, background="#121212", highlightthickness=0)
    right_meter.pack(fill="x", pady=1)

    action_frame = ttk.Frame(tab, padding=2)
    action_frame.pack(fill="x", pady=2)

    conn_label = tk.Label(action_frame, text="OFFLINE", foreground="#cc0000", font=("Segoe UI", 8, "bold"))
    conn_label.pack(side="right", padx=4)

    start_btn = ttk.Button(action_frame, text="START BROADCAST", style="GMA.TButton", command=lambda k=key: handle_start_stop_button(k))
    start_btn.pack(side="left", padx=1)

    status_label = tk.Label(action_frame, text="STANDBY", font=("Segoe UI", 8, "bold"))
    status_label.pack(side="left", padx=6)

    ui_elements[key] = {
        "in_device_combo": in_device_combo,
        "format_combo": format_combo,
        "bitrate_combo": bitrate_combo,
        "sr_combo": sr_combo,
        "in_status": in_status,
        "out_device_combo": out_device_combo,
        "monitor_var": monitor_var,
        "name_entry": name_entry,
        "url_entry": url_entry,
        "gain_scale": gain_scale,
        "vol_scale": vol_scale,
        "gate_scale": gate_scale,
        "gate_var": gate_var,
        "comp_scale": comp_scale,
        "comp_var": comp_var,
        "lim_scale": lim_scale,
        "lim_var": lim_var,
        "hpf_var": hpf_var,
        "mode_combo": mode_combo,
        "agc_var": agc_var,
        "multiband_data": st_cfg.get("multiband", DEFAULT_CONFIG["stations"]["am"]["multiband"]),
        "left_meter": left_meter,
        "right_meter": right_meter,
        "conn_label": conn_label,
        "start_btn": start_btn,
        "status_label": status_label
    }

# Populate initial device lists across both tabs
refresh_all_device_dropdowns()

for key in ["am", "fm"]:
    if all_devices_cache:
        app.after(200, lambda k=key: activate_station_input(k))

log_frame = ttk.LabelFrame(app, text="SYSTEM EVENT LOG", padding=4)
log_frame.pack(fill="both", expand=True, padx=8, pady=(2, 2))
log_box = tk.Text(log_frame, height=3, font=("Consolas", 8))
log_box.pack(fill="both", expand=True)

footer_frame = tk.Frame(app, bg="#eef2f5", padx=6, pady=4)
footer_frame.pack(fill="x", side="bottom")
footer_lbl = tk.Label(footer_frame, text=f"Main Author: {APP_AUTHOR}", fg="#444444", bg="#eef2f5", font=("Segoe UI", 8, "italic"))
footer_lbl.pack(side="right")

update_vu_meters()
if HAS_TRAY:
    setup_tray()

# Initialize main window using saved geometry (or center on first launch).
app.after(100, show_window_centered)

if cfg.get("auto_start_connect", False):
    app.after(1500, lambda: [start_transmitter("am"), start_transmitter("fm")])

app.mainloop()