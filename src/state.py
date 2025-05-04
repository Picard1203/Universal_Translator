# src/state.py

"""
Global shared state for Universal-Translator server.

All modules that modify these globals must acquire `state_lock`.
"""

import asyncio
from asyncio import Lock as AsyncLock
from typing import Any, Dict, Set

# Protects all shared mutable state below
state_lock: AsyncLock = AsyncLock()

# Registered clients:
#   cid → {
#     "nick": str,
#     "lang": str,
#     "rtt_ms": int,
#     "writer": asyncio.StreamWriter,
#     ...
#   }
clients: Dict[str, Dict[str, Any]] = {}

# Currently connected client IDs
active: Set[str] = set()

# Ongoing barrier synchronizations:
#   mid → Latch instance
pending: Dict[str, Any] = {}
