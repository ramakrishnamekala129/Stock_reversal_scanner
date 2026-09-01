"""
FastAPI Server Runner.
Runs Uvicorn server in a background thread and opens the browser.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
import uvicorn

import config

logger = logging.getLogger(__name__)


class WebServerManager:
    """Manages the background FastAPI/Uvicorn server lifecycle."""

    def __init__(self, host: str = config.WEB_HOST, port: int = config.WEB_PORT, auto_open: bool = config.WEB_AUTO_OPEN):
        self.host = host
        self.port = port
        self.auto_open = auto_open
        self.server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts Uvicorn server in a background thread."""
        server_config = uvicorn.Config(
            "web.app:app",
            host=self.host,
            port=self.port,
            log_level="warning",
            loop="asyncio",
        )
        self.server = uvicorn.Server(server_config)

        self._thread = threading.Thread(target=self.server.run, daemon=True)
        self._thread.start()
        logger.info(f"FastAPI Web Dashboard running at: http://{self.host}:{self.port}")

        if self.auto_open:
            threading.Thread(target=self._open_browser_delayed, daemon=True).start()

    def _open_browser_delayed(self):
        time.sleep(1.2)
        try:
            webbrowser.open(f"http://{self.host}:{self.port}")
        except Exception as e:
            logger.debug(f"Could not auto-open browser: {e}")

    def stop(self):
        """Stops the Uvicorn server."""
        if self.server:
            self.server.should_exit = True
