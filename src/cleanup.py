# src/cleanup.py

"""
Cleanup task for inactive WebSocket clients.

Every CLEANUP_INTERVAL_S seconds, this module removes any client
whose StreamWriter has closed, ensuring that shared state remains clean.
"""

import asyncio
import logging
from typing import Any

from src.state import state_lock, clients, active

# Interval in seconds between cleanup runs
CLEANUP_INTERVAL_S: int = 60

logger = logging.getLogger("UT-srv.cleanup")


async def cleanup_inactive_clients() -> None:
    """
    Periodically remove inactive clients from the global state.

    This coroutine sleeps for CLEANUP_INTERVAL_S seconds, then acquires
    the shared state_lock and checks each client in `active`. If a
    client's writer is missing or closed, it logs the removal and
    deletes the client from both `active` and `clients`.

    Returns:
        None
    """
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_S)
        to_remove: list[str] = []

        async with state_lock:
            for cid in list(active):
                client_info: dict[str, Any] = clients.get(cid, {})
                writer = client_info.get("writer")
                if writer is None or writer.is_closing():
                    logger.info("Cleanup: removing inactive client %s", cid)
                    to_remove.append(cid)

            for cid in to_remove:
                active.discard(cid)
                clients.pop(cid, None)
