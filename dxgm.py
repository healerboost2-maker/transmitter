import os
import sys
import json
import time
import math
import struct
import queue
import threading
import winreg as reg
import tkinter as tk
from tkinter import ttk, messagebox

import sounddevice as sd
import websocket
import pystray
from PIL import Image, ImageDraw
from pystray import MenuItem as item

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG = {
    "input_device_index": 0,
    "server_url": "wss://transmitter-zctz.onrender.com/tx",
    "station_name": "Main Radio Feed",
    "listen_enabled": False
}

BLOCK_SIZE = 960
BYTES_PER_SAMPLE = 2
MIN_DB = -60.0
MAX_DB = 0.0

app = None
ws = None

connected = False
connecting = False
transmitting = False
device_active = False
should_run = True

audio_stream = None
monitor_stream = None
tray_icon = None

hw_sample_rate = 44100
hw_channels = 2

transmit_queue = queue.Queue(maxsize=200)
monitor_queue = queue.Queue(maxsize=200)

left_level = MIN_DB
right_level = MIN_DB

state_lock = threading.RLock()
ws_lock = threading.RLock()


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"Error loading config: {e}")
    return cfg


def save_config():
    if app is None:
        return
    
    config_data = {
        "input_device_index": device_combo.current() if 'device_combo' in globals() and device_combo.get() else cfg.get("input_device_index", 0),
        "server_url": server_entry.get().strip() if 'server_entry' in globals() else cfg["server_url"],
        "station_name": stream_name.get().strip() if 'stream_name' in globals() else cfg["station_name"],
        "listen_enabled": listen_var.get() if 'listen_var' in globals() else cfg["listen_enabled"]
    }
    
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        log(f"Failed to save config: {e}")


def add_to_startup():
    try:
        app_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, key_path, 0, reg.KEY_SET_VALUE)
        reg.SetValueEx(key, "AudioBridgeTransmitter", 0, reg.REG_SZ, f'"{app_path}"')
        reg.CloseKey(key)
    except Exception as e:
        print(f"Failed to add to startup: {e}")


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


def update_connection_ui():
    def update():
        with state_lock:
            if connected:
                connection_label.config(text="ONLINE", foreground="green")
                connect_button.config(text="DISCONNECT", state="normal")
            elif connecting:
                connection_label.config(text="CONNECTING...", foreground="#cc8800")
                connect_button.config(text="CONNECTING...", state="disabled")
            else:
                connection_label.config(text="OFFLINE", foreground="red")
                connect_button.config(text="CONNECT", state="normal")
    if app is not None:
        app.after(0, update)


def set_transmission_ui(active):
    def update():
        try:
            if active:
                start_button.config(state="disabled")
                stop_button.config(state="normal")
                status_label.config(text="TRANSMITTING", foreground="green")
            else:
                start_button.config(state="normal")
                stop_button.config(state="disabled")
                status_label.config(text="NOT TRANSMITTING", foreground="black")
        except Exception:
            pass
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


def audio_callback(indata, frames, time_info, status):
    global left_level, right_level

    raw_bytes = indata.tobytes()
    left_db, right_db = calculate_levels(raw_bytes)

    with state_lock:
        left_level = left_db
        right_level = right_db
        is_transmitting = transmitting

    if is_transmitting:
        if transmit_queue.full():
            try: transmit_queue.get_nowait()
            except queue.Empty: pass
        transmit_queue.put_nowait(raw_bytes)

    if listen_var.get():
        if monitor_queue.full():
            try: monitor_queue.get_nowait()
            except queue.Empty: pass
        monitor_queue.put_nowait(raw_bytes)


def monitor_callback(outdata, frames, time_info, status):
    req_bytes = frames * hw_channels * BYTES_PER_SAMPLE
    try:
        data = monitor_queue.get_nowait()
    except queue.Empty:
        data = b"\x00" * req_bytes

    if len(data) >= req_bytes:
        outdata[:] = data[:req_bytes]
    else:
        outdata[:] = b"\x00" * req_bytes


def refresh_devices():
    try:
        devices = sd.query_devices()
        input_devs = [f"{i}: {d['name']}" for i, d in enumerate(devices) if d["max_input_channels"] >= 1]
        device_combo["values"] = input_devs
        
        saved_idx = cfg.get("input_device_index", 0)
        if input_devs:
            if saved_idx < len(input_devs):
                device_combo.current(saved_idx)
            else:
                device_combo.current(0)
            app.after(200, activate_selected_device)
        else:
            device_status_label.config(text="NO INPUT DEVICE", foreground="red")
    except Exception as e:
        log(f"Device refresh error: {e}")


