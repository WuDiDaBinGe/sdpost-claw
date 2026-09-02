"""Desktop Application - pywebview wrapper for sdpost-claw."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to web UI
WEB_UI_PATH = Path(__file__).parent / "web" / "index.html"


class DesktopApp:
    """
    Desktop Application - wraps the web UI in a native window.

    Features:
    - Native desktop window (via pywebview)
    - Built-in HTTP server (Sidecar Server)
    - Session management
    - Real-time chat interface
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        title: str = "sdpost-claw",
        width: int = 1200,
        height: int = 800,
    ):
        self.host = host
        self.port = port
        self.title = title
        self.width = width
        self.height = height
        self._server = None
        self._window = None

    def start(self) -> None:
        """Start the desktop application."""
        import webview

        # Wait briefly for the HTTP server to be ready
        time.sleep(1.5)

        # Create window pointing at the local server
        url = f"http://{self.host}:{self.port}/"
        self._window = webview.create_window(
            self.title,
            url,
            width=self.width,
            height=self.height,
            min_size=(800, 600),
            text_select=True,
        )

        # Start webview
        webview.start(debug=False)

    def start_with_server(self, session_manager=None) -> None:
        """Start with built-in HTTP server."""
        from sdpost_claw.runtime.server import SidecarServer

        # Start server in background thread
        self._server = SidecarServer(
            session_manager=session_manager,
            host=self.host,
            port=self.port,
        )

        def run_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._server.start())
            loop.run_forever()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Start desktop UI
        self.start()


def launch() -> None:
    """Launch the desktop application."""
    app = DesktopApp()
    app.start()
