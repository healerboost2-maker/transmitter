import tkinter as tk
from tkinter import ttk, messagebox
import sounddevice as sd
import websocket
import threading
import queue
import json
import time
import math
import struct

# =========================================================
# AUDIOBRIDGE TRANSMITTER
# Fixed version
# =========================================================

DEFAULT_SERVER_URL = "wss://transmitter-zctz.onrender.com/audio"

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 960                 # 20 ms at 48 kHz
BYTES_PER_SAMPLE = 2

TRANSMIT_QUEUE_SIZE = 40
MONITOR_QUEUE_SIZE = 40

MIN_DB = -60.0
MAX_DB = 0.0

CONNECT_TIMEOUT = 60
STATUS_TIMEOUT = 10
HEARTBEAT_SECONDS = 20

# =========================================================
# GLOBAL STATE
# =========================================================

app = None
ws = None

connected = False
connecting = False
transmitting = False
listen_enabled = False
device_active = False

audio_stream = None
monitor_stream = None
selected_device_index = None

transmit_queue = queue.Queue(maxsize=TRANSMIT_QUEUE_SIZE)
monitor_queue = queue.Queue(maxsize=MONITOR_QUEUE_SIZE)

left_level = MIN_DB
right_level = MIN_DB

state_lock = threading.RLock()
ws_lock = threading.RLock()

# =========================================================
# LOGGING
# =========================================================

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
        if log_box.winfo_exists():
            log_box.insert("end", text)
            log_box.see("end")
    except Exception:
        pass


# =========================================================
# UI HELPERS
# =========================================================

def set_connection_ui(text, color, button_text=None, button_state=None):
    def update():
        try:
            connection_label.config(text=text, foreground=color)

            if button_text is not None:
                connect_button.config(text=button_text)

            if button_state is not None:
                connect_button.config(state=button_state)
        except Exception:
            pass

    try:
        app.after(0, update)
    except Exception:
        pass


def set_transmission_ui(active):
    def update():
        try:
            if active:
                start_button.config(state="disabled")
                stop_button.config(state="normal")
                status_label.config(
                    text="TRANSMITTING",
                    foreground="green"
                )
            else:
                start_button.config(state="normal")
                stop_button.config(state="disabled")
                status_label.config(
                    text="NOT TRANSMITTING",
                    foreground="black"
                )
        except Exception:
            pass

    try:
        app.after(0, update)
    except Exception:
        pass


# =========================================================
# QUEUE UTILITIES
# =========================================================

def clear_queue(target_queue):
    while True:
        try:
            target_queue.get_nowait()
        except queue.Empty:
            break


# =========================================================
# AUDIO LEVEL CALCULATION
# =========================================================

def calculate_levels(audio_bytes):
    if not audio_bytes:
        return MIN_DB, MIN_DB

    try:
        sample_count = len(audio_bytes) // 2

        if sample_count < 2:
            return MIN_DB, MIN_DB

        samples = struct.unpack(
            "<" + ("h" * sample_count),
            audio_bytes
        )

        left_samples = samples[0::2]
        right_samples = samples[1::2]

        if not left_samples or not right_samples:
            return MIN_DB, MIN_DB

        left_sum = sum(sample * sample for sample in left_samples)
        right_sum = sum(sample * sample for sample in right_samples)

        left_rms = math.sqrt(left_sum / len(left_samples))
        right_rms = math.sqrt(right_sum / len(right_samples))

        left_amplitude = max(left_rms / 32768.0, 0.000001)
        right_amplitude = max(right_rms / 32768.0, 0.000001)

        left_db = 20.0 * math.log10(left_amplitude)
        right_db = 20.0 * math.log10(right_amplitude)

        left_db = max(MIN_DB, min(MAX_DB, left_db))
        right_db = max(MIN_DB, min(MAX_DB, right_db))

        return left_db, right_db

    except Exception:
        return MIN_DB, MIN_DB


# =========================================================
# AUDIO INPUT CALLBACK
# =========================================================

