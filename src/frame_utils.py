# src/frame_utils.py

"""
WebSocket framing utilities.

This module provides functions to construct and send WebSocket frames
over an asyncio StreamWriter, handling text messages and optional
fragmentation for large payloads.
"""

import asyncio
import logging
from typing import Final

from src.config import MAX_FRAME_PAYLOAD_SIZE

# WebSocket opcodes
OPCODE_CONTINUATION: Final[int] = 0x0
OPCODE_TEXT: Final[int]         = 0x1

# Payload length thresholds
_PAYLOAD_SMALL_MAX: Final[int]  = 125
_PAYLOAD_MEDIUM_MAX: Final[int] = 65_535

logger = logging.getLogger("UT-srv.frame_utils")


async def send_frame(
    writer: asyncio.StreamWriter,
    text: str,
    opcode: int = OPCODE_TEXT
) -> None:
    """
    Send a WebSocket frame (or fragmented sequence) containing `text`.

    Args:
        writer: The asyncio StreamWriter to send the frame on.
        text:    The UTF-8 string payload to send.
        opcode:  The initial WebSocket opcode (TEXT or CONTROL). Subsequent
                 fragments will use OPCODE_CONTINUATION.

    Raises:
        ConnectionResetError: If the writer is closing or a send fails.
        TypeError:            If `text` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError(f"send_frame: expected str payload, got {type(text).__name__}")

    if writer.is_closing():
        logger.error("send_frame: writer is already closing")
        raise ConnectionResetError("Cannot send: writer is closing")

    data = text.encode("utf-8")
    total_length = len(data)
    cursor = 0

    while cursor < total_length:
        chunk_end = min(cursor + MAX_FRAME_PAYLOAD_SIZE, total_length)
        chunk = data[cursor:chunk_end]
        fin_bit = 0x80 if chunk_end == total_length else 0x00
        current_opcode = opcode if cursor == 0 else OPCODE_CONTINUATION

        # Build frame header
        header = bytearray()
        header.append(fin_bit | current_opcode)
        length = len(chunk)

        if length <= _PAYLOAD_SMALL_MAX:
            header.append(length)
        elif length <= _PAYLOAD_MEDIUM_MAX:
            header.append(126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, "big"))

        try:
            writer.write(header + chunk)
            await writer.drain()
            logger.debug(
                "send_frame: sent fragment FIN=%s OPCODE=0x%X LEN=%d",
                bool(fin_bit), current_opcode, length
            )
        except Exception as err:
            logger.warning("send_frame: failure during write/drain: %s", err)
            raise ConnectionResetError(f"send_frame failed: {err}") from err

        cursor = chunk_end
