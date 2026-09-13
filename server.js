const http = require("http");
const WebSocket = require("ws");

const PORT = 10000;
const PATH = "/audio";

// Create HTTP server
const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("AudioBridge WebSocket Server Running");
});

// Create WebSocket server attached to HTTP server
const wss = new WebSocket.Server({ server, path: PATH });

const transmitters = new Set();
const receivers = new Set();

// Active stream metadata
let activeAudioConfig = {
    sampleRate: 44100,
    channels: 2,
    bitDepth: 16
};

wss.on("connection", (ws, req) => {
    ws.isTransmitter = false;
    ws.isReceiver = false;
    const clientIp = req.socket.remoteAddress;

    console.log(`[+] New client connected from ${clientIp}`);

    ws.on("message", (message, isBinary) => {
        // -------------------------------------------------------------
        // 1. JSON CONTROL MESSAGES
        // -------------------------------------------------------------
        if (!isBinary) {
            try {
                const data = JSON.parse(message.toString());

                // Registering a Transmitter
                if (data.type === "register-transmitter") {
                    ws.isTransmitter = true;
                    ws.isReceiver = false;
                    transmitters.add(ws);

                    if (data.sampleRate) activeAudioConfig.sampleRate = data.sampleRate;
                    if (data.channels) activeAudioConfig.channels = data.channels;

                    console.log(`[Transmitter Registered] SampleRate: ${activeAudioConfig.sampleRate}Hz, Channels: ${activeAudioConfig.channels}`);

                    // Send confirmation back to transmitter
                    ws.send(JSON.stringify({
                        type: "status",
                        message: "Transmitter registered successfully."
                    }));

                    // Broadcast updated dynamic stream metadata to all current receivers
                    const formatNotice = JSON.stringify({
                        type: "format-update",
                        sampleRate: activeAudioConfig.sampleRate,
                        channels: activeAudioConfig.channels
                    });

                    receivers.forEach((receiver) => {
                        if (receiver.readyState === WebSocket.OPEN) {
                            receiver.send(formatNotice);
                        }
                    });
                }

                // Registering a Receiver
                else if (data.type === "register-receiver") {
                    ws.isReceiver = true;
                    ws.isTransmitter = false;
                    receivers.add(ws);

                    console.log(`[Receiver Registered] Total active receivers: ${receivers.size}`);

                    // Acknowledge receiver registration and send current audio layout
                    ws.send(JSON.stringify({
                        type: "status",
                        message: "Connected to AudioBridge server",
                        sampleRate: activeAudioConfig.sampleRate,
                        channels: activeAudioConfig.channels
                    }));
                }
            } catch (err) {
                console.error("[-] Failed to process JSON control message:", err.message);
            }
            return;
        }

        // -------------------------------------------------------------
        // 2. RAW BINARY PCM AUDIO FORWARDING
        // -------------------------------------------------------------
        if (ws.isTransmitter) {
            if (receivers.size === 0) return;

            // Broadcast incoming binary audio chunk to all connected receivers
            receivers.forEach((receiver) => {
                if (receiver.readyState === WebSocket.OPEN) {
                    receiver.send(message, { binary: true });
                }
            });
        }
    });

    // Handle Client Disconnections
    ws.on("close", () => {
        if (ws.isTransmitter) {
            transmitters.delete(ws);
            console.log("[-] Transmitter disconnected.");
        }
        if (ws.isReceiver) {
            receivers.delete(ws);
            console.log(`[-] Receiver disconnected. Remaining receivers: ${receivers.size}`);
        }
    });

    // Handle Connection Errors
    ws.on("error", (error) => {
        console.error(`[-] Socket error (${clientIp}):`, error.message);
    });
});

server.listen(PORT, () => {
    console.log(`====================================================`);
    console.log(` AudioBridge Server is active`);
    console.log(` Listening on: ws://localhost:${PORT}${PATH}`);
    console.log(`====================================================`);
});