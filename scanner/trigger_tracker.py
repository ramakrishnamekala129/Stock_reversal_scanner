"""
Signal Trigger Tracker Module.
Tracks the lifecycle of detected reversal setups:
- BULLISH SETUP: Confirmed when a future candle crosses above the setup candle's high (Price > candle_high).
- BEARISH WARNING: Confirmed when a future candle crosses below the setup candle's low (Price < candle_low).
- INVALIDATED: When price breaks through the opposite stop boundary (setup low for bullish, setup high for bearish).
- PENDING: Waiting for confirmation.
"""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SignalTriggerTracker:
    """Manages active signals and tracks next-candle confirmation."""

    def __init__(self):
        # Active pending signals: symbol -> list of signal dicts
        self._pending_signals: Dict[str, List[dict]] = {}

    def register_signal(self, sig_dict: dict):
        """Registers a newly detected signal for trigger tracking."""
        symbol = sig_dict.get("symbol")
        if not symbol:
            return

        # Ensure required tracking fields exist
        if "trigger_status" not in sig_dict or not sig_dict["trigger_status"]:
            sig_dict["trigger_status"] = "PENDING"

        if sig_dict["trigger_status"] == "PENDING":
            if symbol not in self._pending_signals:
                self._pending_signals[symbol] = []
            self._pending_signals[symbol].append(sig_dict)

    def check_candle_triggers(
        self,
        symbol: str,
        candle_high: float,
        candle_low: float,
        candle_close: float,
        candle_timestamp: Any,
    ) -> List[dict]:
        """
        Evaluates forming or closed candle against pending setups for this symbol.
        Returns list of signals whose trigger status changed (e.g. PENDING -> TRIGGERED or INVALIDATED).
        """
        pending = self._pending_signals.get(symbol, [])
        if not pending:
            return []

        ts_str = candle_timestamp.isoformat() if isinstance(candle_timestamp, datetime) else str(candle_timestamp)
        if "T" in ts_str:
            ts_display = ts_str.split("T")[1].split("+")[0].split(".")[0]
        else:
            ts_display = ts_str[-8:]

        updated_signals = []
        still_pending = []

        for sig in pending:
            sig_ts = str(sig.get("timestamp", ""))
            # Do not trigger on the exact same candle timestamp that formed the setup
            if ts_str == sig_ts or (ts_display and ts_display in sig_ts):
                still_pending.append(sig)
                continue

            direction = sig.get("direction", "")
            setup_high = float(sig.get("candle_high", sig.get("price", 0.0)))
            setup_low = float(sig.get("candle_low", sig.get("price", 0.0)))
            is_bull = "BULLISH" in direction

            if is_bull:
                # Bullish setup confirmed when price crosses setup candle high
                if candle_high > setup_high:
                    sig["trigger_status"] = "TRIGGERED"
                    sig["trigger_time"] = ts_display
                    updated_signals.append(sig)
                    logger.info(f"✅ BULLISH TRIGGER CONFIRMED: {symbol} {sig.get('pattern')} at {ts_display} (High {candle_high:.2f} > Setup High {setup_high:.2f})")
                elif candle_low < setup_low:
                    # Invalidation: price broke below setup candle low before triggering
                    sig["trigger_status"] = "INVALIDATED"
                    sig["trigger_time"] = ts_display
                    updated_signals.append(sig)
                    logger.debug(f"❌ BULLISH SETUP INVALIDATED: {symbol} {sig.get('pattern')} at {ts_display} (Low {candle_low:.2f} < Setup Low {setup_low:.2f})")
                else:
                    still_pending.append(sig)
            else:
                # Bearish setup confirmed when price crosses setup candle low
                if candle_low < setup_low:
                    sig["trigger_status"] = "TRIGGERED"
                    sig["trigger_time"] = ts_display
                    updated_signals.append(sig)
                    logger.info(f"✅ BEARISH TRIGGER CONFIRMED: {symbol} {sig.get('pattern')} at {ts_display} (Low {candle_low:.2f} < Setup Low {setup_low:.2f})")
                elif candle_high > setup_high:
                    # Invalidation: price broke above setup candle high before triggering
                    sig["trigger_status"] = "INVALIDATED"
                    sig["trigger_time"] = ts_display
                    updated_signals.append(sig)
                    logger.debug(f"❌ BEARISH SETUP INVALIDATED: {symbol} {sig.get('pattern')} at {ts_display} (High {candle_high:.2f} > Setup High {setup_high:.2f})")
                else:
                    still_pending.append(sig)

        self._pending_signals[symbol] = still_pending
        return updated_signals

    def evaluate_historical_chain(self, symbol: str, signals: List[dict], df_history: Any) -> List[dict]:
        """
        Reconstructs the trigger status of historical signals based on the sequence of 5M candles.
        """
        if not signals or df_history is None or df_history.empty:
            return signals

        # For each signal, find its position in df_history and check subsequent candles
        for sig in signals:
            sig_ts = str(sig.get("timestamp", ""))
            setup_high = float(sig.get("candle_high", sig.get("price", 0.0)))
            setup_low = float(sig.get("candle_low", sig.get("price", 0.0)))
            is_bull = "BULLISH" in str(sig.get("direction", ""))

            # Find matching candle index in history
            match_idx = -1
            for idx, row in df_history.iterrows():
                r_ts = str(row["timestamp"])
                if sig_ts in r_ts or r_ts in sig_ts or (len(sig_ts) >= 16 and sig_ts[:16] in r_ts):
                    match_idx = idx
                    break

            if match_idx == -1:
                continue

            # Look at future candles (after match_idx)
            future_candles = df_history.iloc[match_idx + 1 :]
            status = "PENDING"
            trigger_time = ""

            for _, f_row in future_candles.iterrows():
                f_high = float(f_row["high"])
                f_low = float(f_row["low"])
                f_ts = str(f_row["timestamp"])
                if "T" in f_ts:
                    t_str = f_ts.split("T")[1].split("+")[0].split(".")[0]
                else:
                    t_str = f_ts[-8:]

                if is_bull:
                    if f_high > setup_high:
                        status = "TRIGGERED"
                        trigger_time = t_str
                        break
                    elif f_low < setup_low:
                        status = "INVALIDATED"
                        trigger_time = t_str
                        break
                else:
                    if f_low < setup_low:
                        status = "TRIGGERED"
                        trigger_time = t_str
                        break
                    elif f_high > setup_high:
                        status = "INVALIDATED"
                        trigger_time = t_str
                        break

            sig["trigger_status"] = status
            sig["trigger_time"] = trigger_time

        return signals
