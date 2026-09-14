const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;

const server = http.createServer((req, res) => {
    res.writeHead(200, {
        "Content-Type": "text/plain"
    });

    res.end("AudioBridge Multi-Stream Server Running");
});

const wss = new WebSocket.Server({
    noServer: true
});

const rooms = {
    am: {
        transmitters: new Set(),
        receivers: new Set(),
        config: {
            sampleRate: 44100,
            channels: 2
        }
    },

    fm: {
        transmitters: new Set(),
        receivers: new Set(),
        config: {
            sampleRate: 44100,
            channels: 2
        }
    }
};


// ================================
// HEARTBEAT PING-PONG (Keeps idle proxy connections alive)
// ================================
function heartbeat() {
    this.isAlive = true;
}

const pingInterval = setInterval(() => {
    wss.clients.forEach((ws) => {
        if (ws.isAlive === false) {
            console.log(`[!] Terminating inactive/dead WebSocket client`);
            return ws.terminate();
        }

        ws.isAlive = false;
        ws.ping();
    });
}, 30000); // Check client status every 30 seconds

wss.on("close", () => {
    clearInterval(pingInterval);
});


// ================================
// WEBSOCKET UPGRADE
// ================================

server.on("upgrade", (request, socket, head) => {

    let pathname;

    try {
        const url = new URL(
            request.url,
            `http://${request.headers.host || "localhost"}`
        );

        pathname = url.pathname.toLowerCase();

    } catch (err) {

        socket.write(
            "HTTP/1.1 400 Bad Request\r\n" +
            "Connection: close\r\n" +
            "\r\n"
        );

        socket.destroy();

        return;
    }


    // Only allow these WebSocket endpoints
    const validPath =
        pathname === "/am/tx" ||
        pathname === "/am/rx" ||
        pathname === "/fm/tx" ||
        pathname === "/fm/rx";

    if (!validPath) {

        socket.write(
            "HTTP/1.1 404 Not Found\r\n" +
            "Connection: close\r\n" +
            "\r\n"
        );

        socket.destroy();

        return;
    }


    wss.handleUpgrade(
        request,
        socket,
        head,
        (ws) => {

            wss.emit(
                "connection",
                ws,
                request
            );

        }
    );
});


// ================================
// CONNECTION
// ================================

wss.on("connection", (ws, req) => {

    // Initialize heartbeat tracking
    ws.isAlive = true;
    ws.on("pong", heartbeat);

    const clientIp =
        req.headers["x-forwarded-for"] ||
        req.socket.remoteAddress;


    const url = new URL(
        req.url,
        `http://${req.headers.host || "localhost"}`
    );

    const pathname =
        url.pathname.toLowerCase();


    let station;

    if (pathname.startsWith("/am/")) {
        station = "am";
    }

    if (pathname.startsWith("/fm/")) {
        station = "fm";
    }


    const isTransmitter =
        pathname.endsWith("/tx");

    const isReceiver =
        pathname.endsWith("/rx");


    if (!station || (!isTransmitter && !isReceiver)) {

        ws.close(
            1008,
            "Invalid WebSocket endpoint"
        );

        return;
    }


    ws.stationRoom = station;
    ws.isTransmitter = isTransmitter;
    ws.isReceiver = isReceiver;


    const room = rooms[station];


    console.log(
        `[+] ${isTransmitter ? "TRANSMITTER" : "RECEIVER"} ` +
        `${station.toUpperCase()} connected from ${clientIp}`
    );


    // ================================
    // TRANSMITTER
    // ================================

    if (isTransmitter) {

        room.transmitters.add(ws);

        console.log(
            `[TX ${station.toUpperCase()}] ` +
            `Active transmitters: ${room.transmitters.size}`
        );


        ws.send(
            JSON.stringify({
                type: "status",
                role: "transmitter",
                station: station.toUpperCase(),
                sampleRate: room.config.sampleRate,
                channels: room.config.channels
            })
        );
    }


    // ================================
    // RECEIVER
    // ================================

    if (isReceiver) {

        room.receivers.add(ws);

        console.log(
            `[RX ${station.toUpperCase()}] ` +
            `Active receivers: ${room.receivers.size}`
        );


        ws.send(
            JSON.stringify({
                type: "status",
                role: "receiver",
                station: station.toUpperCase(),
                sampleRate: room.config.sampleRate,
                channels: room.config.channels
            })
        );
    }


    // ================================
    // MESSAGE
    // ================================

    ws.on("message", (message, isBinary) => {

        if (!ws.isTransmitter) {
            return;
        }


        const room =
            rooms[ws.stationRoom];


        if (!room) {
            return;
        }


        // Broadcast audio/data to receivers
        room.receivers.forEach((receiver) => {

            if (
                receiver.readyState === WebSocket.OPEN
            ) {

                receiver.send(
                    message,
                    {
                        binary: isBinary
                    }
                );

            }

        });

    });


    // ================================
    // CLOSE
    // ================================

    ws.on("close", () => {

        const room =
            rooms[ws.stationRoom];


        if (!room) {
            return;
        }


        if (ws.isTransmitter) {

            room.transmitters.delete(ws);

            console.log(
                `[-] TX ${ws.stationRoom.toUpperCase()} disconnected`
            );

        }


        if (ws.isReceiver) {

            room.receivers.delete(ws);

            console.log(
                `[-] RX ${ws.stationRoom.toUpperCase()} disconnected`
            );

        }

    });


    // ================================
    // ERROR
    // ================================

    ws.on("error", (err) => {

        console.error(
            `[WS ERROR ${clientIp}]`,
            err.message
        );

    });

});


// ================================
// SERVER
// ================================

server.listen(
    PORT,
    "0.0.0.0",
    () => {

        console.log(
            `AudioBridge Server running on port ${PORT}`
        );

    }
);