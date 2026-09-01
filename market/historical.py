"""
Historical Data Loading & Previous Session Resolution Module.
Handles downloading previous trading-day OHLCV and today's initial 5M candles.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from typing import Any, Dict, List, Optional
import pandas as pd
import pytz

import config
from upstox.rest import UpstoxRestClient

logger = logging.getLogger(__name__)


@dataclass
class PreviousDayOHLCV:
    """Previous trading day OHLCV data."""
    symbol: str
    instrument_key: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "instrument_key": self.instrument_key,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class HistoricalDataLoader:
    """Loads previous-day session data and builds initial 5M history."""

    def __init__(self, rest_client: UpstoxRestClient):
        self.rest_client = rest_client
        self._pd_cache: Dict[str, PreviousDayOHLCV] = {}

    def get_cached_previous_day(self, symbol: str) -> Optional[PreviousDayOHLCV]:
        """Returns in-memory cached previous-day OHLCV."""
        return self._pd_cache.get(symbol)

    def load_all_previous_day_ohlcv(
        self,
        universe: Dict[str, Dict[str, Any]],
        force_refresh: bool = False,
    ) -> Dict[str, PreviousDayOHLCV]:
        """
        Retrieves previous trading session OHLCV for every F&O stock.
        Properly ignores current date and picks the most recent completed trading session.
        Uses local disk cache to avoid redundant API requests.
        """
        today_str = date.today().isoformat()
        cache_file = config.CACHE_DIR / f"previous_day_ohlcv_{today_str}.json"

        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    for sym, d in cached_data.items():
                        self._pd_cache[sym] = PreviousDayOHLCV(**d)
                    logger.info(f"Loaded {len(self._pd_cache)} previous-day OHLCV records from cache.")
                    return self._pd_cache
            except Exception as e:
                logger.warning(f"Failed to read previous-day cache: {e}. Fetching via REST...")

        logger.info(f"Fetching previous trading-day OHLCV for {len(universe)} symbols...")
        results: Dict[str, PreviousDayOHLCV] = {}

        def _fetch_single(symbol: str, item: Dict[str, Any]) -> Optional[PreviousDayOHLCV]:
            inst_key = item["instrument_key"]
            daily_candles = self.rest_client.get_historical_daily_candles(inst_key)
            if not daily_candles:
                return None

            # Daily candles format: [timestamp, open, high, low, close, volume, oi]
            # Upstox returns newest first or chronological. Find the most recent date BEFORE today.
            for candle in daily_candles:
                ts_str = candle[0]
                candle_date = ts_str.split("T")[0]
                if candle_date < today_str:
                    return PreviousDayOHLCV(
                        symbol=symbol,
                        instrument_key=inst_key,
                        date=candle_date,
                        open=float(candle[1]),
                        high=float(candle[2]),
                        low=float(candle[3]),
                        close=float(candle[4]),
                        volume=int(candle[5]),
                    )
            return None

        with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_REQUESTS) as executor:
            future_to_sym = {
                executor.submit(_fetch_single, sym, item): sym
                for sym, item in universe.items()
            }
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    res = future.result()
                    if res:
                        results[sym] = res
                except Exception as e:
                    logger.warning(f"Error fetching previous-day data for {sym}: {e}")

        self._pd_cache = results
        logger.info(f"Successfully retrieved previous-day OHLCV for {len(results)} symbols.")

        # Cache to disk
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({sym: pd_data.to_dict() for sym, pd_data in results.items()}, f)
        except Exception as e:
            logger.warning(f"Failed to write previous-day cache: {e}")

        return results

    def load_initial_5m_candles(
        self,
        universe: Dict[str, Dict[str, Any]],
    ) -> Dict[str, pd.DataFrame]:
        """
        Loads today's official broker-side 5-minute historical candles.
        Provides historical candle lookback at startup.
        """
        logger.info("Loading initial 5-minute historical candles directly from broker...")
        dfs = self.refresh_latest_broker_candles(universe)
        logger.info(f"Loaded 5M broker DataFrames for {len(dfs)} symbols.")
        return dfs

    def load_symbol_broker_5m(self, symbol: str, instrument_key: str) -> Optional[pd.DataFrame]:
        """
        Fetches today's official broker candles for a single symbol and returns 5-minute DataFrame.
        """
        kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
        raw_1m = self.rest_client.get_intraday_1m_candles(instrument_key)
        if not raw_1m:
            return None

        records = []
        for c in raw_1m:
            ts = pd.to_datetime(c[0])
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC").tz_convert(kolkata_tz)
            else:
                ts = ts.tz_convert(kolkata_tz)

            records.append({
                "timestamp": ts,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": int(c[5]),
            })

        if not records:
            return None

        df_1m = pd.DataFrame(records).sort_values("timestamp").set_index("timestamp")
        df_5m = df_1m.resample("5min", origin="start_day", offset="15min").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna().reset_index()

        return df_5m

    def refresh_latest_broker_candles(
        self,
        universe: Dict[str, Dict[str, Any]],
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches the latest official broker-side 5-minute candles for all universe stocks.
        """
        results: Dict[str, pd.DataFrame] = {}

        with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_REQUESTS) as executor:
            future_to_sym = {
                executor.submit(self.load_symbol_broker_5m, sym, item["instrument_key"]): sym
                for sym, item in universe.items()
            }
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        results[sym] = df
                except Exception as e:
                    logger.debug(f"Error fetching broker candles for {sym}: {e}")

        return results
