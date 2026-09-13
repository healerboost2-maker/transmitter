const http = require("http");
const WebSocket = require("ws");

const PORT = process.env.PORT || 8080;

const server = http.createServer((request, response) => {
  response.writeHead(200, {
    "Content-Type": "application/json; charset=utf-8"
  });

  response.end(
    JSON.stringify({
      service: "AudioBridge",
      status: "online",
      websocket: "/audio"
    })
  );
});

const websocketServer = new WebSocket.Server({
  server,
  path: "/audio"
});

let transmitter = null;
const receivers = new Set();

function isOpen(socket) {
  return socket && socket.readyState === WebSocket.OPEN;
}

function getStatus() {
  return {
    type: "status",
    transmitterConnected: isOpen(transmitter),
    transmitterName: transmitter?.name || null,
    receiverCount: receivers.size
  };
}

function sendJSON(socket, data) {
  if (isOpen(socket)) {
    socket.send(JSON.stringify(data));
  }
}

function broadcastStatus() {
  const status = getStatus();

  sendJSON(transmitter, status);

  for (const receiver of receivers) {
    sendJSON(receiver, status);
  }
}

websocketServer.on("connection", (socket, request) => {
  console.log("WebSocket client connected:", request.socket.remoteAddress);

  socket.role = null;
  socket.name = null;

  socket.on("message", (message, isBinary) => {
    if (isBinary) {
      if (socket.role !== "transmitter") {
        return;
      }

      for (const receiver of receivers) {
        if (isOpen(receiver)) {
          receiver.send(message, {
            binary: true
          });
        }
      }

      return;
    }

    let data;

    try {
      data = JSON.parse(message.toString());
    } catch {
      sendJSON(socket, {
        type: "error",
        message: "Invalid JSON message."
      });

      return;
    }

    if (data.type === "register-transmitter") {
      if (isOpen(transmitter) && transmitter !== socket) {
        sendJSON(transmitter, {
          type: "status",
          message: "This transmitter was replaced by another transmitter."
        });

        transmitter.close();
      }

      transmitter = socket;
      socket.role = "transmitter";
      socket.name = data.name || "Main Radio Feed";

      sendJSON(socket, {
        type: "registered",
        role: "transmitter",
        name: socket.name,
        message: "Transmitter registered successfully."
      });

      console.log("Transmitter registered:", socket.name);
      broadcastStatus();
      return;
    }

    if (data.type === "register-receiver") {
      socket.role = "receiver";
      receivers.add(socket);

      sendJSON(socket, {
        type: "registered",
        role: "receiver",
        message: "Receiver registered successfully."
      });

      console.log("Receiver registered. Total:", receivers.size);
      broadcastStatus();
      return;
    }

    if (data.type === "get-status") {
      sendJSON(socket, getStatus());
      return;
    }

    if (data.type === "transmitter-info") {
      if (socket === transmitter) {
        socket.name = data.name || "Main Radio Feed";
        broadcastStatus();
      }
    }
  });

  socket.on("close", () => {
    if (socket === transmitter) {
      transmitter = null;
      console.log("Transmitter disconnected.");
    }

    if (receivers.has(socket)) {
      receivers.delete(socket);
      console.log("Receiver disconnected. Total:", receivers.size);
    }

    broadcastStatus();
  });

  socket.on("error", (error) => {
    console.error("WebSocket error:", error.message);
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`AudioBridge server running on port ${PORT}`);
  console.log(`HTTP status: http://0.0.0.0:${PORT}`);
  console.log(`WebSocket path: /audio`);
});