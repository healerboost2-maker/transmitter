const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 10000;


// ============================================================
// ROOMS
// ============================================================

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


// ============================================================
// HTTP SERVER
// ============================================================

const server = http.createServer((req, res) => {

    const url = new URL(
        req.url,
        `http://${req.headers.host || "localhost"}`
    );


    // --------------------------------------------------------
    // HEALTH
    // --------------------------------------------------------

    if (url.pathname === "/health") {

        res.writeHead(200, {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        });

        res.end(JSON.stringify({

            status: "ok",

            service: "AudioBridge",

            websocket: true,

            endpoints: {

                amtx: "/amtx",
                amrx: "/amrx",

                fmtx: "/fmtx",
                fmrx: "/fmrx"

            },

            timestamp: new Date().toISOString()

        }));

        return;
    }


    // --------------------------------------------------------
    // STATUS
    // --------------------------------------------------------

    if (url.pathname === "/status") {

        res.writeHead(200, {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        });

        res.end(JSON.stringify({

            service: "AudioBridge",

            am: {

                transmitters:
                    rooms.am.transmitters.size,

                receivers:
                    rooms.am.receivers.size,

                sampleRate:
                    rooms.am.config.sampleRate,

                channels:
                    rooms.am.config.channels

            },

            fm: {

                transmitters:
                    rooms.fm.transmitters.size,

                receivers:
                    rooms.fm.receivers.size,

                sampleRate:
                    rooms.fm.config.sampleRate,

                channels:
                    rooms.fm.config.channels

            },

            timestamp:
                new Date().toISOString()

        }));

        return;
    }


    // --------------------------------------------------------
    // ROOT
    // --------------------------------------------------------

    res.writeHead(200, {

        "Content-Type":
            "text/plain",

        "Cache-Control":
            "no-cache"

    });


    res.end(

        "AudioBridge Multi-Stream Server Running\n\n" +

        "WebSocket Endpoints:\n\n" +

        "AM Transmitter:\n" +
        "/amtx\n\n" +

        "AM Receiver:\n" +
        "/amrx\n\n" +

        "FM Transmitter:\n" +
        "/fmtx\n\n" +

        "FM Receiver:\n" +
        "/fmrx\n\n" +

        "HTTP:\n" +
        "/health\n" +
        "/status\n"

    );

});


// ============================================================
// WEBSOCKET SERVER
// ============================================================

const wss = new WebSocket.Server({

    noServer: true,

    // Audio should NOT be compressed.
    // This reduces CPU usage and latency.

    perMessageDeflate: false

});


// ============================================================
// WEBSOCKET UPGRADE
// ============================================================

