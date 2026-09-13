const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;

const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("AudioBridge WebSocket Server Running");
});

// Create a single WebSocket server on the HTTP server, then handle paths manually in connection
const wss = new WebSocket.Server({ server });

const transmitters = new Set();
const receivers = new Set();

let activeAudioConfig = {
    sampleRate: 44100,
    channels: 2,
    bitDepth: 16
};

wss.on("connection", (ws, req) => {
    const clientIp = req.headers["x-forwarded-for"] || req.socket.remoteAddress;
    const urlPath = new URL(req.url, `http://${req.headers.host}`).pathname;

    console.log(`[+] New client connected from ${clientIp} on path: ${urlPath}`);

    // Auto-assign roles based on the URL path requested
    if (urlPath === "/tx") {
        ws.isTransmitter = true;
        ws.isReceiver = false;
        transmitters.add(ws);
        console.log(`[Transmitter Connected via /tx] Total active transmitters: ${transmitters.size}`);
    } else if (urlPath === "/rx") {
        ws.isReceiver = true;
        ws.isTransmitter = false;
        receivers.add(ws);
        console.log(`[Receiver Connected via /rx] Total active receivers: ${receivers.size}`);

        // Send current format configuration right away to the receiver
        ws.send(JSON.stringify({
            type: "status",
            message: "Connected to AudioBridge server (/rx)",
            sampleRate: activeAudioConfig.sampleRate,
            channels: activeAudioConfig.channels
        }));
    } else {
        // Fallback / legacy support for generic connection paths
        ws.isTransmitter = false;
        ws.isReceiver = false;
    }

    ws.on("message", (message, isBinary) => {
        const isBinaryData = isBinary || Buffer.isBuffer(message) || message instanceof ArrayBuffer;

        if (!isBinaryData) {
            try {
                const data = JSON.parse(message.toString());

                if (data.type === "register-transmitter") {
                    ws.isTransmitter = true;
                    ws.isReceiver = false;
                    transmitters.add(ws);
                    receivers.delete(ws);

                    if (data.sampleRate) activeAudioConfig.sampleRate = data.sampleRate;
                    if (data.channels) activeAudioConfig.channels = data.channels;

                    console.log(`[Transmitter Registered] SampleRate: ${activeAudioConfig.sampleRate}Hz, Channels: ${activeAudioConfig.channels}`);

                    ws.send(JSON.stringify({
                        type: "status",
                        message: "Transmitter registered successfully."
                    }));

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
                    return;
                } else if (data.type === "register-receiver") {
                    ws.isReceiver = true;
                    ws.isTransmitter = false;
                    receivers.add(ws);
                    transmitters.delete(ws);

                    console.log(`[Receiver Registered] Total active receivers: ${receivers.size}`);

                    ws.send(JSON.stringify({
                        type: "status",
                        message: "Connected to AudioBridge server",
                        sampleRate: activeAudioConfig.sampleRate,
                        channels: activeAudioConfig.channels
                    }));
                    return;
                }
            } catch (err) {
                // Fallback to binary processing if JSON parsing fails
            }
        }

        // Fallback safety if connected without explicit /tx path designation
        if (!ws.isTransmitter && !ws.isReceiver) {
            ws.isTransmitter = true;
            transmitters.add(ws);
            console.log("[Auto-Promoted] Socket registered as Transmitter via binary audio stream.");
        }

        if (ws.isTransmitter) {
            if (receivers.size === 0) return;

            receivers.forEach((receiver) => {
                if (receiver.readyState === WebSocket.OPEN) {
                    receiver.send(message, { binary: true });
                }
            });
        }
    });

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

    ws.on("error", (error) => {
        console.error(`[-] Socket error (${clientIp}):`, error.message);
    });
});

server.listen(PORT, "0.0.0.0", () => {
    console.log(`====================================================`);
    console.log(` AudioBridge Server is active`);
    console.log(` Listening on port: ${PORT}`);
    console.log(` Endpoints: /tx (Transmitter), /rx (Receiver)`);
    console.log(`====================================================`);
});