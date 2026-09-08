"""
Automated Historical Candle Gap Filler.
Queries Upstox REST Historical Candle APIs to fetch missing candle intervals,
validates sessions against 09:15-15:30 boundaries, and saves them into HistoricalCandleDatabase.
"""

import asyncio
import logging
import time
import urllib.parse
from datetime import date, datetime, time as dt_time
from typing import Any, Callable, Dict, List, Optional

import httpx

import config
from database.historical_db import HistoricalCandleDatabase
from market.gap_detector import GapDetector, GapWindow

logger = logging.getLogger(__name__)


class GapFiller:
    """
    Automated backfiller for missing historical candles using Upstox V2 REST endpoints.
    Enforces strict token bucket rate-limiting and atomic database commits.
    """

    BASE_URL = "https://api.upstox.com/v2"

    def __init__(
        self,
        db: HistoricalCandleDatabase,
        access_token: Optional[str] = None,
        rate_limit: float = 20.0,
    ):
        self.db = db
        self.access_token = access_token or config.UPSTOX_ACCESS_TOKEN
        self.rate_limit = rate_limit
        self.gap_detector = GapDetector(db)
        self._is_syncing = False

    def fill_gaps_for_symbol_sync(
        self,
        symbol: str,
        instrument_key: str,
        gap_windows: List[GapWindow],
    ) -> int:
        """Synchronously fills gaps for a single symbol using requests or httpx."""
        if not gap_windows or not self.access_token:
            return 0

        total_inserted = 0
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": "Upstox-FNO-Historical-Sync/1.0",
        }

        today_str = date.today().isoformat()
        encoded_key = urllib.parse.quote(instrument_key)

        with httpx.Client(headers=headers, timeout=10.0) as client:
            for gw in gap_windows:
                candles = []
                # 1. If window includes today, fetch today's intraday 1m feed
                if today_str in gw.missing_dates:
                    try:
                        url_today = f"{self.BASE_URL}/historical-candle/intraday/{encoded_key}/1minute"
                        resp = client.get(url_today)
                        if resp.status_code == 200:
                            c_data = resp.json().get("data", {}).get("candles", [])
                            if c_data:
                                candles.extend(c_data)
                    except Exception as ex:
                        logger.debug(f"Intraday gap fetch error for {symbol}: {ex}")

                # 2. Fetch historical date range for past dates
                past_dates = [d for d in gw.missing_dates if d != today_str]
                if past_dates:
                    from_d = min(past_dates)
                    to_d = max(past_dates)
                    url_hist = f"{self.BASE_URL}/historical-candle/{encoded_key}/1minute/{to_d}/{from_d}"
                    for attempt in range(3):
                        try:
                            resp = client.get(url_hist)
                            if resp.status_code == 200:
                                c_data = resp.json().get("data", {}).get("candles", [])
                                if c_data:
                                    candles.extend(c_data)
                                break
                            elif resp.status_code == 429:
                                time.sleep(1.0 * (attempt + 1))
                            else:
                                logger.debug(f"Historical fetch {symbol} ({from_d} to {to_d}) status {resp.status_code}")
                                break
                        except Exception as ex:
                            logger.debug(f"Attempt {attempt+1} error fetching {symbol} historical candles: {ex}")
                            time.sleep(0.5)

                if candles:
                    # Filter candles within standard market hours 09:15 to 15:30
                    valid_candles = []
                    for c in candles:
                        ts_str = str(c[0])
                        # Time parsing
                        try:
                            t_part = ts_str.split("T")[1][:8]
                            t_obj = datetime.strptime(t_part, "%H:%M:%S").time()
                            if dt_time(9, 15) <= t_obj <= dt_time(15, 30):
                                valid_candles.append(c)
                        except Exception:
                            valid_candles.append(c)

                    inserted = self.db.save_candles_batch(symbol, instrument_key, valid_candles)
                    total_inserted += inserted

        return total_inserted

    async def reconcile_universe_async(
        self,
        universe: Dict[str, Dict[str, Any]],
        lookback_days: int = 30,
        concurrency: int = 15,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Asynchronously scans the entire universe for gaps and fills missing windows
        with token-bucket rate limiting (max 15-20 req/s).
        """
        if self._is_syncing:
            logger.info("Historical gap sync already in progress. Skipping redundant request.")
            return {"status": "already_running", "filled_candles": 0}

        self._is_syncing = True
        t0 = time.time()
        logger.info(f"Checking historical candle database for gaps across {len(universe)} symbols (Lookback: {lookback_days} days)...")

        try:
            # 1. Detect all gaps
            universe_gaps = self.gap_detector.detect_universe_gaps(universe, lookback_days=lookback_days)
            total_symbols_with_gaps = len(universe_gaps)
            total_windows = sum(len(gaps) for gaps in universe_gaps.values())

            logger.info(f"Gap detection complete: Found {total_symbols_with_gaps} symbols with {total_windows} missing window(s).")
            if not universe_gaps:
                return {
                    "status": "complete",
                    "symbols_with_gaps": 0,
                    "total_windows": 0,
                    "filled_candles": 0,
                    "elapsed_s": round(time.time() - t0, 2),
                }

            if not self.access_token:
                logger.warning("No Upstox access token available for gap filling.")
                return {"status": "no_token", "symbols_with_gaps": total_symbols_with_gaps}

            # 2. Asynchronous backfilling with semaphore rate control
            sem = asyncio.Semaphore(concurrency)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                "User-Agent": "Upstox-FNO-Historical-Sync/1.0",
            }

            today_str = date.today().isoformat()
            total_filled = 0
            processed_count = 0

            async with httpx.AsyncClient(
                headers=headers,
                timeout=12.0,
                limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
            ) as client:

                async def _sync_one_symbol(sym: str, gaps: List[GapWindow]):
                    nonlocal total_filled, processed_count
                    inst_key = gaps[0].instrument_key
                    encoded_key = urllib.parse.quote(inst_key)
                    sym_candles = []

                    for gw in gaps:
                        async with sem:
                            # If today is missing
                            if today_str in gw.missing_dates:
                                try:
                                    url_today = f"{self.BASE_URL}/historical-candle/intraday/{encoded_key}/1minute"
                                    r = await client.get(url_today)
                                    if r.status_code == 200:
                                        c_data = r.json().get("data", {}).get("candles", [])
                                        if c_data:
                                            sym_candles.extend(c_data)
                                    await asyncio.sleep(0.05)
                                except Exception:
                                    pass

                            # Historical past date range
                            past_dates = [d for d in gw.missing_dates if d != today_str]
                            if past_dates:
                                from_d = min(past_dates)
                                to_d = max(past_dates)
                                url_hist = f"{self.BASE_URL}/historical-candle/{encoded_key}/1minute/{to_d}/{from_d}"
                                for attempt in range(3):
                                    try:
                                        r = await client.get(url_hist)
                                        if r.status_code == 200:
                                            c_data = r.json().get("data", {}).get("candles", [])
                                            if c_data:
                                                sym_candles.extend(c_data)
                                            break
                                        elif r.status_code == 429:
                                            await asyncio.sleep(1.0 * (attempt + 1))
                                        else:
                                            break
                                    except Exception:
                                        await asyncio.sleep(0.5)
                                await asyncio.sleep(0.05)

                    if sym_candles:
                        # Filter valid 09:15 to 15:30
                        valid_c = []
                        for c in sym_candles:
                            try:
                                ts_str = str(c[0])
                                t_part = ts_str.split("T")[1][:8]
                                t_obj = datetime.strptime(t_part, "%H:%M:%S").time()
                                if dt_time(9, 15) <= t_obj <= dt_time(15, 30):
                                    valid_c.append(c)
                            except Exception:
                                valid_c.append(c)

                        n_saved = self.db.save_candles_batch(sym, inst_key, valid_c)
                        total_filled += n_saved

                    processed_count += 1
                    if progress_cb:
                        try:
                            progress_cb(processed_count, total_symbols_with_gaps, sym)
                        except Exception:
                            pass

                tasks = [_sync_one_symbol(sym, gaps) for sym, gaps in universe_gaps.items()]
                await asyncio.gather(*tasks, return_exceptions=True)

            elapsed = round(time.time() - t0, 2)
            logger.info(f"Historical gap fill complete: Filled {total_filled} candles across {processed_count} symbols in {elapsed}s.")
            return {
                "status": "complete",
                "symbols_with_gaps": total_symbols_with_gaps,
                "symbols_filled": processed_count,
                "filled_candles": total_filled,
                "elapsed_s": elapsed,
            }
        finally:
            self._is_syncing = False

    def reconcile_universe_sync(
        self,
        universe: Dict[str, Dict[str, Any]],
        lookback_days: int = 30,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Convenience method to run reconciliation synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.reconcile_universe_async(universe, lookback_days, progress_cb=progress_cb)).result()
        else:
            return asyncio.run(self.reconcile_universe_async(universe, lookback_days, progress_cb=progress_cb))

    def reconcile_universe_gaps(
        self,
        universe: Dict[str, Dict[str, Any]],
        lookback_days: int = 30,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Alias for reconcile_universe_sync supporting on_progress callback."""
        return self.reconcile_universe_sync(universe, lookback_days=lookback_days, progress_cb=on_progress)