def audio_callback(indata, frames, time_info, status):
    global left_level, right_level

    if status:
        print("Input status:", status)

    audio_bytes = bytes(indata)

    left_db, right_db = calculate_levels(audio_bytes)

    with state_lock:
        left_level = left_db
        right_level = right_db
        current_transmitting = transmitting
        current_listening = listen_enabled

    if current_transmitting:
        try:
            transmit_queue.put_nowait(audio_bytes)
        except queue.Full:
            # Drop the oldest packet to keep latency low.
            try:
                transmit_queue.get_nowait()
                transmit_queue.put_nowait(audio_bytes)
            except queue.Empty:
                pass
            except queue.Full:
                pass

    if current_listening:
        try:
            monitor_queue.put_nowait(audio_bytes)
        except queue.Full:
            try:
                monitor_queue.get_nowait()
                monitor_queue.put_nowait(audio_bytes)
            except Exception:
                pass


# =========================================================
# LOCAL MONITOR OUTPUT
# =========================================================

def monitor_callback(outdata, frames, time_info, status):
    if status:
        print("Output status:", status)

    required_bytes = frames * CHANNELS * BYTES_PER_SAMPLE

    try:
        audio_bytes = monitor_queue.get_nowait()
    except queue.Empty:
        audio_bytes = b""

    if len(audio_bytes) >= required_bytes:
        outdata[:] = audio_bytes[:required_bytes]
    else:
        outdata[:] = b"\x00" * required_bytes


# =========================================================
# DEVICE MANAGEMENT
# =========================================================

def get_input_devices():
    devices = sd.query_devices()
    result = []

    for index, device in enumerate(devices):
        if device["max_input_channels"] >= CHANNELS:
            result.append(f"{index}: {device['name']}")

    return result


def refresh_devices():
    try:
        devices = get_input_devices()
        device_combo["values"] = devices

        if not devices:
            device_status_label.config(
                text="NO INPUT DEVICE",
                foreground="red"
            )
            log("No compatible stereo input devices found.")
            return

        log(f"{len(devices)} input device(s) detected.")

        current_value = device_combo.get()

        if current_value not in devices:
            device_combo.current(0)

        app.after(200, activate_selected_device)

    except Exception as error:
        device_status_label.config(
            text="DEVICE ERROR",
            foreground="red"
        )
        log(f"Device scan error: {error}")


def activate_selected_device(event=None):
    global audio_stream
    global device_active
    global selected_device_index

    selected_device = device_combo.get()

    if not selected_device:
        return

    try:
        device_index = int(selected_device.split(":")[0])

        if (
            device_active
            and selected_device_index == device_index
            and audio_stream is not None
        ):
            return

        close_input_device()

        clear_queue(transmit_queue)
        clear_queue(monitor_queue)

        log(f"Opening input device: {selected_device}")

        audio_stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            device=device_index,
            channels=CHANNELS,
            dtype="int16",
            callback=audio_callback
        )

        audio_stream.start()

        selected_device_index = device_index
        device_active = True

        device_status_label.config(
            text="DEVICE ACTIVE",
            foreground="green"
        )

        log("Input device activated.")
        log("VU meter is monitoring the input.")

    except Exception as error:
        device_active = False
        selected_device_index = None
        audio_stream = None

        device_status_label.config(
            text="DEVICE ERROR",
            foreground="red"
        )

        log(f"Input device activation error: {error}")

        messagebox.showerror(
            "Input Device Error",
            str(error)
        )


def close_input_device():
    global audio_stream
    global device_active
    global selected_device_index

    device_active = False
    selected_device_index = None

    if audio_stream is not None:
        try:
            audio_stream.stop()
            audio_stream.close()
        except Exception:
            pass

    audio_stream = None

    if app is not None:
        try:
            device_status_label.config(
                text="DEVICE INACTIVE",
                foreground="red"
            )
        except Exception:
            pass


# =========================================================
# LOCAL LISTENING
# =========================================================

def start_monitor_output():
    global monitor_stream

    if monitor_stream is not None:
        return

    try:
        monitor_stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=CHANNELS,
            dtype="int16",
            callback=monitor_callback
        )

        monitor_stream.start()
        log("Local audio monitor started.")

    except Exception as error:
        monitor_stream = None
        log(f"Local monitor error: {error}")

        messagebox.showerror(
            "Monitor Error",
            str(error)
        )