server.on("upgrade", (request, socket, head) => {

    let url;


    try {

        url = new URL(

            request.url,

            `http://${request.headers.host || "localhost"}`

        );

    } catch (error) {

        socket.write(

            "HTTP/1.1 400 Bad Request\r\n" +
            "Connection: close\r\n" +
            "\r\n"

        );

        socket.destroy();

        return;
    }


    const pathname =
        url.pathname.toLowerCase();


    // ========================================================
    // VALID ENDPOINTS
    // ========================================================

    const validPaths = new Set([

        "/amtx",
        "/amrx",

        "/fmtx",
        "/fmrx"

    ]);


    if (!validPaths.has(pathname)) {

        socket.write(

            "HTTP/1.1 404 Not Found\r\n" +
            "Connection: close\r\n" +
            "\r\n"

        );

        socket.destroy();

        return;
    }


    // ========================================================
    // HANDLE WEBSOCKET
    // ========================================================

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


// ============================================================
// CONNECTION
// ============================================================

wss.on("connection", (ws, req) => {

    const clientIp =

        req.headers["x-forwarded-for"] ||

        req.socket.remoteAddress;


    let url;


    try {

        url = new URL(

            req.url,

            `http://${req.headers.host || "localhost"}`

        );

    } catch (error) {

        ws.close(

            1008,

            "Invalid URL"

        );

        return;
    }


    const pathname =
        url.pathname.toLowerCase();


    // ========================================================
    // DETERMINE STATION
    // ========================================================

    let station = null;


    if (

        pathname === "/amtx" ||

        pathname === "/amrx"

    ) {

        station = "am";

    }


    if (

        pathname === "/fmtx" ||

        pathname === "/fmrx"

    ) {

        station = "fm";

    }


    // ========================================================
    // DETERMINE ROLE
    // ========================================================

    const isTransmitter =

        pathname === "/amtx" ||

        pathname === "/fmtx";


    const isReceiver =

        pathname === "/amrx" ||

        pathname === "/fmrx";


    // ========================================================
    // VALIDATION
    // ========================================================

    if (

        !station ||

        (!isTransmitter && !isReceiver)

    ) {

        ws.close(

            1008,

            "Invalid WebSocket endpoint"

        );

        return;
    }


    // ========================================================
    // STORE CLIENT INFORMATION
    // ========================================================

    ws.stationRoom =
        station;


    ws.isTransmitter =
        isTransmitter;


    ws.isReceiver =
        isReceiver;


    ws.isAlive = true;


    const room =
        rooms[station];


    // ========================================================
    // CONNECTION LOG
    // ========================================================

    console.log(

        `[+] ` +

        `${isTransmitter ? "TRANSMITTER" : "RECEIVER"} ` +

        `${station.toUpperCase()} ` +

        `connected from ${clientIp}`

    );


    // ========================================================
    // HEARTBEAT
    // ========================================================

    ws.on("pong", () => {

        ws.isAlive = true;

    });


    // ========================================================
    // TRANSMITTER
    // ========================================================

    if (isTransmitter) {

        room.transmitters.add(ws);


        console.log(

            `[TX ${station.toUpperCase()}] ` +

            `Active transmitters: ` +

            `${room.transmitters.size}`

        );


        // Send status/configuration

        ws.send(

            JSON.stringify({

                type: "status",

                role: "transmitter",

                station:
                    station.toUpperCase(),

                sampleRate:
                    room.config.sampleRate,

                channels:
                    room.config.channels,

                endpoint:
                    pathname,

                server:
                    "AudioBridge"

            })

        );

    }


    // ========================================================
    // RECEIVER
    // ========================================================

    if (isReceiver) {

        room.receivers.add(ws);


        console.log(

            `[RX ${station.toUpperCase()}] ` +

            `Active receivers: ` +

            `${room.receivers.size}`

        );


        // Send status/configuration

        ws.send(

            JSON.stringify({

                type: "status",

                role: "receiver",

                station:
                    station.toUpperCase(),

                sampleRate:
                    room.config.sampleRate,

                channels:
                    room.config.channels,

                endpoint:
                    pathname,

                server:
                    "AudioBridge"

            })

        );

    }


    // ========================================================
    // RECEIVE DATA FROM TRANSMITTER
    // ========================================================

    ws.on("message", (message, isBinary) => {

        // ----------------------------------------------------
        // RECEIVERS CANNOT SEND AUDIO
        // ----------------------------------------------------

        if (!ws.isTransmitter) {

            return;

        }


        const currentRoom =
            rooms[ws.stationRoom];


        if (!currentRoom) {

            return;

        }


        // ----------------------------------------------------
        // LOG AUDIO PACKET
        // ----------------------------------------------------

        // Uncomment only for debugging.
        //
        // console.log(
        //     `[AUDIO ${ws.stationRoom.toUpperCase()}]`,
        //     message.length,
        //     "bytes"
        // );


        // ====================================================
        // BROADCAST TO RECEIVERS
        // ====================================================

        currentRoom.receivers.forEach(

            (receiver) => {

                if (

                    receiver.readyState ===

                    WebSocket.OPEN

                ) {

                    try {

                        receiver.send(

                            message,

                            {

                                binary:
                                    isBinary

                            }

                        );

                    } catch (error) {

                        console.error(

                            `[SEND ERROR ` +

                            `${ws.stationRoom.toUpperCase()}]`,

                            error.message

                        );

                    }

                }

            }

        );

    });


    // ========================================================
    // CLOSE
    // ========================================================

    ws.on("close", (code) => {

        const currentRoom =
            rooms[ws.stationRoom];


        if (!currentRoom) {

            return;

        }


        // ----------------------------------------------------
        // TRANSMITTER
        // ----------------------------------------------------

        if (ws.isTransmitter) {

            currentRoom.transmitters.delete(ws);


            console.log(

                `[-] TX ` +

                `${ws.stationRoom.toUpperCase()} ` +

                `disconnected ` +

                `(code ${code})`

            );

        }


        // ----------------------------------------------------
        // RECEIVER
        // ----------------------------------------------------

        if (ws.isReceiver) {

            currentRoom.receivers.delete(ws);


            console.log(

                `[-] RX ` +

                `${ws.stationRoom.toUpperCase()} ` +

                `disconnected ` +

                `(code ${code})`

            );

        }

    });


    // ========================================================
    // ERROR
    // ========================================================

    ws.on("error", (error) => {

        console.error(

            `[WS ERROR ${clientIp}]`,

            error.message

        );

    });

});


// ============================================================
// HEARTBEAT TIMER
// ============================================================

const heartbeatTimer = setInterval(() => {

    wss.clients.forEach((ws) => {

        // ----------------------------------------------------
        // Dead connection
        // ----------------------------------------------------

        if (ws.isAlive === false) {

            console.log(

                `[TIMEOUT] ` +

                `${ws.stationRoom || "UNKNOWN"} ` +

                `client`

            );


            ws.terminate();

            return;

        }


        ws.isAlive = false;


        // ----------------------------------------------------
        // Ping
        // ----------------------------------------------------

        ws.ping();

    });

}, 30000);


// ============================================================
// WEBSOCKET CLOSE
// ============================================================

wss.on("close", () => {

    clearInterval(
        heartbeatTimer
    );

});


// ============================================================
// GRACEFUL SHUTDOWN
// ============================================================

process.on("SIGTERM", () => {

    console.log(
        "SIGTERM received."
    );


    wss.clients.forEach((ws) => {

        try {

            ws.close(

                1001,

                "Server shutting down"

            );

        } catch (error) {

            // Ignore
        }

    });


    server.close(() => {

        console.log(
            "Server closed."
        );

        process.exit(0);

    });

});


// ============================================================
// START
// ============================================================

server.listen(

    PORT,

    "0.0.0.0",

    () => {

        console.log("");
        console.log("======================================");
        console.log("       AUDIOBRIDGE SERVER");
        console.log("======================================");

        console.log(
            `PORT: ${PORT}`
        );

        console.log("");

        console.log(
            "AM TX: /amtx"
        );

        console.log(
            "AM RX: /amrx"
        );

        console.log("");

        console.log(
            "FM TX: /fmtx"
        );

        console.log(
            "FM RX: /fmrx"
        );

        console.log("");

        console.log(
            "HEALTH: /health"
        );

        console.log(
            "STATUS: /status"
        );

        console.log(
            "======================================"
        );

        console.log("");

    }

);