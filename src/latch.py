# src/latch.py

"""
Barrier synchronization primitive for chat message release.

Clients participate in a multi-stage handshake before a message is
released to display simultaneously. This class tracks stages and,
upon readiness or disconnect, broadcasts a 'release' with per-client
delays and optional translations.
"""

import asyncio
import copy
import json
import logging
from typing import Any, Dict, Set

from src.frame_utils import send_frame
from src.state import state_lock, clients, active, pending
from src.translation import get_translation

logger = logging.getLogger("UT-srv.latch")


class Latch:
    """
    Coordinate a single chat message across multiple clients.

    Attributes:
        payload:       Original JSON payload dict for the message.
        mid:           Unique message ID.
        sender_cid:    Client ID of the sender.
        participants:  IDs of other clients to synchronize.
        status:        Mapping cid → current stage index.
        lock:          Protects internal state.
        released:      Event set when release has been performed.
    """

    STAGES: list[str] = [
        "waiting",
        "sent",
        "received",
        "integrity_check",
        "ready_to_display",
    ]

    def __init__(self, payload: Dict[str, Any], participants: Set[str], sender_cid: str):
        """
        Initialize a Latch for a new message.

        Args:
            payload:      The full message data (must include 'id').
            participants: Set of other client IDs to wait on.
            sender_cid:   ID of the client who sent the message.
        """
        self.payload: Dict[str, Any] = payload
        self.mid: str = payload["id"]
        self.sender_cid: str = sender_cid
        self.participants: Set[str] = set(participants)
        # Initialize all stages to 0 (waiting); sender is at stage 1 (sent)
        self.status: Dict[str, int] = {cid: 0 for cid in participants}
        self.status[sender_cid] = 1
        self.lock: asyncio.Lock = asyncio.Lock()
        self.released: asyncio.Event = asyncio.Event()

        logger.debug(
            "Latch created mid=%s sender=%s participants=%s",
            self.mid, self.sender_cid, self.participants,
        )

    async def update_stage(self, cid: str, stage: int) -> bool:
        """
        Update a client's stage and trigger release if all are ready.

        Args:
            cid:   Client ID updating its stage.
            stage: New stage index (0 <= stage < len(STAGES)).

        Returns:
            True if the latch has been (or is being) released, False otherwise.
        """
        async with self.lock:
            if self.released.is_set():
                return True

            if cid not in self.status:
                logger.warning("Unexpected stage update from %s for mid=%s", cid, self.mid)
                return False

            current = self.status[cid]
            if not (0 <= stage < len(self.STAGES)) or stage <= current:
                logger.debug("Ignoring invalid/duplicate stage %d→%d for %s", current, stage, cid)
                return False

            self.status[cid] = stage
            logger.debug("Latch mid=%s: %s → stage %d", self.mid, cid, stage)

            # Check release condition:
            all_present = set(self.status.keys()) == self.participants | {self.sender_cid}
            receivers_ready = all(self.status[p] == len(self.STAGES) - 1 for p in self.participants)
            sender_sent = self.status[self.sender_cid] >= 1

            if all_present and sender_sent and receivers_ready:
                logger.info("Latch mid=%s: all clients at final stage, releasing", self.mid)
                # Release asynchronously to avoid blocking callers
                asyncio.create_task(self._release("all-ready"))
                return True

            return False

    async def _release(self, reason: str) -> None:
        """
        Perform the message release: compute per-client delays, translations,
        and send a 'release' frame to each active participant + sender.

        Args:
            reason: Textual reason for release (e.g., "all-ready").
        """
        async with self.lock:
            if self.released.is_set():
                return
            self.released.set()

        logger.info("Latch._release mid=%s reason=%s", self.mid, reason)

        # Collect writers & RTTs
        async with state_lock:
            group = self.participants | {self.sender_cid}
            infos: list[tuple[str, asyncio.StreamWriter, int]] = []
            max_rtt = 0

            for cid in group:
                info = clients.get(cid, {})
                writer = info.get("writer")
                rtt = info.get("rtt_ms", 0)
                if cid in active and writer and not writer.is_closing():
                    infos.append((cid, writer, rtt))
                    max_rtt = max(max_rtt, rtt)

            pending.pop(self.mid, None)

        tasks = []
        for cid, writer, rtt in infos:
            payload = copy.deepcopy(self.payload)
            payload["type"] = "release"
            payload["delay"] = max_rtt - rtt
            payload["final_status"] = self.status

            # Optional translation
            src = payload.get("senderLang") or payload.get("lang")
            text = payload.get("text")
            tgt = clients.get(cid, {}).get("lang")
            if src and text and tgt and src != tgt:
                logger.debug("Translating mid=%s for %s: %s→%s", self.mid, cid, src, tgt)
                tr = await get_translation(text, src, tgt)
                payload.setdefault("translations", {})[tgt] = tr

            tasks.append(asyncio.create_task(send_frame(writer, json.dumps(payload))))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [e for e in results if isinstance(e, Exception)]
            if failures:
                logger.warning(
                    "Latch._release mid=%s: %d send failures", self.mid, len(failures)
                )

    async def client_disconnected(self, cid: str) -> None:
        """
        Handle a participant or sender disconnecting before release.

        If the sender disconnects, release immediately. If a receiver
        disconnects, remove them and release if the remaining set is ready.
        """
        async with self.lock:
            if self.released.is_set():
                return

            if cid == self.sender_cid:
                logger.warning("Latch mid=%s: sender disconnected, forcing release", self.mid)
                asyncio.create_task(self._release("sender-disconnected"))
                return

            if cid in self.participants:
                logger.info("Latch mid=%s: participant %s disconnected", self.mid, cid)
                self.participants.remove(cid)
                self.status.pop(cid, None)

                # Check if remaining receivers are ready
                all_present = set(self.status.keys()) == self.participants | {self.sender_cid}
                receivers_ready = all(self.status[p] == len(self.STAGES) - 1 for p in self.participants)
                sender_sent = self.status.get(self.sender_cid, 0) >= 1

                if all_present and sender_sent and receivers_ready:
                    logger.info("Latch mid=%s: remaining clients ready after disconnect, releasing", self.mid)
                    asyncio.create_task(self._release("disconnect-and-ready"))
