const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;

const transmitters = new Set();
const receivers = new Set();

const httpServer = http.createServer((req, res) => {
    if (req.url === "/" || req.url === "/health") {
        res.writeHead(200, {
            "Content-Type": "text/plain; charset=utf-8"
        });

        res.end("AudioBridge server is running");
        return;
    }

    res.writeHead(404, {
        "Content-Type": "text/plain; charset=utf-8"
    });

    res.end("Not found");
});


const wss = new WebSocket.Server({
    server: httpServer,
    path: "/audio",

    // PCM audio is already uncompressed.
    // Disable WebSocket compression to reduce CPU usage.
    perMessageDeflate: false,

    // 1 MB is more than enough for our 20 ms PCM packets.
    maxPayload: 1024 * 1024
});


function sendJSON(socket, object) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
    }

    try {
        socket.send(JSON.stringify(object));
    } catch (error) {
        console.error("[WS] Failed to send JSON:", error.message);
    }
}


function sendStatus(socket, message) {
    sendJSON(socket, {
        type: "status",
        message: message,
        status: message,
        transmitters: transmitters.size,
        receivers: receivers.size,
        timestamp: Date.now()
    });
}


function broadcastStatus() {
    const status = {
        type: "server-status",
        message: "AudioBridge server status",
        status: "online",
        transmitters: transmitters.size,
        receivers: receivers.size,
        timestamp: Date.now()
    };

    const json = JSON.stringify(status);

    for (const socket of [...transmitters, ...receivers]) {
        if (socket.readyState !== WebSocket.OPEN) {
            continue;
        }

        try {
            socket.send(json);
        } catch (error) {
            console.error(
                "[STATUS] Failed to send status:",
                error.message
            );
        }
    }
}


function removeClient(socket) {
    transmitters.delete(socket);
    receivers.delete(socket);

    broadcastStatus();
}


function registerTransmitter(socket, message) {
    // Remove from either group first.
    transmitters.delete(socket);
    receivers.delete(socket);

    socket.clientRole = "transmitter";

    socket.station = message.station || "Unknown Station";
    socket.sampleRate = message.sampleRate || 48000;
    socket.channels = message.channels || 2;
    socket.format = message.format || "pcm_s16le";

    transmitters.add(socket);

    console.log(
        `[REGISTER] TRANSMITTER: ${socket.station}`
    );

    console.log(
        `[REGISTER] Format: ${socket.sampleRate} Hz / ` +
        `${socket.channels} channel(s) / ${socket.format}`
    );

    console.log(
        `[STATUS] Transmitters: ${transmitters.size} | ` +
        `Receivers: ${receivers.size}`
    );

    sendStatus(
        socket,
        "Transmitter registered successfully"
    );

    broadcastStatus();
}


function registerReceiver(socket) {
    // Remove from either group first.
    transmitters.delete(socket);
    receivers.delete(socket);

    socket.clientRole = "receiver";

    receivers.add(socket);

    console.log("[REGISTER] RECEIVER");

    console.log(
        `[STATUS] Transmitters: ${transmitters.size} | ` +
        `Receivers: ${receivers.size}`
    );

    sendStatus(
        socket,
        "Receiver registered successfully"
    );

    broadcastStatus();
}


