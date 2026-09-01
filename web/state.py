"""
Thread-safe shared state for FastAPI Web Dashboard.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

IST_TZ = timezone(timedelta(hours=5, minutes=30))


class WebDashboardState:
    """Singleton-style state manager for the web dashboard."""

    def __init__(self):
        self._lock = threading.Lock()
        self.pivots: Dict[str, dict] = {}
        self.live_prices: Dict[str, dict] = {}
        self.signals: List[dict] = []
        self.stats: Dict[str, Any] = {
            "symbols_scanned": 0,
            "candles_processed": 0,
            "patterns_detected": 0,
            "bullish_signals": 0,
            "bearish_signals": 0,
            "ws_status": "CONNECTING",
            "last_updated": "--",
        }
        self.active_websockets: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def initialize_pivots(self, pivots_map: Dict[str, Any]):
        """Populates initial daily pivot records and baseline prices."""
        now_ist = datetime.now(IST_TZ).strftime("%H:%M:%S")
        with self._lock:
            for sym, p in pivots_map.items():
                p_dict = p.to_dict() if hasattr(p, "to_dict") else dict(p)
                self.pivots[sym] = p_dict
                pdc = p_dict.get("pdc", 0.0)
                self.live_prices[sym] = {
                    "symbol": sym,
                    "ltp": pdc,
                    "change_pct": 0.0,
                    "volume": p_dict.get("pdv", 0),
                    "time": now_ist,
                }
            self.stats["symbols_scanned"] = len(self.pivots)

    def update_price(self, symbol: str, ltp: float, volume: int = 0, timestamp: Optional[datetime] = None):
        """Updates live price for a symbol and broadcasts to WebSockets in IST."""
        if timestamp:
            if timestamp.tzinfo is None:
                ts_ist = timestamp.replace(tzinfo=IST_TZ)
            else:
                ts_ist = timestamp.astimezone(IST_TZ)
            time_str = ts_ist.strftime("%H:%M:%S")
        else:
            time_str = datetime.now(IST_TZ).strftime("%H:%M:%S")

        with self._lock:
            p_data = self.pivots.get(symbol, {})
            pdc = p_data.get("pdc", ltp)
            change_pct = ((ltp - pdc) / pdc) * 100.0 if pdc > 0 else 0.0

            update_payload = {
                "symbol": symbol,
                "ltp": round(ltp, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "time": time_str,
            }
            self.live_prices[symbol] = update_payload
            self.stats["last_updated"] = time_str

        self._broadcast({"type": "PRICE_UPDATE", "data": update_payload})

    def add_signal(self, signal: Any):
        """Appends a new detected signal and broadcasts to WebSockets."""
        sig_dict = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
        # Format signal timestamp in IST if it is a datetime object
        ts_val = sig_dict.get("timestamp")
        if isinstance(ts_val, datetime):
            if ts_val.tzinfo is None:
                ts_val = ts_val.replace(tzinfo=IST_TZ)
            else:
                ts_val = ts_val.astimezone(IST_TZ)
            sig_dict["timestamp"] = ts_val.strftime("%H:%M:%S")

        with self._lock:
            self.signals.insert(0, sig_dict)  # Newest first
            if "BULLISH" in sig_dict.get("direction", ""):
                self.stats["bullish_signals"] += 1
            elif "BEARISH" in sig_dict.get("direction", ""):
                self.stats["bearish_signals"] += 1
            self.stats["patterns_detected"] = len(self.signals)

        self._broadcast({
            "type": "NEW_SIGNAL",
            "data": sig_dict,
            "stats": self.get_stats(),
        })

    def update_stats(self, **kwargs):
        """Updates session metadata."""
        with self._lock:
            self.stats.update(kwargs)
        self._broadcast({"type": "STATS_UPDATE", "stats": self.get_stats()})

    def get_snapshot(self) -> dict:
        """Returns complete state snapshot for initial client render."""
        with self._lock:
            # Combine pivots and live prices
            market_data = []
            for sym, p in sorted(self.pivots.items()):
                lp = self.live_prices.get(sym, {})
                market_data.append({
                    "symbol": sym,
                    "ltp": lp.get("ltp", p.get("pdc", 0.0)),
                    "change_pct": lp.get("change_pct", 0.0),
                    "volume": lp.get("volume", p.get("pdv", 0)),
                    "time": lp.get("time", "--"),
                    "pdo": p.get("pdo", 0.0),
                    "pdh": p.get("pdh", 0.0),
                    "pdl": p.get("pdl", 0.0),
                    "pdc": p.get("pdc", 0.0),
                    "pdv": p.get("pdv", 0),
                    "pp": p.get("pp") or p.get("pivot", 0.0),
                    "r1": p.get("r1", 0.0),
                    "r2": p.get("r2", 0.0),
                    "r3": p.get("r3", 0.0),
                    "s1": p.get("s1", 0.0),
                    "s2": p.get("s2", 0.0),
                    "s3": p.get("s3", 0.0),
                })

            return {
                "market": market_data,
                "signals": list(self.signals),
                "stats": dict(self.stats),
            }

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self.stats)

    def register_ws(self, ws: WebSocket):
        with self._lock:
            self.active_websockets.add(ws)

    def unregister_ws(self, ws: WebSocket):
        with self._lock:
            self.active_websockets.discard(ws)

    def _broadcast(self, message: dict):
        """Dispatches JSON message across all active WebSocket connections."""
        if not self._loop or not self.active_websockets:
            return

        msg_str = json.dumps(message, default=str)

        async def _send_all():
            dead = set()
            with self._lock:
                sockets = list(self.active_websockets)
            for ws in sockets:
                try:
                    await ws.send_text(msg_str)
                except Exception:
                    dead.add(ws)
            if dead:
                with self._lock:
                    for d in dead:
                        self.active_websockets.discard(d)

        asyncio.run_coroutine_threadsafe(_send_all(), self._loop)


# Global dashboard state singleton
dashboard_state = WebDashboardState()