def stop_monitor_output():
    global monitor_stream

    if monitor_stream is not None:
        try:
            monitor_stream.stop()
            monitor_stream.close()
        except Exception:
            pass

    monitor_stream = None
    clear_queue(monitor_queue)


def toggle_listen():
    global listen_enabled

    listen_enabled = listen_var.get()
    clear_queue(monitor_queue)

    if listen_enabled:
        start_monitor_output()
        listen_button.config(text="LISTEN: ON")
        log("Local monitoring enabled.")
    else:
        stop_monitor_output()
        listen_button.config(text="LISTEN: OFF")
        log("Local monitoring disabled.")


# =========================================================
# WEBSOCKET CONNECTION
# =========================================================

def build_registration():
    return {
        "type": "register",
        "role": "transmitter",
        "station": stream_name.get().strip() or "Main Radio Feed",
        "sampleRate": SAMPLE_RATE,
        "channels": CHANNELS,
        "format": "pcm_s16le"
    }


def connect_server():
    """
    Starts the connection in a background thread so the Tkinter
    interface never freezes while Render wakes up.
    """
    global connecting

    with state_lock:
        if connected or connecting:
            return

        connecting = True

    server_url = server_entry.get().strip()

    if not server_url:
        with state_lock:
            connecting = False

        messagebox.showwarning(
            "Server Address",
            "Please enter the server address."
        )
        return

    set_connection_ui(
        "CONNECTING...",
        "#cc8800",
        "CONNECTING...",
        "disabled"
    )

    log(f"Connecting to {server_url}...")
    log(f"Connection timeout: {CONNECT_TIMEOUT} seconds.")

    threading.Thread(
        target=connection_worker,
        args=(server_url,),
        daemon=True
    ).start()


def connection_worker(server_url):
    global ws
    global connected
    global connecting

    new_ws = None

    try:
        new_ws = websocket.create_connection(
            server_url,
            timeout=CONNECT_TIMEOUT,
            origin=None,
            enable_multithread=True
        )

        log("WebSocket TCP/TLS connection established.")

        new_ws.settimeout(STATUS_TIMEOUT)

        registration = build_registration()

        new_ws.send(json.dumps(registration))
        log("Transmitter registration sent.")

        # The server sends a status message immediately after connection.
        # We wait briefly for it, but lack of a status response does not
        # automatically mean the WebSocket failed.
        try:
            response = new_ws.recv()

            if isinstance(response, bytes):
                log(f"Server sent binary data: {len(response)} bytes")
            else:
                log(f"Server response: {response}")

        except websocket.WebSocketTimeoutException:
            log("No immediate status response; keeping WebSocket open.")

        new_ws.settimeout(None)

        with ws_lock:
            ws = new_ws

        with state_lock:
            connected = True
            connecting = False

        set_connection_ui(
            "ONLINE",
            "green",
            "CONNECTED",
            "disabled"
        )

        log("CONNECTED TO AUDIOBRIDGE SERVER.")

        # Start the receive/heartbeat worker.
        threading.Thread(
            target=websocket_receive_worker,
            args=(new_ws,),
            daemon=True
        ).start()

        threading.Thread(
            target=heartbeat_worker,
            args=(new_ws,),
            daemon=True
        ).start()

    except Exception as error:
        try:
            if new_ws is not None:
                new_ws.close()
        except Exception:
            pass

        with ws_lock:
            if ws is new_ws:
                ws = None

        with state_lock:
            connected = False
            connecting = False
            transmitting = False

        set_connection_ui(
            "OFFLINE",
            "red",
            "CONNECT",
            "normal"
        )

        set_transmission_ui(False)

        log(f"Connection failed: {error}")

        try:
            app.after(
                0,
                lambda e=str(error): messagebox.showerror(
                    "Connection Error",
                    "Could not connect to AudioBridge server:\n\n" + e
                )
            )
        except Exception:
            pass


