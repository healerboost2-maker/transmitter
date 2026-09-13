const http = require("http");
const { WebSocketServer } = require("ws");

const PORT = process.env.PORT || 8080;

// Create HTTP server
const server = http.createServer((req, res) => {
  res.writeHead(200, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(
    JSON.stringify({
      status: "online",
      service: "AudioBridge Server",
      websocket: "/audio"
    })
  );
});

// WebSocket server
const wss = new WebSocketServer({
  server,
  path: "/audio"
});

let transmitter = null;
const receivers = new Set();

function sendJSON(socket, data) {
  if (socket && socket.readyState === 1) {
    socket.send(JSON.stringify(data));
  }
}

function broadcastStatus(message) {
  for (const receiver of receivers) {
    sendJSON(receiver, {
      type: "status",
      message
    });
  }
}

wss.on("connection", (socket, request) => {
  console.log("WebSocket client connected:", request.socket.remoteAddress);

  let clientRole = null;

  sendJSON(socket, {
    type: "connected",
    message: "Connected to AudioBridge server"
  });

  socket.on("message", (data, isBinary) => {
    // Binary data is raw PCM audio
    if (isBinary || Buffer.isBuffer(data)) {
      if (clientRole === "transmitter") {
        for (const receiver of receivers) {
          if (receiver.readyState === 1) {
            receiver.send(data, { binary: true });
          }
        }
      }

      return;
    }

    let message;

    try {
      message = JSON.parse(data.toString());
    } catch (error) {
      console.log("Invalid JSON message received");
      return;
    }

    // Register transmitter
    if (message.type === "register-transmitter") {
      if (transmitter && transmitter !== socket) {
        sendJSON(transmitter, {
          type: "error",
          message: "Another transmitter is already connected"
        });

        transmitter.close();
      }

      transmitter = socket;
      clientRole = "transmitter";

      console.log(
        "Transmitter connected:",
        message.name || "Unnamed transmitter"
      );

      sendJSON(socket, {
        type: "registered",
        role: "transmitter",
        message: "Transmitter registered successfully"
      });

      broadcastStatus("Transmitter is online");
      return;
    }

    // Register receiver
    if (message.type === "register-receiver") {
      receivers.add(socket);
      clientRole = "receiver";

      console.log("Receiver connected");

      sendJSON(socket, {
        type: "registered",
        role: "receiver",
        message: "Receiver registered successfully"
      });

      if (transmitter && transmitter.readyState === 1) {
        sendJSON(socket, {
          type: "status",
          message: "Transmitter is online"
        });
      } else {
        sendJSON(socket, {
          type: "status",
          message: "Waiting for transmitter"
        });
      }

      return;
    }
  });

  socket.on("close", () => {
    console.log("WebSocket client disconnected");

    if (socket === transmitter) {
      transmitter = null;
      console.log("Transmitter disconnected");
      broadcastStatus("Transmitter is offline");
    }

    if (receivers.has(socket)) {
      receivers.delete(socket);
      console.log("Receiver disconnected");
    }
  });

  socket.on("error", (error) => {
    console.log("WebSocket error:", error.message);
  });
});

// Start server
server.listen(PORT, "0.0.0.0", () => {
  console.log(`AudioBridge server running on port ${PORT}`);
  console.log("HTTP server: http://0.0.0.0:" + PORT);
  console.log("WebSocket path: /audio");
});