def activate_selected_device(event=None):
    global audio_stream, device_active, hw_sample_rate, hw_channels
    selected = device_combo.get()
    if not selected:
        return

    try:
        dev_idx = int(selected.split(":")[0])
        close_input_device()

        dev_info = sd.query_devices(dev_idx)
        hw_sample_rate = int(dev_info.get("default_samplerate", 44100))
        hw_channels = min(2, int(dev_info.get("max_input_channels", 2)))

        audio_stream = sd.InputStream(
            samplerate=hw_sample_rate,
            blocksize=BLOCK_SIZE,
            device=dev_idx,
            channels=hw_channels,
            dtype="int16",
            callback=audio_callback
        )
        audio_stream.start()

        device_active = True
        device_status_label.config(text="DEVICE ACTIVE", foreground="green")
        log(f"Native Input Active: {selected} ({hw_sample_rate}Hz/{hw_channels}Ch)")
        toggle_listen()
        save_config()
    except Exception as e:
        device_active = False
        device_status_label.config(text="DEVICE ERROR", foreground="red")
        log(f"Device error: {e}")


def close_input_device():
    global audio_stream, device_active
    device_active = False
    if audio_stream:
        try:
            audio_stream.stop()
            audio_stream.close()
        except Exception:
            pass
    audio_stream = None


def toggle_listen():
    global monitor_stream
    if listen_var.get() and device_active:
        if not monitor_stream:
            try:
                monitor_stream = sd.RawOutputStream(
                    samplerate=hw_sample_rate,
                    blocksize=BLOCK_SIZE,
                    channels=hw_channels,
                    dtype="int16",
                    callback=monitor_callback
                )
                monitor_stream.start()
                log("Local monitoring active.")
            except Exception as e:
                log(f"Monitor stream error: {e}")
    else:
        if monitor_stream:
            try:
                monitor_stream.stop()
                monitor_stream.close()
            except Exception:
                pass
            monitor_stream = None
            log("Local monitoring disabled.")


def toggle_connection():
    with state_lock:
        if connected:
            disconnect_server()
        else:
            connect_server()


def connect_server():
    global connecting
    with state_lock:
        if connected or connecting:
            return
        connecting = True

    update_connection_ui()
    server_url = server_entry.get().strip()
    threading.Thread(target=connection_watchdog, args=(server_url,), daemon=True).start()


def disconnect_server():
    global ws, connected, connecting
    stop_transmitter()
    with ws_lock:
        if ws:
            try: ws.close()
            except Exception: pass
            ws = None

    with state_lock:
        connected = False
        connecting = False

    update_connection_ui()
    log("Disconnected from server.")


def connection_watchdog(server_url):
    global ws, connected, connecting, should_run
    while should_run:
        with state_lock:
            if not connected and not connecting and not transmitting:
                # If purposely disconnected, break loop
                break
        
        try:
            with state_lock:
                connecting = True
            update_connection_ui()
            
            new_ws = websocket.create_connection(server_url, timeout=10)
            reg_payload = {
                "type": "register-transmitter",
                "station": stream_name.get().strip() or "Main Radio Feed",
                "sampleRate": hw_sample_rate,
                "channels": hw_channels,
                "format": "pcm_s16le"
            }
            new_ws.send(json.dumps(reg_payload))

            with ws_lock:
                ws = new_ws
            with state_lock:
                connected = True
                connecting = False

            update_connection_ui()
            log("Connected to server.")
            
            # Auto-start transmission if device is active
            if device_active and not transmitting:
                app.after(100, start_transmitter)

            # Block inside recv loop until connection fails
            new_ws.settimeout(5.0)
            while should_run:
                try:
                    msg = new_ws.recv()
                    if msg is None:
                        break
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
        except Exception as e:
            log(f"Connection dropped/failed: {e}")
        
        with ws_lock:
            if ws:
                try: ws.close()
                except Exception: pass
                ws = None

        with state_lock:
            connected = False
            connecting = False
        update_connection_ui()
        
        log("Attempting reconnection in 5 seconds...")
        time.sleep(5)


