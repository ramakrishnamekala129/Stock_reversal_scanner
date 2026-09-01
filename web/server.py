import logging
import socket
import threading
import time
from typing import Optional
import webbrowser
import uvicorn

import config

logger = logging.getLogger(__name__)


def is_port_in_use(host: str, port: int) -> bool:
    """Checks if a TCP port is already open/bound on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def find_available_port(host: str, start_port: int = 8000, max_attempts: int = 20) -> int:
    """Finds the next available port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        if not is_port_in_use(host, p):
            return p
    return start_port


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
        if is_port_in_use(self.host, self.port):
            free_port = find_available_port(self.host, self.port + 1)
            logger.warning(f"Port {self.port} in use. Switching to port {free_port}.")
            self.port = free_port
            config.WEB_PORT = free_port

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
