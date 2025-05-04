# src/ws_server.py

"""
Asyncio-based WebSocket server for Universal-Translator.

This module implements the low-level WebSocket RFC-6455 handshake and
frame parsing over TLS-wrapped asyncio streams. It handles:
  - WebSocket upgrade handshake (including Sec-WebSocket-Accept).
  - Masked frame unmasking, fragmentation, and control frames.
  - Registration of new clients via the first JSON message.
  - Ping/Pong RTT measurement and update of client rtt_ms.
  - Message dispatch to application handlers and broadcasting join/leave.
  - Graceful cleanup on client disconnect.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import ssl
import struct
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

from src.config import (
    WS_PORT,
    CERT_FILE,
    KEY_FILE,
    GUID,
    HANDSHAKE_BUFFER,
    RECEIVE_BUFFER_SIZE,
    MAX_CONNECTIONS,
)
from src.frame_utils import send_frame
from src.handlers import broadcast, handle_ws_message
from src.latch import Latch
from src.ping_utils import ping_timestamps
from src.state import state_lock, clients, active, pending

logger = logging.getLogger("UT-srv.ws_server")

# WebSocket opcodes (RFC 6455)
OPCODE_CONTINUATION = 0x0
OPCODE_TEXT         = 0x1
OPCODE_CLOSE        = 0x8
OPCODE_PING         = 0x9
OPCODE_PONG         = 0xA


async def handle_websocket_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter
) -> None:
    """
    Handle a single WebSocket client connection.

    1. Perform the TLS-upgraded WebSocket handshake.
    2. Read and unmask incoming frames:
       - Respond to PING with PONG.
       - Update RTT on PONG frames.
       - Handle CLOSE frames by closing the connection.
       - For TEXT frames:
         • First message must be a JSON "register" → add to state, broadcast join.
         • Subsequent messages → pass to application-level handler.
    3. On error or disconnect, clean up client state and broadcast leave.
    """
    peer = writer.get_extra_info("peername")
    url_cid: Optional[str] = None
    registered_cid: Optional[str] = None
    client_nick: Optional[str] = None
    buffer = bytearray()

    try:
        # ─── Handshake ───────────────────────────────────────
        raw = await reader.read(HANDSHAKE_BUFFER)
        if not raw:
            raise ConnectionError("Handshake failed: no data received")
        request = raw.decode("utf-8", errors="ignore")
        first_line = request.split("\r\n", 1)[0]
        match = re.match(r"GET\s+(\S+)\s+HTTP/1\.1", first_line)
        if not match:
            raise ValueError("Handshake failed: invalid HTTP request line")
        path = match.group(1)

        # Parse headers
        headers = {}
        for line in request.split("\r\n")[1:]:
            if not line:
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.lower().strip()] = val.strip()

        # Validate WebSocket upgrade
        if (
            headers.get("upgrade", "").lower() != "websocket"
            or "upgrade" not in headers.get("connection", "").lower()
            or headers.get("sec-websocket-version") != "13"
        ):
            raise ValueError("Handshake failed: not a WebSocket upgrade request")

        key = headers.get("sec-websocket-key")
        if not key:
            raise ValueError("Handshake failed: missing Sec-WebSocket-Key")

        # Extract cid query parameter
        query = urlparse(path).query
        cid_list = parse_qs(query).get("cid", [])
        if not cid_list:
            raise ValueError("Handshake failed: missing 'cid' query parameter")
        url_cid = cid_list[0]

        # Compute and send Sec-WebSocket-Accept
        accept_val = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()
        ).decode()
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()
        logger.info("Handshake complete for cid=%s @ %s", url_cid, peer)

        # ─── Frame Processing Loop ───────────────────────────
        while True:
            chunk = await reader.read(RECEIVE_BUFFER_SIZE)
            if not chunk:
                raise ConnectionError("Connection closed by client")
            buffer.extend(chunk)

            # Process all complete frames in buffer
            while True:
                if len(buffer) < 2:
                    break

                b1, b2 = buffer[0], buffer[1]
                fin = (b1 & 0x80) >> 7
                opcode = b1 & 0x0F
                masked = (b2 & 0x80) >> 7
                length = b2 & 0x7F
                idx = 2

                # Extended payload length
                if length == 126:
                    if len(buffer) < idx + 2:
                        break
                    length = struct.unpack_from("!H", buffer, idx)[0]
                    idx += 2
                elif length == 127:
                    if len(buffer) < idx + 8:
                        break
                    length = struct.unpack_from("!Q", buffer, idx)[0]
                    idx += 8

                if not masked:
                    raise ConnectionError("Protocol violation: client frames must be masked")
                if len(buffer) < idx + 4 + length:
                    break

                mask_key = buffer[idx : idx + 4]
                idx += 4
                payload = buffer[idx : idx + length]
                buffer = buffer[idx + length :]

                # Unmask payload
                data_bytes = bytearray(
                    b ^ mask_key[i % 4] for i, b in enumerate(payload)
                )

                # ── Control Frames ─────────────────────────────
                if opcode == OPCODE_CLOSE:
                    writer.close()
                    await writer.wait_closed()
                    return

                if opcode == OPCODE_PING:
                    # Echo payload in PONG
                    await send_frame(
                        writer,
                        data_bytes.decode("utf-8", errors="ignore"),
                        opcode=OPCODE_PONG
                    )
                    continue

                if opcode == OPCODE_PONG:
                    text = data_bytes.decode("utf-8", errors="ignore")
                    if "|" in text and registered_cid:
                        ping_id, _ = text.split("|", 1)
                        sent = ping_timestamps.pop(ping_id, None)
                        if sent is not None:
                            rtt_ms = int((time.monotonic() - sent) * 1000)
                            async with state_lock:
                                if registered_cid in clients:
                                    clients[registered_cid]["rtt_ms"] = rtt_ms
                            logger.info(
                                "Updated RTT for %s (%s): %d ms",
                                client_nick,
                                registered_cid,
                                rtt_ms
                            )
                    continue

                # ── Data Frames (Text Only) ────────────────────
                if opcode != OPCODE_TEXT:
                    # Ignore non-text data
                    continue

                message = data_bytes.decode("utf-8", errors="ignore")

                # First TEXT must be registration
                if not registered_cid:
                    obj = json.loads(message)
                    if obj.get("type") != "register":
                        raise ConnectionError("First message must be a register")
                    registered_cid = obj["cid"]
                    client_nick = obj["nick"]
                    async with state_lock:
                        client = clients.setdefault(registered_cid, {})
                        client["writer"] = writer
                        active.add(registered_cid)
                    await broadcast(
                        {"type": "join", "nick": client_nick, "cid": registered_cid},
                        exclude_cid=registered_cid
                    )
                else:
                    # Delegate application messages
                    await handle_ws_message(registered_cid, message)

    except Exception as exc:
        logger.info("WebSocket client %s disconnected: %s", peer, exc)

    finally:
        # Cleanup state if registered
        if registered_cid:
            async with state_lock:
                active.discard(registered_cid)
                clients.pop(registered_cid, None)
                # Notify any pending latches
                for latch in list(pending.values()):
                    asyncio.create_task(latch.client_disconnected(registered_cid))
            await broadcast(
                {"type": "leave", "nick": client_nick, "cid": registered_cid},
                exclude_cid=registered_cid
            )
        # Ensure writer is closed
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_websocket_server() -> None:
    """
    Spin up the TLS-wrapped asyncio WebSocket server.

    Binds to 0.0.0.0:WS_PORT with a backlog of MAX_CONNECTIONS and
    uses CERT_FILE/KEY_FILE for TLS.
    """
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(CERT_FILE, KEY_FILE)

    server = await asyncio.start_server(
        handle_websocket_client,
        host="0.0.0.0",
        port=WS_PORT,
        ssl=ssl_ctx,
        backlog=MAX_CONNECTIONS,
    )
    addr = server.sockets[0].getsockname()
    logger.info("WebSocket server listening on wss://%s:%d", addr[0], addr[1])

    async with server:
        await server.serve_forever()