wss.on("connection", (socket, request) => {

    socket.clientRole = null;
    socket.isAlive = true;

    const remoteAddress =
        request.socket.remoteAddress || "unknown";

    console.log(
        `[WS] Client connected from ${remoteAddress}`
    );

    sendStatus(
        socket,
        "Connected to AudioBridge server"
    );


    // ---------------------------------------------------------
    // WEBSOCKET PONG / HEARTBEAT
    // ---------------------------------------------------------

    socket.on("pong", () => {
        socket.isAlive = true;
    });


    // ---------------------------------------------------------
    // MESSAGE HANDLER
    // ---------------------------------------------------------

    socket.on("message", (data, isBinary) => {

        // -----------------------------------------------------
        // BINARY AUDIO
        // -----------------------------------------------------

        if (isBinary || Buffer.isBuffer(data)) {

            if (socket.clientRole !== "transmitter") {
                console.warn(
                    "[AUDIO] Ignoring binary packet from " +
                    "unregistered/non-transmitter client."
                );

                return;
            }

            const audioData = Buffer.isBuffer(data)
                ? data
                : Buffer.from(data);

            if (audioData.length === 0) {
                return;
            }

            let forwarded = 0;

            for (const receiver of [...receivers]) {

                if (
                    receiver.readyState !== WebSocket.OPEN
                ) {
                    continue;
                }

                try {
                    receiver.send(
                        audioData,
                        {
                            binary: true
                        }
                    );

                    forwarded++;

                } catch (error) {

                    console.error(
                        "[AUDIO] Receiver send error:",
                        error.message
                    );
                }
            }

            console.log(
                `[AUDIO] ${audioData.length} bytes -> ` +
                `${forwarded} receiver(s)`
            );

            return;
        }


        // -----------------------------------------------------
        // TEXT / JSON MESSAGE
        // -----------------------------------------------------

        let message;

        try {

            const text = data.toString();

            message = JSON.parse(text);

        } catch (error) {

            console.warn(
                "[WS] Invalid JSON message:",
                error.message
            );

            return;
        }


        if (!message || typeof message !== "object") {
            return;
        }


        // -----------------------------------------------------
        // TRANSMITTER REGISTRATION
        // -----------------------------------------------------

        if (
            message.type === "register-transmitter"
        ) {

            registerTransmitter(
                socket,
                message
            );

            return;
        }


        // -----------------------------------------------------
        // RECEIVER REGISTRATION
        // -----------------------------------------------------

        if (
            message.type === "register-receiver"
        ) {

            registerReceiver(socket);

            return;
        }


        // -----------------------------------------------------
        // BACKWARD-COMPATIBLE REGISTRATION
        // -----------------------------------------------------

        if (
            message.type === "register" &&
            message.role === "transmitter"
        ) {

            registerTransmitter(
                socket,
                message
            );

            return;
        }


        if (
            message.type === "register" &&
            message.role === "receiver"
        ) {

            registerReceiver(socket);

            return;
        }


        // -----------------------------------------------------
        // APPLICATION PING
        // -----------------------------------------------------

        if (message.type === "ping") {

            sendJSON(socket, {
                type: "pong",
                timestamp: Date.now(),
                transmitters: transmitters.size,
                receivers: receivers.size
            });

            return;
        }


        // -----------------------------------------------------
        // REQUEST STATUS
        // -----------------------------------------------------

        if (message.type === "status") {

            sendStatus(
                socket,
                "AudioBridge server status"
            );

            return;
        }


        // -----------------------------------------------------
        // UNKNOWN MESSAGE
        // -----------------------------------------------------

        console.log(
            "[WS] Unknown message:",
            message
        );
    });


    // ---------------------------------------------------------
    // ERROR
    // ---------------------------------------------------------

    socket.on("error", (error) => {

        console.error(
            `[WS] Socket error (${remoteAddress}):`,
            error.message
        );
    });


    // ---------------------------------------------------------
    // CLOSE
    // ---------------------------------------------------------

    socket.on("close", (code, reason) => {

        const role = socket.clientRole || "unregistered";

        console.log(
            `[WS] Client disconnected: ${remoteAddress} ` +
            `role=${role} code=${code}`
        );

        if (reason && reason.length > 0) {
            console.log(
                `[WS] Close reason: ${reason.toString()}`
            );
        }

        removeClient(socket);
    });
});


// -------------------------------------------------------------
// HEARTBEAT
// -------------------------------------------------------------
//
// Render / proxies can close connections that appear dead.
// This keeps active WebSocket connections alive.
//

const heartbeatTimer = setInterval(() => {

    for (const socket of wss.clients) {

        if (socket.isAlive === false) {

            console.log(
                "[HEARTBEAT] Terminating dead connection."
            );

            try {
                socket.terminate();
            } catch (error) {
                console.error(
                    "[HEARTBEAT] Termination error:",
                    error.message
                );
            }

            continue;
        }

        socket.isAlive = false;

        try {
            socket.ping();
        } catch (error) {
            console.error(
                "[HEARTBEAT] Ping error:",
                error.message
            );
        }
    }

}, 30000);


// -------------------------------------------------------------
// CLEANUP
// -------------------------------------------------------------

wss.on("close", () => {
    clearInterval(heartbeatTimer);
});


// -------------------------------------------------------------
// HTTP SERVER
// -------------------------------------------------------------

httpServer.listen(
    PORT,
    "0.0.0.0",
    () => {

        console.log(
            "=========================================="
        );

        console.log(
            "       AUDIOBRIDGE SERVER ONLINE"
        );

        console.log(
            "=========================================="
        );

        console.log(
            `HTTP port: ${PORT}`
        );

        console.log(
            `WebSocket endpoint: /audio`
        );

        console.log(
            `Health endpoint: /health`
        );

        console.log(
            "PCM format: 48000 Hz / Stereo / " +
            "16-bit PCM"
        );

        console.log(
            "=========================================="
        );
    }
);


// -------------------------------------------------------------
// SERVER ERROR
// -------------------------------------------------------------

httpServer.on("error", (error) => {

    console.error(
        "[HTTP] Server error:",
        error
    );
});


// -------------------------------------------------------------
// PROCESS SHUTDOWN
// -------------------------------------------------------------

function shutdown(signal) {

    console.log(
        `\n[SERVER] Received ${signal}. Shutting down...`
    );

    clearInterval(heartbeatTimer);

    for (const socket of wss.clients) {

        try {
            socket.close(
                1001,
                "Server shutting down"
            );
        } catch (error) {
            // Ignore shutdown errors.
        }
    }

    httpServer.close(() => {

        console.log(
            "[SERVER] Shutdown complete."
        );

        process.exit(0);
    });
}


process.on(
    "SIGTERM",
    () => shutdown("SIGTERM")
);

process.on(
    "SIGINT",
    () => shutdown("SIGINT")
);