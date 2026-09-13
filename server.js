const http = require("http");
const { WebSocketServer } = require("ws");

const PORT = process.env.PORT || 8080;

const server = http.createServer((request, response) => {
    response.writeHead(200, {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    });

    response.end(JSON.stringify({
        status: "online",
        service: "AudioBridge Server",
        websocket: "/audio"
    }));
});

const wss = new WebSocketServer({
    server,
    path: "/audio"
});

let transmitter = null;
const receivers = new Set();

let packetCounter = 0;

function sendJSON(socket, data) {
    if (
        socket &&
        socket.readyState === 1
    ) {
        socket.send(JSON.stringify(data));
    }
}

function broadcastStatus(message) {
    for (const receiver of receivers) {
        sendJSON(receiver, {
            type: "status",
            message: message
        });
    }
}

wss.on("connection", (socket) => {
    let clientRole = null;

    console.log("WebSocket client connected.");

    sendJSON(socket, {
        type: "connected",
        message: "Connected to AudioBridge server"
    });

    socket.on("message", (data, isBinary) => {
        /*
         * IMPORTANT:
         *
         * Use only isBinary here.
         * Text WebSocket messages may arrive as Buffers,
         * but they are still text when isBinary is false.
         */

        if (isBinary) {
            if (clientRole !== "transmitter") {
                return;
            }

            packetCounter++;

            for (const receiver of receivers) {
                if (receiver.readyState === 1) {
                    receiver.send(data, {
                        binary: true
                    });
                }
            }

            if (
                packetCounter === 1 ||
                packetCounter % 100 === 0
            ) {
                console.log(
                    `Audio packet ${packetCounter}: ` +
                    `${data.length} bytes forwarded to ` +
                    `${receivers.size} receiver(s)`
                );
            }

            return;
        }

        let message;

        try {
            message = JSON.parse(
                data.toString()
            );
        } catch (error) {
            console.log(
                "Invalid text message:",
                data.toString()
            );

            return;
        }

        /*
         * TRANSMITTER REGISTRATION
         */

        if (
            message.type ===
            "register-transmitter"
        ) {
            if (
                transmitter &&
                transmitter !== socket
            ) {
                sendJSON(transmitter, {
                    type: "error",
                    message:
                        "Another transmitter is already connected"
                });

                transmitter.close();
            }

            transmitter = socket;
            clientRole = "transmitter";

            console.log(
                "Transmitter registered successfully."
            );

            sendJSON(socket, {
                type: "registered",
                role: "transmitter",
                message:
                    "Transmitter registered successfully"
            });

            broadcastStatus(
                "Transmitter is online"
            );

            return;
        }

        /*
         * RECEIVER REGISTRATION
         */

        if (
            message.type ===
            "register-receiver"
        ) {
            receivers.add(socket);
            clientRole = "receiver";

            console.log(
                `Receiver registered successfully. ` +
                `Total receivers: ${receivers.size}`
            );

            sendJSON(socket, {
                type: "registered",
                role: "receiver",
                message:
                    "Receiver registered successfully"
            });

            const transmitterOnline =
                transmitter &&
                transmitter.readyState === 1;

            sendJSON(socket, {
                type: "status",
                message: transmitterOnline
                    ? "Transmitter is online"
                    : "Waiting for transmitter"
            });

            return;
        }

        /*
         * OPTIONAL PING
         */

        if (message.type === "ping") {
            sendJSON(socket, {
                type: "pong",
                message: "AudioBridge server is active"
            });

            return;
        }

        console.log(
            "Unknown message type:",
            message.type
        );
    });

    socket.on("close", () => {
        console.log(
            `${clientRole || "Unknown"} client disconnected.`
        );

        if (socket === transmitter) {
            transmitter = null;

            broadcastStatus(
                "Transmitter is offline"
            );
        }

        if (receivers.has(socket)) {
            receivers.delete(socket);

            console.log(
                `Receiver removed. ` +
                `Remaining receivers: ${receivers.size}`
            );
        }
    });

    socket.on("error", (error) => {
        console.log(
            "WebSocket error:",
            error.message
        );
    });
});

server.listen(
    PORT,
    "0.0.0.0",
    () => {
        console.log(
            `AudioBridge Server listening on port ${PORT}`
        );
    }
);