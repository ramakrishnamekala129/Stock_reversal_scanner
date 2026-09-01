"""
Upstox WebSocket Streaming Module.
Wraps MarketDataStreamerV3 to provide normalized tick streaming, auto-reconnection,
and subscription management.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, List, Optional
import upstox_client
from upstox_client.feeder import MarketDataStreamerV3

import config

logger = logging.getLogger(__name__)


@dataclass
class NormalizedTick:
    """Normalized live market tick structure."""
    instrument_key: str
    symbol: str
    timestamp: datetime
    ltp: float
    volume: int
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    total_buy_qty: Optional[float] = None
    total_sell_qty: Optional[float] = None


class UpstoxWebSocketStreamer:
    """
    Manages WebSocket lifecycle for Upstox V3 Market Data Feeds.
    Emits normalized tick events to subscribers.
    """

    def __init__(
        self,
        api_client: upstox_client.ApiClient,
        instrument_key_to_symbol: Optional[Dict[str, str]] = None,
        on_tick: Optional[Callable[[NormalizedTick], None]] = None,
        mode: str = "full",
    ):
        self.api_client = api_client
        self.key_to_symbol = instrument_key_to_symbol or {}
        self.on_tick = on_tick
        self.mode = mode
        self.streamer: Optional[MarketDataStreamerV3] = None
        self.is_connected = False
        self._subscribed_keys: List[str] = []

    def set_symbol_map(self, mapping: Dict[str, str]):
        """Sets or updates the instrument_key -> symbol lookup dictionary."""
        self.key_to_symbol = mapping

    def connect(self, instrument_keys: List[str]):
        """
        Initializes MarketDataStreamerV3 and starts streaming.
        """
        self._subscribed_keys = instrument_keys
        logger.info(f"Initializing Upstox WebSocket streamer with {len(instrument_keys)} instruments...")

        self.streamer = MarketDataStreamerV3(
            api_client=self.api_client,
            instrumentKeys=instrument_keys,
            mode=self.mode,
        )

        self.streamer.on(MarketDataStreamerV3.Event["OPEN"], self._handle_open)
        self.streamer.on(MarketDataStreamerV3.Event["MESSAGE"], self._handle_message)
        self.streamer.on(MarketDataStreamerV3.Event["ERROR"], self._handle_error)
        self.streamer.on(MarketDataStreamerV3.Event["CLOSE"], self._handle_close)
        self.streamer.on(MarketDataStreamerV3.Event["RECONNECTING"], self._handle_reconnecting)
        # Connect streamer
        self.streamer.connect()

    def disconnect(self):
        """Disconnects the WebSocket session."""
        if self.streamer:
            try:
                self.streamer.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting WebSocket: {e}")
            self.is_connected = False

    def subscribe(self, instrument_keys: List[str]):
        """Subscribes to additional instrument keys."""
        if self.streamer and self.is_connected:
            self.streamer.subscribe(instrument_keys, self.mode)

    def _handle_open(self, *args, **kwargs):
        self.is_connected = True
        logger.info("WebSocket market data feed connected successfully.")

    def _handle_close(self, *args, **kwargs):
        self.is_connected = False
        logger.warning(f"WebSocket market data feed closed: {args}")

    def _handle_error(self, *args, **kwargs):
        logger.error(f"WebSocket error encountered: {args}")

    def _handle_reconnecting(self, *args, **kwargs):
        self.is_connected = False
        logger.warning(f"WebSocket reconnecting: {args}")

    def _handle_reconnect_stopped(self, *args, **kwargs):
        self.is_connected = False
        logger.critical(f"WebSocket auto-reconnect stopped: {args}")

    def _handle_message(self, message: Dict[str, Any]):
        """
        Parses incoming Upstox protobuf feed dictionary and dispatches normalized ticks.
        """
        if not message or not isinstance(message, dict):
            return

        feeds = message.get("feeds", {})
        if not feeds:
            return

        for key, feed_data in feeds.items():
            tick = self._parse_feed_data(key, feed_data)
            if tick and self.on_tick:
                try:
                    self.on_tick(tick)
                except Exception as e:
                    logger.error(f"Error executing on_tick callback for {tick.symbol}: {e}", exc_info=True)

    def _parse_feed_data(self, key: str, feed_data: Dict[str, Any]) -> Optional[NormalizedTick]:
        """
        Converts fullFeed, marketFF, or ltpc feed structures into NormalizedTick.
        """
        try:
            symbol = self.key_to_symbol.get(key, key)
            ltp = 0.0
            volume = 0
            open_price = None
            high_price = None
            low_price = None
            close_price = None
            ts_ms = None

            # Handle fullFeed (Market Full Feed)
            if "fullFeed" in feed_data:
                ff = feed_data["fullFeed"]
                market_ff = ff.get("marketFF", {})
                ltpc = market_ff.get("ltpc", {})
                eod_ohlc = market_ff.get("marketOHLC", {}).get("ohlc", [])
                
                ltp = float(ltpc.get("ltp", 0.0))
                # Upstox Protobuf: vtt = Volume Traded Today, ltq = Last Traded Qty
                volume = int(market_ff.get("vtt", 0) or market_ff.get("v", 0) or ltpc.get("ltq", 0))
                ts_ms = ltpc.get("ltt")

                # Parse intraday / day OHLC if present
                for ohlc in eod_ohlc:
                    if ohlc.get("interval") in ["I1", "1d", "1m", "I30", "30m"]:
                        open_price = float(ohlc.get("open", 0.0)) or None
                        high_price = float(ohlc.get("high", 0.0)) or None
                        low_price = float(ohlc.get("low", 0.0)) or None
                        close_price = float(ohlc.get("close", 0.0)) or None
                        if not volume and ohlc.get("vol"):
                            volume = int(ohlc.get("vol"))

            # Handle ltpc feed
            elif "ltpc" in feed_data:
                ltpc = feed_data["ltpc"]
                ltp = float(ltpc.get("ltp", 0.0))
                close_price = float(ltpc.get("cp", 0.0)) if ltpc.get("cp") else None
                ts_ms = ltpc.get("ltt")

            if ltp <= 0:
                return None

            # Parse timestamp (in milliseconds or ISO string) and ensure IST timezone
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            if ts_ms:
                try:
                    if isinstance(ts_ms, (int, float)) or str(ts_ms).isdigit():
                        ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=ist_tz)
                    else:
                        ts = datetime.fromisoformat(str(ts_ms))
                        if ts.tzinfo is not None:
                            ts = ts.astimezone(ist_tz)
                        else:
                            ts = ts.replace(tzinfo=ist_tz)
                except Exception:
                    ts = datetime.now(ist_tz)
            else:
                ts = datetime.now(ist_tz)

            return NormalizedTick(
                instrument_key=key,
                symbol=symbol,
                timestamp=ts,
                ltp=ltp,
                volume=volume,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
            )

        except Exception as e:
            logger.debug(f"Failed to parse tick for key {key}: {e}")
            return None
