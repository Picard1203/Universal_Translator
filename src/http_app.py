# src/http_app.py

"""
HTTP application for serving the chat UI and registration endpoint.

Uses Flask to serve static assets and HTML templates, plus a POST API
to register new chat clients before they connect via WebSocket.
"""

import os
from pathlib import Path
from flask import Flask, send_from_directory, Response, abort, request

from src.handlers import api_register

# Compute project root: one level above src/
BASE_DIR: Path = Path(__file__).resolve().parent.parent
STATIC_FOLDER: Path = BASE_DIR / "static"
TEMPLATE_FOLDER: Path = BASE_DIR / "templates"

app: Flask = Flask(
    __name__,
    static_folder=str(STATIC_FOLDER),
    template_folder=str(TEMPLATE_FOLDER),
)


@app.route("/", methods=["GET"])
def index() -> Response:
    """
    Serve the login page.

    Returns:
        The rendered `index.html` from the templates directory.
    """
    return send_from_directory(app.template_folder, "index.html")


@app.route("/chat.html", methods=["GET"])
def chat() -> Response:
    """
    Serve the chat UI page.

    Returns:
        The rendered `chat.html` from the templates directory.
    """
    return send_from_directory(app.template_folder, "chat.html")


@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename: str) -> Response:
    """
    Serve static assets (JS, CSS, images).

    Args:
        filename: Relative path under the `static/` directory.

    Returns:
        The requested file from the static folder.

    Raises:
        404 if the file does not exist.
    """
    static_path = STATIC_FOLDER / filename
    if not static_path.exists():
        abort(404)
    return send_from_directory(app.static_folder, filename)


# HTTP API endpoint for client registration
# Expects JSON {"cid":..., "nick":..., "lang":...}
app.add_url_rule(
    "/api/register",
    endpoint="api_register",
    view_func=api_register,
    methods=["POST"],
)
