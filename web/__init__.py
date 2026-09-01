"""
FastAPI Web Dashboard Package.
"""

from web.server import WebServerManager
from web.state import dashboard_state

__all__ = ["WebServerManager", "dashboard_state"]
