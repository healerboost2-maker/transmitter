const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;
const PATH = "/audio";

const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("AudioBridge WebSocket Server Running");
});

const wss = new WebSocket.Server({ server, path: PATH });

const transmitters = new Set();
const receivers = new Set();

let activeAudioConfig = {
    sampleRate: 44100,
    channels: 2,
    bitDepth: 16
};

wss.on("connection", (ws, req) => {
    ws.isTransmitter = false;
    ws.isReceiver = false;
    const clientIp = req.headers["x-forwarded-for"] || req.socket.remoteAddress;

    console.log(`[+] New client connected from ${clientIp}`);

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
    console.log(` Listening on port: ${PORT}${PATH}`);
    console.log(`====================================================`);
});