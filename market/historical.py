import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
import json
import logging
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import httpx
import pandas as pd
import polars as pl
import pytz

import config
from database.repository import DatabaseRepository
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

    def __init__(self, rest_client: UpstoxRestClient, db: Optional[DatabaseRepository] = None):
        self.rest_client = rest_client
        self.db = db or DatabaseRepository()
        self._pd_cache: Dict[str, PreviousDayOHLCV] = {}

    def get_cached_previous_day(self, symbol: str) -> Optional[PreviousDayOHLCV]:
        """Returns in-memory cached previous-day OHLCV."""
        return self._pd_cache.get(symbol)

    def load_all_previous_day_ohlcv(
        self,
        universe: Dict[str, Dict[str, Any]],
        force_refresh: bool = False,
        mode: str = "FUTURES",
    ) -> Dict[str, PreviousDayOHLCV]:
        """
        Retrieves previous trading day's OHLCV for all universe symbols.
        Checks mode-specific local disk cache first (previous_day_ohlcv_{mode}_{date}.json).
        """
        today_str = date.today().isoformat()
        mode_tag = mode.lower() if mode else "futures"
        cache_file = config.CACHE_DIR / f"previous_day_ohlcv_{mode_tag}_{today_str}.json"

        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    for sym, d in cached_data.items():
                        self._pd_cache[sym] = PreviousDayOHLCV(**d)
                    logger.info(f"Loaded {len(self._pd_cache)} previous-day OHLCV records ({mode_tag}) from cache.")
                    return self._pd_cache
            except Exception as e:
                logger.warning(f"Failed to read previous-day cache: {e}. Fetching via REST...")

        logger.info(f"Fetching previous trading-day OHLCV ({mode_tag}) for {len(universe)} symbols via asyncio (Rate Limit: {config.UPSTOX_RATE_LIMIT_PER_SEC} req/s)...")
        token = self.rest_client.access_token
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        results: Dict[str, PreviousDayOHLCV] = {}

        async def _fetch_all_daily():
            rate_limiter = AsyncUpstoxRateLimiter(config.UPSTOX_RATE_LIMIT_PER_SEC)
            from_date_str = (date.today() - timedelta(days=35)).isoformat()
            async with httpx.AsyncClient(
                headers=headers,
                timeout=12.0,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=25),
            ) as client:
                async def _fetch_one(sym: str, item: Dict[str, Any]):
                    inst_key = item["instrument_key"]
                    encoded_key = urllib.parse.quote(inst_key)
                    url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/day/{today_str}/{from_date_str}"
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
        Caches into SQLite database table (candles_5m) for sub-millisecond lookback and atomic batch inserts.
        """
        today_str = datetime.now(pytz.timezone(config.MARKET_TIMEZONE)).strftime("%Y-%m-%d")

        # 1. Query SQLite Database Cache
        if not force_refresh:
            try:
                db_dfs = self.db.get_candles_by_date(today_str)
                if len(db_dfs) >= len(universe):
                    logger.info(f"Loaded {len(db_dfs)}/{len(universe)} 5-minute candle datasets directly from SQLite database (candles_5m).")
                    return db_dfs
            except Exception as e:
                logger.warning(f"Failed to query SQLite candle cache: {e}")

        # 2. Fetch fresh 5M candles from broker via asyncio
        logger.info("Loading initial 5-minute historical candles directly from broker via asyncio...")
        start_t = time.time()
        dfs = self.refresh_latest_broker_candles(universe)
        elapsed = time.time() - start_t
        logger.info(f"Loaded 5M broker DataFrames for {len(dfs)}/{len(universe)} symbols in {elapsed:.2f}s.")

        # 3. Batch insert all fetched candles into SQLite database
        try:
            records = []
            for sym, df in dfs.items():
                if df is not None and not df.empty:
                    inst_key = universe.get(sym, {}).get("instrument_key", "")
                    for row in df.itertuples():
                        ts_str = row.timestamp.isoformat() if hasattr(row.timestamp, "isoformat") else str(row.timestamp)
                        records.append({
                            "symbol": sym,
                            "instrument_key": inst_key,
                            "timestamp": ts_str,
                            "open": float(row.open),
                            "high": float(row.high),
                            "low": float(row.low),
                            "close": float(row.close),
                            "volume": int(row.volume),
                            "is_closed": 1,
                        })
            if records:
                self.db.save_candles_batch(records)
                logger.info(f"Cached {len(records)} 5-minute candles across {len(dfs)} stocks into SQLite database.")
        except Exception as e:
            logger.warning(f"Failed to write 5M candles into SQLite database: {e}")

        return dfs

    def _process_raw_1m_to_5m(self, raw_1m: List[List[Any]], timeframe: str = "5m") -> Optional[pd.DataFrame]:
        """
        Converts raw 1-minute candle tuples into 3m, 5m, or 15m resampled DataFrame.
        Accelerated using Polars (40x+ faster than pure Python/pandas date parsing).
        """
        if not raw_1m:
            return None

        rule_map = {"3m": "3m", "5m": "5m", "15m": "15m"}
        rule = rule_map.get(timeframe, "5m")

        cutoff_map = {"3m": dt_time(15, 27), "5m": dt_time(15, 25), "15m": dt_time(15, 15)}
        cutoff = cutoff_map.get(timeframe, dt_time(15, 25))

        try:
            # 1. High-speed Polars Vectorized Ingestion & Resampling
            df_pl = (
                pl.DataFrame(
                    raw_1m,
                    schema=["timestamp", "open", "high", "low", "close", "volume"],
                    orient="row",
                )
                .with_columns([
                    pl.col("timestamp").str.to_datetime(time_zone=config.MARKET_TIMEZONE),
                    pl.col("open").cast(pl.Float64),
                    pl.col("high").cast(pl.Float64),
                    pl.col("low").cast(pl.Float64),
                    pl.col("close").cast(pl.Float64),
                    pl.col("volume").cast(pl.Int64),
                ])
                .filter(
                    (pl.col("timestamp").dt.hour() > 9)
                    | ((pl.col("timestamp").dt.hour() == 9) & (pl.col("timestamp").dt.minute() >= 15))
                )
                .filter(
                    (pl.col("timestamp").dt.hour() < 15)
                    | ((pl.col("timestamp").dt.hour() == 15) & (pl.col("timestamp").dt.minute() <= 30))
                )
                .sort("timestamp")
            )

            if df_pl.is_empty():
                return None

            df_res_pl = (
                df_pl.group_by_dynamic(
                    "timestamp",
                    every=rule,
                    period=rule,
                    offset="15m",
                )
                .agg([
                    pl.col("open").first(),
                    pl.col("high").max(),
                    pl.col("low").min(),
                    pl.col("close").last(),
                    pl.col("volume").sum(),
                ])
                .sort("timestamp")
            )

            df_res = df_res_pl.to_pandas()
            df_res = df_res[df_res["timestamp"].dt.time <= cutoff].reset_index(drop=True)
            return df_res

        except Exception as e:
            logger.debug(f"Polars resampling fallback to Pandas: {e}")
            # Fallback to standard pandas pipeline
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
            records = [r for r in records if dt_time(9, 15) <= r["timestamp"].time() <= dt_time(15, 30)]
            if not records:
                return None
            df_1m = pd.DataFrame(records).sort_values("timestamp").set_index("timestamp")
            p_rule = {"3m": "3min", "5m": "5min", "15m": "15min"}.get(timeframe, "5min")
            df_res = df_1m.resample(p_rule, origin="start_day", offset="15min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna().reset_index()
            return df_res[df_res["timestamp"].dt.time <= cutoff].reset_index(drop=True)

    def load_symbol_broker_5m(self, symbol: str, instrument_key: str, timeframe: str = "5m") -> Optional[pd.DataFrame]:
        """
        Fetches today's official broker candles for a single symbol and returns resampled DataFrame (synchronous).
        """
        raw_1m = self.rest_client.get_intraday_1m_candles(instrument_key)
        return self._process_raw_1m_to_5m(raw_1m, timeframe=timeframe)

    def refresh_latest_broker_candles(
        self,
        universe: Dict[str, Dict[str, Any]],
        timeframe: str = "5m",
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches the latest official broker-side candles (3m, 5m, 15m) for all universe stocks in parallel using asyncio.
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
                                df = self._process_raw_1m_to_5m(candles, timeframe=timeframe)
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
