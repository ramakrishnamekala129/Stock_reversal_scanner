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
        # Format signal timestamp in IST as HH:MM:SS
        ts_val = sig_dict.get("timestamp")
        if isinstance(ts_val, datetime):
            if ts_val.tzinfo is None:
                ts_val = ts_val.replace(tzinfo=IST_TZ)
            else:
                ts_val = ts_val.astimezone(IST_TZ)
            sig_dict["timestamp"] = ts_val.strftime("%H:%M:%S")
        elif isinstance(ts_val, str) and "T" in ts_val:
            try:
                dt = datetime.fromisoformat(ts_val)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(IST_TZ)
                sig_dict["timestamp"] = dt.strftime("%H:%M:%S")
            except Exception:
                pass

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
                ltp = lp.get("ltp", p.get("pdc", 0.0))
                r3 = p.get("r3", 0.0)
                r2 = p.get("r2", 0.0)
                r1 = p.get("r1", 0.0)
                pp = p.get("pp") or p.get("pivot", 0.0)
                s1 = p.get("s1", 0.0)
                s2 = p.get("s2", 0.0)
                s3 = p.get("s3", 0.0)

                tc = p.get("tc", pp)
                bc = p.get("bc", pp)
                cpr_top = p.get("cpr_top", max(tc, bc))
                cpr_bottom = p.get("cpr_bottom", min(tc, bc))
                cpr_width_pct = p.get("cpr_width_pct", 0.0)

                pdh = p.get("pdh", 0.0)
                pdl = p.get("pdl", 0.0)

                bull_trap_top = p.get("bull_trap_top", max(r1, pdh) if (r1 and pdh) else 0.0)
                bull_trap_bottom = p.get("bull_trap_bottom", min(r1, pdh) if (r1 and pdh) else 0.0)

                bear_trap_top = p.get("bear_trap_top", max(s1, pdl) if (s1 and pdl) else 0.0)
                bear_trap_bottom = p.get("bear_trap_bottom", min(s1, pdl) if (s1 and pdl) else 0.0)

                zone = "PP - R1 (Bullish Territory)"
                if bull_trap_bottom > 0 and bull_trap_bottom <= ltp <= bull_trap_top:
                    zone = "Bull Trap Zone (R1 - PDH)"
                elif bear_trap_bottom > 0 and bear_trap_bottom <= ltp <= bear_trap_top:
                    zone = "Bear Trap Zone (S1 - PDL)"
                elif cpr_bottom > 0 and cpr_bottom <= ltp <= cpr_top:
                    zone = "Inside CPR Zone (Choppy / Base)"
                elif r3 > 0 and ltp >= r3:
                    zone = "Above R3 (Super Breakout)"
                elif r2 > 0 and ltp >= r2:
                    zone = "R2 - R3 (Bullish Extension)"
                elif bull_trap_top > 0 and ltp > bull_trap_top:
                    zone = "Above R1/PDH (Strong Bullish)"
                elif bear_trap_bottom > 0 and ltp < bear_trap_bottom:
                    zone = "Below S1/PDL (Strong Breakdown)"
                elif pp > 0 and ltp >= pp:
                    zone = "PP - R1 (Bullish Territory)"
                elif s2 > 0 and ltp <= s2:
                    zone = "Below S2 (Oversold / Crash)"
                else:
                    zone = "S1 - PP (Support / Retest)"

                market_data.append({
                    "symbol": sym,
                    "ltp": ltp,
                    "change_pct": lp.get("change_pct", 0.0),
                    "volume": lp.get("volume", p.get("pdv", 0)),
                    "zone": zone,
                    "time": lp.get("time", "--"),
                    "pdo": p.get("pdo", 0.0),
                    "pdh": pdh,
                    "pdl": pdl,
                    "pdc": p.get("pdc", 0.0),
                    "pdv": p.get("pdv", 0),
                    "pp": pp,
                    "tc": tc,
                    "bc": bc,
                    "cpr_top": cpr_top,
                    "cpr_bottom": cpr_bottom,
                    "cpr_width_pct": cpr_width_pct,
                    "is_narrow_cpr": p.get("is_narrow_cpr", cpr_width_pct <= 0.10),
                    "r1": r1,
                    "r2": r2,
                    "r3": r3,
                    "s1": s1,
                    "s2": s2,
                    "s3": s3,
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
