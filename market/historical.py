import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
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


class AsyncUpstoxRateLimiter:
    """
    Token bucket rate limiter strictly enforcing Upstox max 25 requests/second limit.
    Enables initial burst capacity up to 25 and continuous smooth token refill at 25 req/sec.
    """
    def __init__(self, rate_per_sec: float = 25.0, burst_capacity: float = 25.0):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst_capacity)
        self.tokens = float(burst_capacity)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


class HistoricalDataLoader:
    """Loads previous-day session data and builds initial 5M history with asyncio acceleration & rate limiting."""

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
        Enforces Upstox 25 req/sec rate limit.
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

        logger.info(f"Fetching previous trading-day OHLCV for {len(universe)} symbols via asyncio (Rate Limit: {config.UPSTOX_RATE_LIMIT_PER_SEC} req/s)...")
        token = self.rest_client.access_token
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        results: Dict[str, PreviousDayOHLCV] = {}

        async def _fetch_all_daily():
            rate_limiter = AsyncUpstoxRateLimiter(config.UPSTOX_RATE_LIMIT_PER_SEC)
            async with httpx.AsyncClient(
                headers=headers,
                timeout=12.0,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=25),
            ) as client:
                async def _fetch_one(sym: str, item: Dict[str, Any]):
                    inst_key = item["instrument_key"]
                    url = f"https://api.upstox.com/v2/historical-candle/{inst_key}/day/2026-09-01/2025-01-01"
                    for attempt in range(config.API_RETRY_ATTEMPTS):
                        await rate_limiter.acquire()
                        try:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                data = resp.json()
                                daily_candles = data.get("data", {}).get("candles", [])
                                for candle in daily_candles:
                                    candle_date = candle[0].split("T")[0]
                                    if candle_date < today_str:
                                        return (sym, PreviousDayOHLCV(
                                            symbol=sym,
                                            instrument_key=inst_key,
                                            date=candle_date,
                                            open=float(candle[1]),
                                            high=float(candle[2]),
                                            low=float(candle[3]),
                                            close=float(candle[4]),
                                            volume=int(candle[5]),
                                        ))
                                return (sym, None)
                            elif resp.status_code == 429:
                                retry_after = float(resp.headers.get("Retry-After", config.API_RETRY_BACKOFF_BASE * (2 ** attempt)))
                                logger.warning(f"Upstox 429 Rate limit on {sym}, backing off for {retry_after:.1f}s...")
                                await asyncio.sleep(retry_after)
                            else:
                                break
                        except Exception as e:
                            logger.debug(f"Attempt {attempt+1} daily fetch error for {sym}: {e}")
                            await asyncio.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))
                    return (sym, None)

                tasks = [_fetch_one(sym, item) for sym, item in universe.items()]
                res_list = await asyncio.gather(*tasks)
                for sym, pd_obj in res_list:
                    if pd_obj:
                        results[sym] = pd_obj

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # In running event loop, create task or run in thread
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _fetch_all_daily()).result()
            else:
                asyncio.run(_fetch_all_daily())
        except Exception as e:
            logger.warning(f"Async daily fetch fallback error: {e}. Running threaded fallback...")
            # Fallback to ThreadPoolExecutor
            def _fetch_single_sync(symbol: str, item: Dict[str, Any]) -> Optional[PreviousDayOHLCV]:
                inst_key = item["instrument_key"]
                daily_candles = self.rest_client.get_historical_daily_candles(inst_key)
                if not daily_candles:
                    return None
                for candle in daily_candles:
                    candle_date = candle[0].split("T")[0]
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
                    executor.submit(_fetch_single_sync, sym, item): sym
                    for sym, item in universe.items()
                }
                for future in as_completed(future_to_sym):
                    sym = future_to_sym[future]
                    try:
                        res = future.result()
                        if res:
                            results[sym] = res
                    except Exception as err:
                        logger.warning(f"Error fetching previous-day data for {sym}: {err}")

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
        force_refresh: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        """
        Loads today's official broker-side 5-minute historical candles using asyncio concurrency.
        Caches locally so dry runs and restarts load in milliseconds.
        """
        today_str = datetime.now(pytz.timezone(config.MARKET_TIMEZONE)).strftime("%Y-%m-%d")
        cache_file = config.CACHE_DIR / f"intraday_5m_{today_str}.json"

        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                dfs = {}
                kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
                for sym, recs in data.items():
                    if recs:
                        df = pd.DataFrame(recs)
                        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(kolkata_tz)
                        dfs[sym] = df
                if len(dfs) > 0:
                    logger.info(f"Loaded {len(dfs)} 5-minute candle datasets from local cache: {cache_file.name}")
                    return dfs
            except Exception as e:
                logger.warning(f"Failed to read 5M candle cache: {e}")

        logger.info("Loading initial 5-minute historical candles directly from broker via asyncio...")
        start_t = time.time()
        dfs = self.refresh_latest_broker_candles(universe)
        elapsed = time.time() - start_t
        logger.info(f"Loaded 5M broker DataFrames for {len(dfs)}/{len(universe)} symbols in {elapsed:.2f}s.")

        # Save to disk cache
        try:
            serializable = {}
            for sym, df in dfs.items():
                if df is not None and not df.empty:
                    df_copy = df.copy()
                    df_copy["timestamp"] = df_copy["timestamp"].astype(str)
                    serializable[sym] = df_copy.to_dict(orient="records")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f)
        except Exception as e:
            logger.warning(f"Failed to write 5M candle cache: {e}")

        return dfs

    def _process_raw_1m_to_5m(self, raw_1m: List[List[Any]]) -> Optional[pd.DataFrame]:
        """Converts raw 1-minute candle tuples into 5-minute resampled DataFrame."""
        if not raw_1m:
            return None

        kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
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

    def load_symbol_broker_5m(self, symbol: str, instrument_key: str) -> Optional[pd.DataFrame]:
        """
        Fetches today's official broker candles for a single symbol and returns 5-minute DataFrame (synchronous).
        """
        raw_1m = self.rest_client.get_intraday_1m_candles(instrument_key)
        return self._process_raw_1m_to_5m(raw_1m)

    def refresh_latest_broker_candles(
        self,
        universe: Dict[str, Dict[str, Any]],
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches the latest official broker-side 5-minute candles for all universe stocks in parallel using asyncio.
        Enforces Upstox 25 req/sec rate limit with automatic exponential backoff.
        """
        token = self.rest_client.access_token
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        results: Dict[str, pd.DataFrame] = {}

        async def _fetch_all_async():
            rate_limiter = AsyncUpstoxRateLimiter(config.UPSTOX_RATE_LIMIT_PER_SEC)
            async with httpx.AsyncClient(
                headers=headers,
                timeout=10.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
            ) as client:
                async def _fetch_one(sym: str, item: Dict[str, Any]):
                    inst_key = item["instrument_key"]
                    url = f"https://api.upstox.com/v2/historical-candle/intraday/{inst_key}/1minute"
                    for attempt in range(config.API_RETRY_ATTEMPTS):
                        await rate_limiter.acquire()
                        try:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                data = resp.json()
                                candles = data.get("data", {}).get("candles", [])
                                df = self._process_raw_1m_to_5m(candles)
                                if df is not None and not df.empty:
                                    return (sym, df)
                                return (sym, None)
                            elif resp.status_code == 429:
                                retry_after = float(resp.headers.get("Retry-After", config.API_RETRY_BACKOFF_BASE * (2 ** attempt)))
                                logger.warning(f"Upstox 429 on {sym}, backing off for {retry_after:.1f}s...")
                                await asyncio.sleep(retry_after)
                            else:
                                break
                        except Exception as e:
                            logger.debug(f"Attempt {attempt+1} async fetch failed for {sym}: {e}")
                            await asyncio.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))
                    return (sym, None)

                tasks = [_fetch_one(sym, item) for sym, item in universe.items()]
                res_list = await asyncio.gather(*tasks)
                for sym, df in res_list:
                    if df is not None:
                        results[sym] = df

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(asyncio.run, _fetch_all_async()).result()
            else:
                asyncio.run(_fetch_all_async())
        except Exception as e:
            logger.warning(f"Async candle fetch error: {e}. Falling back to ThreadPoolExecutor...")
            with ThreadPoolExecutor(max_workers=30) as executor:
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
                    except Exception as err:
                        logger.debug(f"Error fetching broker candles for {sym}: {err}")

        return results
