# src/handlers.py

"""
HTTP registration and WebSocket message handling.

- api_register: HTTP POST /api/register endpoint.
- handle_ws_message: Process JSON messages from WebSocket clients.
- broadcast: Send arbitrary JSON to active WebSocket clients.
"""

import json
import logging
from typing import Any, Dict, Optional, Set

from flask import request, abort
import asyncio

from src.config import FIXED_RTT_MS
from src.frame_utils import send_frame
from src.latch import Latch
from src.state import state_lock, clients, active, pending
from src.translation import get_translation

logger = logging.getLogger("UT-srv.handlers")


async def api_register() -> tuple[str, int]:
    """
    Handle HTTP POST /api/register.

    Expects JSON body with keys: "cid", "nick", "lang".

    On success, stores/updates the client record and returns 204 No Content.
    On malformed input, aborts with 400.

    Returns:
        A tuple of (empty body, HTTP status code).
    """
    data: Optional[Dict[str, Any]] = request.get_json(force=True, silent=True)
    expected_keys = {"cid", "nick", "lang"}
    if not data or expected_keys - data.keys():
        logger.error("api_register: malformed request data: %s", data)
        abort(400, "malformed register payload")

    cid: str  = data["cid"]
    nick: str = data["nick"]
    lang: str = data["lang"]

    async with state_lock:
        client = clients.get(cid, {})
        client["nick"]   = nick
        client["lang"]   = lang
        client.setdefault("rtt_ms", FIXED_RTT_MS)
        clients[cid]     = client
        logger.info("api_register: registered client %s (%s) lang=%s rtt=%d",
                    nick, cid, lang, client["rtt_ms"])

    return "", 204


async def handle_ws_message(cid: str, msg_str: str) -> None:
    """
    Process a single JSON‐encoded WebSocket message from client `cid`.

    Supports two message types:
      - "payload": begins a barrier sync for a new message.
      - "stage":   updates barrier stage for an ongoing message.

    Args:
        cid:     The client ID that sent the message.
        msg_str: The raw JSON string received.

    Returns:
        None
    """
    try:
        data = json.loads(msg_str)
    except json.JSONDecodeError:
        logger.warning("handle_ws_message: invalid JSON from %s: %s", cid, msg_str)
        return

    msg_type = data.get("type")
    if msg_type == "payload":
        mid    = data.get("id")
        sender = data.get("cid")
        text   = data.get("text")

        if not mid or sender != cid:
            logger.warning("handle_ws_message: bad payload fields from %s: %s", cid, data)
            return

        # Initialize a new latch if needed
        async with state_lock:
            participants = {other for other in active if other != cid}
            if mid in pending:
                logger.debug("handle_ws_message: payload %s already pending", mid)
                return
            pending[mid] = Latch(data, participants, cid)
            latch = pending[mid]
            logger.info("handle_ws_message: created latch for mid=%s participants=%s", mid, participants)

        if participants:
            await broadcast(data, exclude_cid=cid, specific_targets=participants)
        else:
            # No peers → release immediately
            asyncio.create_task(latch._release("no-participants"))

    elif msg_type == "stage":
        mid   = data.get("mid")
        stage = data.get("stage")
        sender = data.get("cid")

        if not mid or stage is None or sender != cid:
            logger.warning("handle_ws_message: bad stage fields from %s: %s", cid, data)
            return

        async with state_lock:
            latch = pending.get(mid)
        if latch:
            await latch.update_stage(cid, stage)
        else:
            logger.debug("handle_ws_message: no latch found for mid=%s", mid)

    else:
        logger.debug("handle_ws_message: unknown message type '%s' from %s", msg_type, cid)


async def broadcast(
    obj: Dict[str, Any],
    exclude_cid: Optional[str] = None,
    specific_targets: Optional[Set[str]] = None
) -> None:
    """
    Send a JSON-serializable `obj` to active WebSocket clients.

    Args:
        obj:              The object to broadcast (will be json.dumps).
        exclude_cid:      If provided, skip this client ID.
        specific_targets: If provided, send only to this set; otherwise to all `active`.

    Returns:
        None
    """
    message = json.dumps(obj)
    targets = specific_targets if specific_targets is not None else active

    async with state_lock:
        writers = [
            clients[c]["writer"]
            for c in targets
            if c != exclude_cid
               and c in clients
               and "writer" in clients[c]
               and not clients[c]["writer"].is_closing()
        ]

    if not writers:
        logger.debug("broadcast: no valid writers (exclude=%s, specific=%s)",
                     exclude_cid, specific_targets)
        return

    tasks = [asyncio.create_task(send_frame(w, message)) for w in writers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        logger.warning("broadcast: %d failures during send: %s",
                       len(failures), failures)