def sender_thread():
    global transmitting
    while True:
        # Check if we should be transmitting
        with state_lock:
            curr_transmitting = transmitting
            curr_connected = connected

        if not curr_transmitting or not curr_connected:
            time.sleep(0.5)
            continue

        try:
            raw_audio = transmit_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        with ws_lock:
            curr_ws = ws

        if curr_ws and curr_connected:
            try:
                curr_ws.send_binary(raw_audio)
            except Exception as e:
                log(f"Transmission Send Error: {e}")
                # Don't kill the global 'transmitting' state here. 
                # Just break this send loop; the watchdog will reconnect and auto-resume.
                break


def connection_watchdog(server_url):
    global ws, connected, connecting, should_run
    while should_run:
        with state_lock:
            # Only run if not already connected
            if connected:
                time.sleep(1)
                continue
        
        try:
            with state_lock:
                connecting = True
            update_connection_ui()
            
            new_ws = websocket.create_connection(server_url, timeout=10)
            reg_payload = {
                "type": "register-transmitter",
                "station": stream_name.get().strip() or "Main Radio Feed",
                "sampleRate": hw_sample_rate,
                "channels": hw_channels,
                "format": "pcm_s16le"
            }
            new_ws.send(json.dumps(reg_payload))

            with ws_lock:
                ws = new_ws
            with state_lock:
                connected = True
                connecting = False

            update_connection_ui()
            log("Connected to server.")
            
            # --- AUTO-RESUME LOGIC ---
            # If the device is active, automatically ensure transmission is active or restarted
            if device_active:
                with state_lock:
                    if not transmitting:
                        transmitting = True
                set_transmission_ui(True)
                log("Transmission automatically resumed.")

            # Block inside recv loop until connection fails/times out
            new_ws.settimeout(5.0)
            while should_run:
                try:
                    msg = new_ws.recv()
                    if msg is None:
                        break
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
        except Exception as e:
            log(f"Connection dropped/failed: {e}")
        
        with ws_lock:
            if ws:
                try: ws.close()
                except Exception: pass
                ws = None

        with state_lock:
            connected = False
            connecting = False
        update_connection_ui()
        
        log("Attempting reconnection in 3 seconds...")
        time.sleep(3)

def start_transmitter():
    global transmitting
    if not device_active or not connected:
        return

    while not transmit_queue.empty():
        try: transmit_queue.get_nowait()
        except queue.Empty: break

    save_config()
    with state_lock:
        transmitting = True

    threading.Thread(target=sender_thread, daemon=True).start()
    set_transmission_ui(True)
    log("Transmission started.")


def stop_transmitter():
    global transmitting
    with state_lock:
        transmitting = False
    set_transmission_ui(False)
    log("Transmission stopped.")