def websocket_receive_worker(socket):
    global connected
    global ws
    global transmitting

    while True:
        try:
            message = socket.recv()

            if message is None:
                raise ConnectionError("Server closed the WebSocket.")

            if isinstance(message, bytes):
                log(f"Received binary packet from server: {len(message)} bytes")
                continue

            try:
                data = json.loads(message)
                message_type = data.get("type", "")

                if message_type in ("status", "server-status"):
                    log(
                        f"Server status: "
                        f"{data.get('status', data.get('message', 'unknown'))} | "
                        f"TX={data.get('transmitters', '?')} "
                        f"RX={data.get('receivers', '?')}"
                    )

                elif message_type == "pong":
                    pass

                else:
                    log(f"Server message: {message}")

            except json.JSONDecodeError:
                log(f"Server text: {message}")

        except Exception as error:
            # Ignore an intentional close.
            with ws_lock:
                same_socket = (ws is socket)

            if same_socket:
                with state_lock:
                    connected = False
                    transmitting = False

                with ws_lock:
                    if ws is socket:
                        ws = None

                set_connection_ui(
                    "OFFLINE",
                    "red",
                    "CONNECT",
                    "normal"
                )
                set_transmission_ui(False)

                log(f"WebSocket disconnected: {error}")

            break


def heartbeat_worker(socket):
    while True:
        time.sleep(HEARTBEAT_SECONDS)

        with state_lock:
            still_connected = connected

        with ws_lock:
            same_socket = (ws is socket)

        if not still_connected or not same_socket:
            break

        try:
            socket.send(
                json.dumps({
                    "type": "ping",
                    "timestamp": int(time.time() * 1000)
                })
            )
        except Exception as error:
            log(f"Heartbeat failed: {error}")
            break


def disconnect_server():
    global ws
    global connected
    global connecting
    global transmitting

    with state_lock:
        transmitting = False
        connected = False
        connecting = False

    with ws_lock:
        current_ws = ws
        ws = None

    if current_ws is not None:
        try:
            current_ws.close()
        except Exception:
            pass

    clear_queue(transmit_queue)

    set_connection_ui(
        "OFFLINE",
        "red",
        "CONNECT",
        "normal"
    )

    set_transmission_ui(False)

    log("Disconnected from AudioBridge server.")


# =========================================================
# TRANSMISSION
# =========================================================

def sender_thread():
    global transmitting
    global connected

    log("Audio sender thread started.")

    packet_count = 0
    byte_count = 0
    last_report = time.time()

    while True:
        with state_lock:
            active = transmitting
            online = connected

        if not active:
            break

        try:
            audio_bytes = transmit_queue.get(timeout=1)
        except queue.Empty:
            continue

        if not online:
            continue

        with ws_lock:
            current_ws = ws

        if current_ws is None:
            continue

        try:
            current_ws.send_binary(audio_bytes)

            packet_count += 1
            byte_count += len(audio_bytes)

            now = time.time()

            if now - last_report >= 5:
                log(
                    f"Audio transmitting: "
                    f"{packet_count} packets / "
                    f"{byte_count:,} bytes total"
                )
                last_report = now

        except Exception as error:
            log(f"Transmission error: {error}")

            with state_lock:
                transmitting = False
                connected = False

            with ws_lock:
                if ws is current_ws:
                    ws = None

            try:
                current_ws.close()
            except Exception:
                pass

            set_connection_ui(
                "OFFLINE",
                "red",
                "CONNECT",
                "normal"
            )

            set_transmission_ui(False)
            break

    log("Audio sender thread stopped.")


def start_transmitter():
    global transmitting

    with state_lock:
        if transmitting:
            return
        online = connected

    if not device_active:
        messagebox.showwarning(
            "Input Device",
            "Please select an active input device first."
        )
        return

    if not online:
        messagebox.showwarning(
            "AudioBridge",
            "Please CONNECT to the AudioBridge server first."
        )
        return

    clear_queue(transmit_queue)

    with state_lock:
        transmitting = True

    threading.Thread(
        target=sender_thread,
        daemon=True
    ).start()

    set_transmission_ui(True)

    log("Internet audio transmission started.")


def stop_transmitter():
    global transmitting

    with state_lock:
        transmitting = False

    clear_queue(transmit_queue)
    set_transmission_ui(False)

    log("Internet audio transmission stopped.")
    log("Input device remains active.")


