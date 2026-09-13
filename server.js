const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;

// Create standard HTTP server
const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("AudioBridge Multi-Stream Server Running");
});

// Initialize WebSocket server attached to the HTTP server instance
const wss = new WebSocket.Server({ noServer: true });

const rooms = {
    am: { transmitters: new Set(), receivers: new Set(), config: { sampleRate: 44100, channels: 2 } },
    fm: { transmitters: new Set(), receivers: new Set(), config: { sampleRate: 44100, channels: 2 } }
};

// Handle explicit HTTP upgrade requests for WebSockets to support Render's proxy routing
server.on("upgrade", (request, socket, head) => {
    let urlPath = "/";
    try {
        const parsedUrl = new URL(request.url, `http://${request.headers.host || 'localhost'}`);
        urlPath = parsedUrl.pathname;
    } catch (e) {
        urlPath = request.url || "/";
    }

    // Accept connections on /am, /fm, or root
    if (urlPath.startsWith("/am") || urlPath.startsWith("/fm") || urlPath === "/") {
        wss.handleUpgrade(request, socket, head, (ws) => {
            wss.emit("connection", ws, request);
        });
    } else {
        socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
        socket.destroy();
    }
});

wss.on("connection", (ws, req) => {
    const clientIp = req.headers["x-forwarded-for"] || req.socket.remoteAddress;
    
    let urlPath = "/";
    try {
        const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
        urlPath = parsedUrl.pathname;
    } catch (e) {
        urlPath = req.url || "/";
    }

    console.log(`[+] Connection from ${clientIp} on path: ${urlPath}`);

    let station = "am";
    if (urlPath.includes("fm")) {
        station = "fm";
    }

    const isTx = urlPath.includes("tx") || urlPath === "/" || urlPath === "";

    if (isTx) {
        ws.isTransmitter = true;
        ws.isReceiver = false;
        ws.stationRoom = station;
        rooms[station].transmitters.add(ws);
        console.log(`[Transmitter Connected -> ${station.toUpperCase()}] Active Tx: ${rooms[station].transmitters.size}`);
        
        ws.send(JSON.stringify({ type: "status", message: `Transmitter bound to ${station.toUpperCase()}` }));
    } else {
        ws.isReceiver = true;
        ws.isTransmitter = false;
        ws.stationRoom = station;
        rooms[station].receivers.add(ws);
        console.log(`[Receiver Connected -> ${station.toUpperCase()}] Active Rx: ${rooms[station].receivers.size}`);

        ws.send(JSON.stringify({
            type: "status",
            message: `Connected to ${station.toUpperCase()} stream`,
            sampleRate: rooms[station].config.sampleRate,
            channels: rooms[station].config.channels
        }));
    }

    ws.on("message", (message, isBinary) => {
        const isBinaryData = isBinary || Buffer.isBuffer(message) || message instanceof ArrayBuffer;

        if (!isBinaryData) {
            try {
                const data = JSON.parse(message.toString());
                if (data.type === "register-transmitter" && data.station) {
                    const oldStation = ws.stationRoom;
                    if (oldStation && rooms[oldStation]) {
                        rooms[oldStation].transmitters.delete(ws);
                    }
                    ws.stationRoom = data.station;
                    if (rooms[data.station]) {
                        rooms[data.station].transmitters.add(ws);
                        console.log(`[Transmitter Re-registered to ${data.station.toUpperCase()}]`);
                    }
                    return;
                }
            } catch (e) {}
        }

        if (ws.isTransmitter) {
            const currentRoom = rooms[ws.stationRoom || station];
            if (currentRoom && currentRoom.receivers.size > 0) {
                currentRoom.receivers.forEach((receiver) => {
                    if (receiver.readyState === WebSocket.OPEN) {
                        receiver.send(message, { binary: isBinaryData });
                    }
                });
            }
        }
    });

    ws.on("close", () => {
        const currentRoom = rooms[ws.stationRoom || station];
        if (currentRoom) {
            if (ws.isTransmitter) {
                currentRoom.transmitters.delete(ws);
                console.log(`[-] Transmitter disconnected from ${ws.stationRoom?.toUpperCase() || station.toUpperCase()}`);
            }
            if (ws.isReceiver) {
                currentRoom.receivers.delete(ws);
                console.log(`[-] Receiver disconnected from ${ws.stationRoom?.toUpperCase() || station.toUpperCase()}`);
            }
        }
    });

    ws.on("error", (err) => {
        console.error(`[-] Error (${clientIp}):`, err.message);
    });
});

// Explicitly bind to 0.0.0.0 for cloud hosting compatibility
server.listen(PORT, "0.0.0.0", () => {
    console.log(`AudioBridge Server running on port ${PORT}`);
});