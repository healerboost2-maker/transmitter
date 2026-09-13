const http = require("http");
const { WebSocketServer, WebSocket } = require("ws");

const PORT = process.env.PORT || 10000;

const transmitters = new Set();
const receivers = new Set();

const httpServer = http.createServer((request, response) => {
  if (request.url === "/" || request.url === "/health") {
    response.writeHead(200, {
      "Content-Type": "text/plain; charset=utf-8"
    });

    response.end("AudioBridge server is running");
    return;
  }

  response.writeHead(404, {
    "Content-Type": "text/plain; charset=utf-8"
  });

  response.end("Not found");
});

const websocketServer = new WebSocketServer({
  server: httpServer,
  path: "/audio"
});

websocketServer.on("connection", (socket, request) => {
  let clientRole = null;

  console.log(
    "WebSocket client connected:",
    request.socket.remoteAddress
  );

  sendStatus(socket, "Connected to AudioBridge server");

  socket.on("message", (data, isBinary) => {
    /*
     * Binary messages are raw PCM audio.
     */
    if (isBinary) {
      if (clientRole !== "transmitter") {
        console.log(
          "Rejected audio packet from unregistered client"
        );

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
              "Audio forwarding error:",
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
      console.log("Invalid JSON message received");
      return;
    }

    if (!message || typeof message.type !== "string") {
      console.log("Invalid control message");
      return;
    }

    /*
     * Transmitter registration.
     *
     * Supports both:
     * {"type":"register-transmitter"}
     *
     * and the older format:
     * {"type":"register","role":"transmitter"}
     */
    const isTransmitter =
      message.type === "register-transmitter" ||
      (
        message.type === "register" &&
        message.role === "transmitter"
      );

    if (isTransmitter) {
      removeClientFromGroups(socket);

      clientRole = "transmitter";
      transmitters.add(socket);

      console.log(
        `Transmitter registered. Total transmitters: ${
          transmitters.size
        }`
      );

      sendStatus(socket, "Transmitter registered");
      broadcastStatus();

      return;
    }

    /*
     * Receiver registration.
     *
     * Supports both:
     * {"type":"register-receiver"}
     *
     * and:
     * {"type":"register","role":"receiver"}
     */
    const isReceiver =
      message.type === "register-receiver" ||
      (
        message.type === "register" &&
        message.role === "receiver"
      );

    if (isReceiver) {
      removeClientFromGroups(socket);

      clientRole = "receiver";
      receivers.add(socket);

      console.log(
        `Receiver registered. Total receivers: ${
          receivers.size
        }`
      );

      sendStatus(socket, "Receiver registered");
      broadcastStatus();

      return;
    }

    /*
     * Application-level ping.
     */
    if (message.type === "ping") {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify({
            type: "pong",
            timestamp: Date.now()
          })
        );
      }

      return;
    }

    console.log(
      "Unknown message type:",
      message.type
    );
  });

  socket.on("close", (code, reason) => {
    transmitters.delete(socket);
    receivers.delete(socket);

    console.log(
      `${clientRole || "Unknown client"} disconnected. ` +
      `Code: ${code}. ` +
      `Reason: ${reason.toString()}`
    );

    broadcastStatus();
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
        message: message,
        status: message,
        transmitters: transmitters.size,
        receivers: receivers.size,
        timestamp: Date.now()
      })
    );
  } catch (error) {
    console.error(
      "Status sending error:",
      error.message
    );
  }
}

function broadcastStatus() {
  const statusMessage = JSON.stringify({
    type: "server-status",
    message: "AudioBridge server status",
    status: "online",
    transmitters: transmitters.size,
    receivers: receivers.size,
    timestamp: Date.now()
  });

  for (const socket of [
    ...transmitters,
    ...receivers
  ]) {
    if (socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(statusMessage);
      } catch (error) {
        console.error(
          "Broadcast error:",
          error.message
        );
      }
    }
  }
}

function removeClientFromGroups(socket) {
  transmitters.delete(socket);
  receivers.delete(socket);
}

httpServer.listen(PORT, "0.0.0.0", () => {
  console.log(
    `AudioBridge server listening on port ${PORT}`
  );

  console.log(
    "WebSocket endpoint: /audio"
  );
});