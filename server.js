"use strict";

const http = require("http");
const WebSocket = require("ws");

const PORT = 8080;

const AUDIO_SAMPLE_RATE = 48000;
const AUDIO_CHANNELS = 2;
const AUDIO_FORMAT = "pcm_s16le";

let transmitter = null;
let transmitterInfo = null;

let receiverCount = 0;
let totalPackets = 0;
let totalBytes = 0;

const serverStartTime = Date.now();


// =====================================================
// HTTP SERVER
// =====================================================

const httpServer = http.createServer((request, response) => {
    if (request.url === "/status") {
        const status = {
            service: "AudioBridge Server",
            status: "online",

            uptimeSeconds: Math.floor(
                (Date.now() - serverStartTime) / 1000
            ),

            transmitterConnected:
                transmitter !== null &&
                transmitter.readyState === WebSocket.OPEN,

            transmitter: transmitterInfo,

            receiverCount: receiverCount,

            audio: {
                sampleRate: AUDIO_SAMPLE_RATE,
                channels: AUDIO_CHANNELS,
                format: AUDIO_FORMAT
            },

            statistics: {
                totalPackets: totalPackets,
                totalBytes: totalBytes
            }
        };

        response.writeHead(200, {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        });

        response.end(
            JSON.stringify(status, null, 2)
        );

        return;
    }

    response.writeHead(200, {
        "Content-Type": "text/plain"
    });

    response.end(
        "AudioBridge Server is running.\n"
    );
});


// =====================================================
// WEBSOCKET SERVER
// =====================================================

const websocketServer = new WebSocket.Server({
    server: httpServer
});


// =====================================================
// HELPER FUNCTIONS
// =====================================================

function sendJSON(socket, data) {
    if (
        socket &&
        socket.readyState === WebSocket.OPEN
    ) {
        socket.send(
            JSON.stringify(data)
        );
    }
}


function broadcastAudio(audioData) {
    for (const client of websocketServer.clients) {
        if (
            client.role === "receiver" &&
            client.readyState === WebSocket.OPEN
        ) {
            client.send(
                audioData,
                {
                    binary: true
                }
            );
        }
    }
}


function sendServerInfo(socket) {
    sendJSON(socket, {
        type: "server_info",

        audio: {
            sampleRate: AUDIO_SAMPLE_RATE,
            channels: AUDIO_CHANNELS,
            format: AUDIO_FORMAT
        },

        transmitterConnected:
            transmitter !== null &&
            transmitter.readyState === WebSocket.OPEN,

        receiverCount: receiverCount
    });
}


// =====================================================
// CLIENT CONNECTION
// =====================================================

websocketServer.on("connection", (socket, request) => {
    socket.role = null;

    socket.clientAddress =
        request.socket.remoteAddress;

    console.log(
        `Client connected: ${socket.clientAddress}`
    );

    sendServerInfo(socket);


    // =================================================
    // MESSAGE HANDLER
    // =================================================

    socket.on("message", (data, isBinary) => {

        // ---------------------------------------------
        // JSON CONTROL MESSAGE
        // ---------------------------------------------

        if (!isBinary) {
            let message;

            try {
                message = JSON.parse(
                    data.toString()
                );
            } catch (error) {
                sendJSON(socket, {
                    type: "error",
                    message: "Invalid JSON message."
                });

                return;
            }


            // -----------------------------------------
            // TRANSMITTER REGISTRATION
            // -----------------------------------------

            if (
                message.type === "register" &&
                message.role === "transmitter"
            ) {
                if (
                    transmitter !== null &&
                    transmitter.readyState === WebSocket.OPEN
                ) {
                    sendJSON(socket, {
                        type: "error",
                        message:
                            "A transmitter is already connected."
                    });

                    socket.close();

                    return;
                }

                transmitter = socket;
                socket.role = "transmitter";

                transmitterInfo = {
                    station:
                        message.station ||
                        "Unnamed Station",

                    sampleRate:
                        message.sampleRate ||
                        AUDIO_SAMPLE_RATE,

                    channels:
                        message.channels ||
                        AUDIO_CHANNELS,

                    format:
                        message.format ||
                        AUDIO_FORMAT,

                    address:
                        socket.clientAddress,

                    connectedAt:
                        new Date().toISOString()
                };

                sendJSON(socket, {
                    type: "registered",
                    role: "transmitter",

                    audio: {
                        sampleRate: AUDIO_SAMPLE_RATE,
                        channels: AUDIO_CHANNELS,
                        format: AUDIO_FORMAT
                    }
                });

                console.log(
                    "Transmitter registered:",
                    transmitterInfo.station
                );

                return;
            }


            // -----------------------------------------
            // RECEIVER REGISTRATION
            // -----------------------------------------

            if (
                message.type === "register" &&
                message.role === "receiver"
            ) {
                socket.role = "receiver";
                receiverCount++;

                sendJSON(socket, {
                    type: "registered",
                    role: "receiver",

                    audio: {
                        sampleRate: AUDIO_SAMPLE_RATE,
                        channels: AUDIO_CHANNELS,
                        format: AUDIO_FORMAT
                    }
                });

                console.log(
                    `Receiver registered. Total receivers: ${receiverCount}`
                );

                return;
            }


            // -----------------------------------------
            // PING
            // -----------------------------------------

            if (message.type === "ping") {
                sendJSON(socket, {
                    type: "pong",
                    timestamp: Date.now()
                });

                return;
            }

            return;
        }


        // ---------------------------------------------
        // BINARY AUDIO DATA
        // ---------------------------------------------

        if (
            socket === transmitter &&
            socket.role === "transmitter"
        ) {
            totalPackets++;
            totalBytes += data.length;

            broadcastAudio(data);
        }
    });


    // =================================================
    // CLIENT DISCONNECTED
    // =================================================

    socket.on("close", () => {
        console.log(
            `Client disconnected: ${socket.clientAddress}`
        );

       if (
    socket === transmitter &&
    socket.role === "transmitter"
) {
    totalPackets++;
    totalBytes += data.length;

    if (totalPackets % 50 === 0) {
        console.log(
            `Audio received: ${totalPackets} packets | ${totalBytes} bytes`
        );
    }

    broadcastAudio(data);
}
        if (socket.role === "receiver") {
            receiverCount = Math.max(
                0,
                receiverCount - 1
            );

            console.log(
                `Receiver disconnected. Total receivers: ${receiverCount}`
            );
        }
    });


    // =================================================
    // ERROR HANDLER
    // =================================================

    socket.on("error", (error) => {
        console.error(
            "WebSocket error:",
            error.message
        );
    });
});


// =====================================================
// START SERVER
// =====================================================

httpServer.listen(
    PORT,
    "0.0.0.0",
    () => {
        console.log(
            "========================================"
        );

        console.log(
            "        AUDIOBRIDGE SERVER"
        );

        console.log(
            "========================================"
        );

        console.log(
            `WebSocket: ws://127.0.0.1:${PORT}`
        );

        console.log(
            `Status:    http://127.0.0.1:${PORT}/status`
        );

        console.log(
            "Waiting for transmitter and receivers..."
        );
    }
);