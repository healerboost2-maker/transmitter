const express = require('express');
const http = require('http');
const WebSocket = require('ws');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

const PORT = process.env.PORT || 3000;

// Store rooms and their respective configurations and clients
const rooms = {};

// Helper to initialize a room if it doesn't exist
function getOrCreateRoom(stationKey) {
    const key = stationKey.toLowerCase();
    if (!rooms[key]) {
        rooms[key] = {
            config: {
                casterId: null,
                stationName: null,
                format: 'Opus',
                bitrate: '128 kbps',
                sampleRate: 44100,
                channels: 2
            },
            transmitter: null,
            receivers: new Set()
        };
    }
    return rooms[key];
}

// Basic status route to check active streams and listeners
app.get('/status', (req, res) => {
    const statusData = {};
    for (const [key, room] of Object.entries(rooms)) {
        statusData[key] = {
            config: room.config,
            hasTransmitter: room.transmitter !== null && room.transmitter.readyState === WebSocket.OPEN,
            receiverCount: room.receivers.size
        };
    }
    res.json(statusData);
});

// WebSocket Connection Handler
wss.on('connection', (ws, req) => {
    const urlParams = new URLSearchParams(req.url.split('?')[1]);
    const station = urlParams.get('station') || 'default';
    const clientType = urlParams.get('type') || 'receiver'; // 'caster' or 'receiver'

    const room = getOrCreateRoom(station);
    ws.stationRoom = station;
    
    if (clientType === 'caster') {
        ws.isTransmitter = true;
        
        // If an old transmitter exists, close it
        if (room.transmitter && room.transmitter.readyState === WebSocket.OPEN) {
            room.transmitter.close();
        }
        
        room.transmitter = ws;
        console.log(`[TRANSMITTER CONNECTED] Station: ${station.toUpperCase()}`);

    } else {
        ws.isTransmitter = false;
        room.receivers.add(ws);
        console.log(`[RECEIVER CONNECTED] Station: ${station.toUpperCase()} (Total listeners: ${room.receivers.size})`);
    }

    // Handle incoming messages from clients
    ws.on('message', (message, isBinary) => {
        
        // 1. Handle Caster Registration JSON Payload
        if (!isBinary && ws.isTransmitter) {
            try {
                const data = JSON.parse(message.toString());
                
                if (data.type === "register-transmitter") {
                    room.config.casterId = data.casterId || "UNKNOWN";
                    room.config.stationName = data.station || "";
                    room.config.format = data.format || "Opus";
                    room.config.bitrate = data.bitrate || "128 kbps";
                    room.config.sampleRate = data.sampleRate || 44100;
                    room.config.channels = data.channels || 2;

                    console.log(`[CONFIG UPDATE (${station.toUpperCase()})] Caster: ${room.config.casterId} | Format: ${room.config.format} | Bitrate: ${room.config.bitrate} | SampleRate: ${room.config.sampleRate}Hz`);

                    // Acknowledge registration so the Python caster can begin streaming audio
                    ws.send(JSON.stringify({
                        type: "transmitter-accepted",
                        casterId: room.config.casterId,
                        sampleRate: room.config.sampleRate,
                        channels: room.config.channels,
                        format: room.config.format
                    }));
                    return;
                }
            } catch (e) {
                // If it's not JSON, it might be raw control text; ignore or handle accordingly
            }
        }

        // 2. Prevent Receivers from sending data
        if (!ws.isTransmitter) {
            return;
        }

        // 3. Broadcast Audio Binary Data from Transmitters to Receivers
        const currentRoom = rooms[ws.stationRoom];
        if (!currentRoom) return;

        currentRoom.receivers.forEach((receiver) => {
            if (receiver.readyState === WebSocket.OPEN) {
                try {
                    receiver.send(message, { binary: isBinary });
                } catch (error) {
                    console.error(`[SEND ERROR (${ws.stationRoom.toUpperCase()})]`, error.message);
                }
            }
        });
    });

    // Handle client disconnection
    ws.on('close', () => {
        if (ws.isTransmitter) {
            const currentRoom = rooms[ws.stationRoom];
            if (currentRoom && currentRoom.transmitter === ws) {
                currentRoom.transmitter = null;
                console.log(`[TRANSMITTER DISCONNECTED] Station: ${ws.stationRoom.toUpperCase()}`);
            }
        } else {
            const currentRoom = rooms[ws.stationRoom];
            if (currentRoom) {
                currentRoom.receivers.delete(ws);
                console.log(`[RECEIVER DISCONNECTED] Station: ${ws.stationRoom.toUpperCase()} (Remaining listeners: ${currentRoom.receivers.size})`);
            }
        }
    });

    ws.on('error', (err) => {
        console.error(`[WEBSOCKET ERROR (${station.toUpperCase()})]`, err.message);
    });
});

// Start the server
server.listen(PORT, () => {
    console.log(`AM/FM Streaming Server running on port ${PORT}`);
});