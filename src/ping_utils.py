# src/ping_utils.py

"""
Periodic RTT measurement using WebSocket PING/PONG frames.

This module provides:
  - OPCODE_PING: the WebSocket opcode for PING frames.
  - ping_timestamps: a mapping from ping IDs to send timestamps.
  - send_ping_for_rtt(): send a single PING frame with a unique ID.
  - measure_rtt_periodically(): coroutine to ping all active clients
    at regular intervals and clean up stale entries.
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Tuple

from src.frame_utils import send_frame
from src.state import state_lock, clients, active
from src.config import RTT_MEASUREMENT_INTERVAL_S

# WebSocket PING opcode (RFC 6455)
OPCODE_PING: int = 0x9

# Stores outstanding ping IDs → send timestamps (monotonic time)
ping_timestamps: Dict[str, float] = {}

# How many intervals before considering a ping stale
_STALE_MULTIPLIER: int = 2

logger = logging.getLogger("UT-srv.ping_utils")


async def send_ping_for_rtt(cid: str, writer: asyncio.StreamWriter) -> None:
    """
    Send a PING frame to a single client to measure RTT.

    Args:
        cid:    The client identifier.
        writer: The asyncio StreamWriter for that client's WebSocket.

    Behavior:
        - Generates a unique ping_id.
        - Records the send timestamp (monotonic).
        - Sends the payload "ping_id|timestamp" with OPCODE_PING.
        - If sending fails, the ping_id is removed to avoid leaks.
    """
    if writer.is_closing():
        logger.debug("send_ping_for_rtt: writer for %s is closing — skipping ping", cid)
        return

    ping_id = str(uuid.uuid4())
    send_ts = time.monotonic()
    ping_timestamps[ping_id] = send_ts
    payload = f"{ping_id}|{send_ts}"

    try:
        await send_frame(writer, payload, opcode=OPCODE_PING)
        logger.debug("Sent PING to %s (id=%s)", cid, ping_id)
    except Exception as exc:
        logger.warning("send_ping_for_rtt: failed to send PING to %s: %s", cid, exc)
        # Clean up so stale entries don't accumulate
        ping_timestamps.pop(ping_id, None)


async def measure_rtt_periodically() -> None:
    """
    Periodically ping all active clients to measure and update RTT.

    Loop:
      1. Sleep for RTT_MEASUREMENT_INTERVAL_S seconds.
      2. Collect (cid, writer) for each active client under state_lock.
      3. Send a PING to each client concurrently.
      4. Remove any ping_ids older than RTT_MEASUREMENT_INTERVAL_S * _STALE_MULTIPLIER.

    The actual RTT update occurs when the WebSocket handler processes
    the corresponding PONG frame and updates clients[cid]["rtt_ms"].
    """
    while True:
        await asyncio.sleep(RTT_MEASUREMENT_INTERVAL_S)

        # Gather targets under lock
        async with state_lock:
            targets = [
                (cid, info["writer"])
                for cid, info in clients.items()
                if cid in active
                and "writer" in info
                and not info["writer"].is_closing()
            ]

        if not targets:
            logger.debug("measure_rtt_periodically: no active clients to ping")
            continue

        logger.info("RTT check: pinging %d clients", len(targets))
        # Send all pings concurrently
        send_tasks = [asyncio.create_task(send_ping_for_rtt(cid, w)) for cid, w in targets]
        await asyncio.gather(*send_tasks, return_exceptions=True)

        # Clean up stale ping IDs
        cutoff = time.monotonic() - (RTT_MEASUREMENT_INTERVAL_S * _STALE_MULTIPLIER)
        stale_ids = [pid for pid, ts in ping_timestamps.items() if ts < cutoff]
        for pid in stale_ids:
            ping_timestamps.pop(pid, None)
        if stale_ids:
            logger.debug("measure_rtt_periodically: cleaned %d stale ping IDs", len(stale_ids))