def draw_vu_meter(canvas, level_db, channel_label):
    canvas.delete("all")
    width = canvas.winfo_width() or 720
    height = canvas.winfo_height() or 28

    norm = max(0.0, min(1.0, (level_db - MIN_DB) / (MAX_DB - MIN_DB)))
    active_w = max(0, (width - 8) * norm)

    canvas.create_rectangle(4, 3, width - 4, height - 3, fill="#121212", outline="#333333")
    if active_w > 0:
        bar_color = "#00e676" if level_db < -6 else ("#ffeb3b" if level_db < -1 else "#ff1744")
        canvas.create_rectangle(4, 4, 4 + active_w, height - 4, fill=bar_color, outline="")

    canvas.create_text(10, height // 2, anchor="w", text=channel_label, fill="#ffffff", font=("Segoe UI", 9, "bold"))
    canvas.create_text(width - 10, height // 2, anchor="e", text=f"{level_db:5.1f} dBFS", fill="#ffffff", font=("Consolas", 9, "bold"))


def update_vu_meters():
    if app is not None:
        with state_lock:
            draw_vu_meter(left_meter, left_level, "L")
            draw_vu_meter(right_meter, right_level, "R")
        app.after(50, update_vu_meters)


# --- System Tray Integration ---
def create_tray_image():
    image = Image.new('RGB', (64, 64), color="#1b2229")
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill="#4ade80")
    return image


def show_window(icon, item):
    app.after(0, app.deiconify)


def quit_app(icon=None, item=None):
    global should_run
    should_run = False
    save_config()
    toggle_listen()
    close_input_device()
    disconnect_server()
    if tray_icon:
        tray_icon.stop()
    if app:
        app.after(0, app.destroy)


def on_close():
    # Hide window to tray instead of closing
    app.withdraw()


def setup_tray():
    global tray_icon
    menu = (
        item('Open UI', show_window),
        item('Exit', quit_app)
    )
    tray_icon = pystray.Icon("AudioBridgeTransmitter", create_tray_image(), "AudioBridge Transmitter", menu)
    tray_icon.run()


cfg = load_config()
add_to_startup()

app = tk.Tk()
app.title("AudioBridge Transmitter")
app.geometry("760x550")
app.protocol("WM_DELETE_WINDOW", on_close)

header = ttk.Frame(app, padding=(10, 8))
header.pack(fill="x")
ttk.Label(header, text="AUDIOBRIDGE TRANSMITTER", font=("Segoe UI", 14, "bold")).pack(side="left")
connection_label = tk.Label(header, text="OFFLINE", foreground="red", font=("Segoe UI", 9, "bold"))
connection_label.pack(side="right")

device_frame = ttk.LabelFrame(app, text="INPUT DEVICE & LOCAL MONITOR", padding=8)
device_frame.pack(fill="x", padx=10, pady=4)
device_combo = ttk.Combobox(device_frame, state="readonly")
device_combo.pack(side="left", fill="x", expand=True)
device_combo.bind("<<ComboboxSelected>>", activate_selected_device)

listen_var = tk.BooleanVar(value=cfg["listen_enabled"])
listen_check = ttk.Checkbutton(device_frame, text="LISTEN (Local Monitor)", variable=listen_var, command=toggle_listen)
listen_check.pack(side="left", padx=10)

device_status_label = tk.Label(device_frame, text="DEVICE INACTIVE", foreground="red", font=("Segoe UI", 8, "bold"))
device_status_label.pack(side="left")

meter_frame = ttk.LabelFrame(app, text="AUDIO LEVEL — dBFS", padding=8)
meter_frame.pack(fill="x", padx=10, pady=4)
left_meter = tk.Canvas(meter_frame, height=28, background="#121212", highlightthickness=0)
left_meter.pack(fill="x", pady=2)
right_meter = tk.Canvas(meter_frame, height=28, background="#121212", highlightthickness=0)
right_meter.pack(fill="x", pady=2)

net_frame = ttk.Frame(app)
net_frame.pack(fill="x", padx=10, pady=4)

stream_sub = ttk.LabelFrame(net_frame, text="STREAM NAME", padding=8)
stream_sub.pack(side="left", fill="both", expand=True, padx=(0, 5))
stream_name = ttk.Entry(stream_sub)
stream_name.insert(0, cfg["station_name"])
stream_name.pack(fill="x")

server_sub = ttk.LabelFrame(net_frame, text="SERVER URL", padding=8)
server_sub.pack(side="left", fill="both", expand=True, padx=(5, 0))
server_entry = ttk.Entry(server_sub)
server_entry.insert(0, cfg["server_url"])
server_entry.pack(fill="x")

control_frame = ttk.Frame(app, padding=(10, 4))
control_frame.pack(fill="x")
connect_button = ttk.Button(control_frame, text="CONNECT", command=toggle_connection)
connect_button.pack(side="left", padx=(0, 5))
start_button = ttk.Button(control_frame, text="START", command=start_transmitter)
start_button.pack(side="left", padx=5)
stop_button = ttk.Button(control_frame, text="STOP", command=stop_transmitter, state="disabled")
stop_button.pack(side="left", padx=5)

quit_button = ttk.Button(control_frame, text="QUIT / EXIT", command=quit_app)
quit_button.pack(side="left", padx=(15, 0))

status_label = tk.Label(control_frame, text="NOT TRANSMITTING", font=("Segoe UI", 9, "bold"))
status_label.pack(side="right")

log_frame = ttk.LabelFrame(app, text="EVENT LOG", padding=6)
log_frame.pack(fill="both", expand=True, padx=10, pady=(2, 8))
log_box = tk.Text(log_frame, height=5, font=("Consolas", 9))
log_box.pack(fill="both", expand=True)

# Start system tray thread
threading.Thread(target=setup_tray, daemon=True).start()

refresh_devices()
update_vu_meters()

# Auto-connect on startup
app.after(1000, connect_server)

app.mainloop()