# =========================================================
# VU METERS
# =========================================================

def draw_vu_meter(canvas, level_db, channel_label):
    canvas.delete("all")

    width = canvas.winfo_width()
    height = canvas.winfo_height()

    if width <= 1:
        width = 700

    if height <= 1:
        height = 48

    meter_left = 4
    meter_right = width - 4
    meter_top = 4
    meter_bottom = 26
    meter_width = meter_right - meter_left

    normalized = (
        (level_db - MIN_DB) /
        (MAX_DB - MIN_DB)
    )

    normalized = max(0.0, min(1.0, normalized))
    active_width = meter_width * normalized

    canvas.create_rectangle(
        meter_left,
        meter_top,
        meter_right,
        meter_bottom,
        fill="#181818",
        outline="#555555"
    )

    segment_count = 60
    segment_gap = 1

    segment_width = (
        meter_width -
        ((segment_count - 1) * segment_gap)
    ) / segment_count

    active_segments = int(normalized * segment_count)

    for index in range(segment_count):
        x1 = meter_left + index * (
            segment_width + segment_gap
        )
        x2 = x1 + segment_width

        segment_db = MIN_DB + (
            index / segment_count
        ) * 60.0

        if index < active_segments:
            if segment_db >= -3:
                fill = "#ff2020"
            elif segment_db >= -12:
                fill = "#ffd000"
            else:
                fill = "#00d83a"
        else:
            fill = "#303030"

        canvas.create_rectangle(
            x1,
            meter_top + 2,
            x2,
            meter_bottom - 2,
            fill=fill,
            outline=""
        )

    canvas.create_text(
        8,
        15,
        anchor="w",
        text=channel_label,
        fill="white",
        font=("Segoe UI", 8, "bold")
    )

    canvas.create_text(
        width - 8,
        15,
        anchor="e",
        text=f"{level_db:5.1f} dBFS",
        fill="white",
        font=("Consolas", 9, "bold")
    )

    scale_values = [-60, -50, -40, -30, -20, -10, -6, -3, 0]

    for db_value in scale_values:
        position = (
            (db_value - MIN_DB) /
            (MAX_DB - MIN_DB)
        )

        x = meter_left + position * meter_width

        canvas.create_line(
            x,
            meter_bottom + 1,
            x,
            meter_bottom + 5,
            fill="#aaaaaa"
        )

        canvas.create_text(
            x,
            height - 8,
            text=str(db_value),
            fill="#cccccc",
            font=("Segoe UI", 7)
        )


def update_vu_meters():
    with state_lock:
        current_left = left_level
        current_right = right_level

    draw_vu_meter(left_meter, current_left, "L")
    draw_vu_meter(right_meter, current_right, "R")

    app.after(50, update_vu_meters)


# =========================================================
# APPLICATION CLOSE
# =========================================================

def on_close():
    global transmitting
    global listen_enabled

    with state_lock:
        transmitting = False
        listen_enabled = False

    try:
        stop_monitor_output()
    except Exception:
        pass

    try:
        close_input_device()
    except Exception:
        pass

    try:
        disconnect_server()
    except Exception:
        pass

    app.destroy()


# =========================================================
# MAIN WINDOW
# =========================================================

app = tk.Tk()
app.title("AudioBridge Transmitter")
app.geometry("760x520")
app.minsize(700, 480)

app.protocol("WM_DELETE_WINDOW", on_close)

# =========================================================
# HEADER
# =========================================================

header = ttk.Frame(app, padding=(10, 8))
header.pack(fill="x")

title_label = ttk.Label(
    header,
    text="AUDIOBRIDGE TRANSMITTER",
    font=("Segoe UI", 15, "bold")
)
title_label.pack(side="left")

connection_label = tk.Label(
    header,
    text="OFFLINE",
    foreground="red",
    font=("Segoe UI", 9, "bold")
)
connection_label.pack(side="right")

# =========================================================
# INPUT DEVICE
# =========================================================

device_frame = ttk.LabelFrame(
    app,
    text="INPUT DEVICE",
    padding=8
)
device_frame.pack(
    fill="x",
    padx=10,
    pady=4
)

