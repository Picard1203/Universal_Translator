#!/usr/bin/env python3
import asyncio
import logging

from src.config import HTTP_PORT, CERT_FILE, KEY_FILE, LOG_LEVEL, ensure_cert
from src.http_app import app as flask_app
from src.ws_server import start_websocket_server
from src.ping_utils import measure_rtt_periodically
from src.cleanup import cleanup_inactive_clients

import hypercorn.config
from hypercorn.asyncio import serve

# Import the internal loader to preload both directions
from transformer_module.async_translator import _load_model_pair

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("UT-srv")

async def main():
    # 1) Ensure certificates
    ensure_cert()

    # 2) Preload both translation models (zero-hit on first call)
    log.info("Preloading EN→HE and HE→EN translation models…")
    await asyncio.gather(
        asyncio.to_thread(_load_model_pair, "en", "he"),
        asyncio.to_thread(_load_model_pair, "he", "en"),
    )
    log.info("Translation models loaded successfully.")

    # 3) Start background tasks
    ws_task = asyncio.create_task(start_websocket_server(), name="WebSocketServer")
    cleanup_task = asyncio.create_task(cleanup_inactive_clients(), name="CleanupTask")
    rtt_task = asyncio.create_task(measure_rtt_periodically(), name="RTTMeasureTask")

    # 4) Configure Hypercorn for HTTPS + static REST
    cfg = hypercorn.config.Config()
    cfg.bind = [f"0.0.0.0:{HTTP_PORT}"]
    cfg.certfile = CERT_FILE
    cfg.keyfile = KEY_FILE
    cfg.use_reloader = False
    cfg.loglevel = LOG_LEVEL.lower()

    log.info(f"Starting HTTPS+WSS on ports HTTPS={HTTP_PORT}, WSS via same certs")
    try:
        await serve(flask_app, cfg, shutdown_trigger=lambda: asyncio.Future())
    finally:
        log.info("Shutting down background tasks…")
        for t in (ws_task, cleanup_task, rtt_task):
            t.cancel()
        await asyncio.gather(ws_task, cleanup_task, rtt_task, return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped by user")
