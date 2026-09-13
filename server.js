const http = require("http");
const { WebSocketServer, WebSocket } = require("ws");

const PORT = process.env.PORT || 10000;

const transmitters = new Set();
const receivers = new Set();

const httpServer = http.createServer((req, res) => {
  if (req.url === "/" || req.url === "/health") {
    res.writeHead(200, {
      "Content-Type": "text/plain; charset=utf-8"
    });

    res.end("AudioBridge server is running");
    return;
  }

  res.writeHead(404, {
    "Content-Type": "text/plain; charset=utf-8"
  });

  res.end("Not found");
});

const websocketServer = new WebSocketServer({
  server: httpServer,
  path: "/audio"
});

websocketServer.on("connection", (socket, request) => {
  let clientRole = null;

  const clientAddress =
    request.socket.remoteAddress || "unknown address";

  console.log(`WebSocket client connected: ${clientAddress}`);

  sendStatus(socket, "Connected to AudioBridge server");

  socket.on("message", (data, isBinary) => {
    /*
     * Binary messages are raw PCM audio.
     * Only registered transmitters are allowed to send audio.
     */
    if (isBinary) {
      if (clientRole !== "transmitter") {
        console.log("Rejected binary data from unregistered client");
        return;
      }

      let forwardedCount = 0;

      for (const receiver of receivers) {
        if (receiver.readyState === WebSocket.OPEN) {
          try {
            receiver.send(data, {
              binary: true
            });

            forwardedCount++;
          } catch (error) {
            console.error(
              "Error forwarding audio:",
              error.message
            );
          }
        }
      }

      console.log(
        `Audio packet received: ${data.length} bytes; ` +
        `forwarded to ${forwardedCount} receiver(s)`
      );

      return;
    }

    /*
     * Text messages are JSON control messages.
     */
    let message;

    try {
      message = JSON.parse(data.toString());
    } catch (error) {
      console.log("Received invalid JSON control message");
      return;
    }

    if (!message || typeof message.type !== "string") {
      console.log("Received control message without a type");
      return;
    }

    /*
     * Register transmitter.
     */
    if (
      message.type === "register-transmitter" ||
      message.type === "transmitter"
    ) {
      removeFromAllSets(socket);

      clientRole = "transmitter";
      transmitters.add(socket);

      console.log("Transmitter registered");

      sendStatus(socket, "Transmitter registered");

      broadcastServerStatus();
      return;
    }

    /*
     * Register receiver.
     */
    if (
      message.type === "register-receiver" ||
      message.type === "receiver"
    ) {
      removeFromAllSets(socket);

      clientRole = "receiver";
      receivers.add(socket);

      console.log(
        `Receiver registered. Total receivers: ${receivers.size}`
      );

      sendStatus(socket, "Receiver registered");

      broadcastServerStatus();
      return;
    }

    /*
     * Optional ping/pong application message.
     */
    if (message.type === "ping") {
      socket.send(
        JSON.stringify({
          type: "pong",
          timestamp: Date.now()
        })
      );

      return;
    }

    console.log("Unknown message type:", message.type);
  });

  socket.on("close", (code, reason) => {
    transmitters.delete(socket);
    receivers.delete(socket);

    console.log(
      `${clientRole || "Unknown client"} disconnected. ` +
      `Code: ${code}. ` +
      `Reason: ${reason.toString()}`
    );

    broadcastServerStatus();
  });

  socket.on("error", (error) => {
    console.error(
      `${clientRole || "Unknown client"} WebSocket error:`,
      error.message
    );
  });
});

function sendStatus(socket, message) {
  if (socket.readyState !== WebSocket.OPEN) {
    return;
  }

  try {
    socket.send(
      JSON.stringify({
        type: "status",
        message,
        status: message,
        transmitters: transmitters.size,
        receivers: receivers.size,
        timestamp: Date.now()
      })
    );
  } catch (error) {
    console.error("Unable to send status:", error.message);
  }
}

function broadcastServerStatus() {
  const statusMessage = {
    type: "server-status",
    message: "AudioBridge server status",
    status: "online",
    transmitters: transmitters.size,
    receivers: receivers.size,
    timestamp: Date.now()
  };

  const encodedMessage = JSON.stringify(statusMessage);

  for (const socket of [
    ...transmitters,
    ...receivers
  ]) {
    if (socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(encodedMessage);
      } catch (error) {
        console.error(
          "Unable to broadcast server status:",
          error.message
        );
      }
    }
  }
}

function removeFromAllSets(socket) {
  transmitters.delete(socket);
  receivers.delete(socket);
}

httpServer.listen(PORT, "0.0.0.0", () => {
  console.log(
    `AudioBridge HTTP server listening on port ${PORT}`
  );

  console.log(
    `WebSocket endpoint available at /audio`
  );
});