device_combo = ttk.Combobox(
    device_frame,
    state="readonly"
)
device_combo.pack(
    side="left",
    fill="x",
    expand=True
)

device_combo.bind(
    "<<ComboboxSelected>>",
    activate_selected_device
)

refresh_button = ttk.Button(
    device_frame,
    text="Refresh",
    command=refresh_devices
)
refresh_button.pack(
    side="left",
    padx=(6, 0)
)

device_status_label = tk.Label(
    device_frame,
    text="DEVICE INACTIVE",
    foreground="red",
    font=("Segoe UI", 8, "bold")
)
device_status_label.pack(
    side="left",
    padx=(8, 0)
)

# =========================================================
# AUDIO LEVEL
# =========================================================

meter_frame = ttk.LabelFrame(
    app,
    text="AUDIO LEVEL — dBFS",
    padding=8
)
meter_frame.pack(
    fill="x",
    padx=10,
    pady=4
)

left_meter = tk.Canvas(
    meter_frame,
    height=48,
    background="#181818",
    highlightthickness=0
)
left_meter.pack(
    fill="x",
    pady=2
)

right_meter = tk.Canvas(
    meter_frame,
    height=48,
    background="#181818",
    highlightthickness=0
)
right_meter.pack(
    fill="x",
    pady=2
)

# =========================================================
# STREAM AND SERVER
# =========================================================

settings_frame = ttk.Frame(app)
settings_frame.pack(
    fill="x",
    padx=10,
    pady=4
)

stream_frame = ttk.LabelFrame(
    settings_frame,
    text="STREAM",
    padding=8
)
stream_frame.pack(
    side="left",
    fill="both",
    expand=True,
    padx=(0, 5)
)

stream_name = ttk.Entry(stream_frame)
stream_name.insert(0, "Main Radio Feed")
stream_name.pack(fill="x")

server_frame = ttk.LabelFrame(
    settings_frame,
    text="SERVER",
    padding=8
)
server_frame.pack(
    side="left",
    fill="both",
    expand=True,
    padx=(5, 0)
)

server_entry = ttk.Entry(server_frame)
server_entry.insert(0, DEFAULT_SERVER_URL)
server_entry.pack(fill="x")

# =========================================================
# CONTROL BUTTONS
# =========================================================

control_frame = ttk.Frame(app, padding=(10, 4))
control_frame.pack(fill="x")

connect_button = ttk.Button(
    control_frame,
    text="CONNECT",
    command=connect_server
)
connect_button.pack(
    side="left",
    padx=(0, 5)
)

start_button = ttk.Button(
    control_frame,
    text="START",
    command=start_transmitter
)
start_button.pack(
    side="left",
    padx=5
)

stop_button = ttk.Button(
    control_frame,
    text="STOP",
    command=stop_transmitter,
    state="disabled"
)
stop_button.pack(
    side="left",
    padx=5
)

listen_var = tk.BooleanVar(value=False)

listen_button = ttk.Checkbutton(
    control_frame,
    text="LISTEN: OFF",
    variable=listen_var,
    command=toggle_listen
)
listen_button.pack(
    side="left",
    padx=12
)

status_label = tk.Label(
    control_frame,
    text="NOT TRANSMITTING",
    font=("Segoe UI", 9, "bold")
)
status_label.pack(side="right")

# =========================================================
# EVENT LOG
# =========================================================

log_frame = ttk.LabelFrame(
    app,
    text="EVENT LOG",
    padding=6
)
log_frame.pack(
    fill="both",
    expand=True,
    padx=10,
    pady=(2, 8)
)

log_box = tk.Text(
    log_frame,
    height=5,
    font=("Consolas", 9)
)
log_box.pack(
    fill="both",
    expand=True
)

# =========================================================
# INITIALIZATION
# =========================================================

log("AudioBridge transmitter ready.")
log(
    f"Audio format: {SAMPLE_RATE} Hz / "
    f"{CHANNELS} channels / PCM 16-bit"
)
log(
    f"Network block: {BLOCK_SIZE} frames "
    f"({BLOCK_SIZE / SAMPLE_RATE * 1000:.0f} ms)"
)

refresh_devices()
update_vu_meters()

app.mainloop()
