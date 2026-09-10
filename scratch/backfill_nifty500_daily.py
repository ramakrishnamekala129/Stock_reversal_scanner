"""
Utility script to backfill multi-year daily candles for all missing Nifty 500 stocks
into HistoricalCandleDatabase (candles_history_daily table).
"""

import asyncio
import logging
import time
import urllib.parse
from datetime import date
from typing import Any, Dict, List

import httpx

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.historical_db import HistoricalCandleDatabase
from market.instruments import InstrumentManager
from market.universe_loader import UniverseLoader
from upstox.auth import UpstoxAuth
from upstox.rest import UpstoxRestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def backfill_missing_nifty500_daily():
    auth = UpstoxAuth()
    if not auth.validate_token():
        logger.error("No valid Upstox access token found!")
        return

    client = UpstoxRestClient(auth.get_api_client())
    mgr = InstrumentManager(client)
    univ_500 = mgr.load_universe("NIFTY500", mode="SPOT")
    logger.info(f"Loaded Nifty 500 Universe: {len(univ_500)} stocks.")

    hist_db = HistoricalCandleDatabase()
    cached_map = hist_db.get_all_daily_candles_map()
    logger.info(f"Currently cached daily symbols in DB: {len(cached_map)}.")

    missing_symbols = {sym: info for sym, info in univ_500.items() if sym not in cached_map}
    logger.info(f"Missing daily symbols to download: {len(missing_symbols)}.")

    if not missing_symbols:
        logger.info("All Nifty 500 symbols already have daily candles cached!")
        return

    today_str = date.today().isoformat()
    from_date_str = "2024-01-01"
    headers = {"Authorization": f"Bearer {auth.access_token}", "Accept": "application/json"}
    sem = asyncio.Semaphore(12)  # Conservative concurrency
    results_raw: Dict[str, List[Any]] = {}

    async with httpx.AsyncClient(headers=headers, timeout=12.0) as http_client:
        async def fetch_one(sym: str, item: Dict[str, Any]):
            key = item.get("instrument_key", "")
            if not key:
                return
            encoded = urllib.parse.quote(key)
            url = f"https://api.upstox.com/v2/historical-candle/{encoded}/day/{today_str}/{from_date_str}"

            for attempt in range(3):
                async with sem:
                    try:
                        resp = await http_client.get(url)
                        if resp.status_code == 200:
                            candles = resp.json().get("data", {}).get("candles", [])
                            if candles:
                                results_raw[sym] = candles
                            return
                        elif resp.status_code == 429:
                            backoff = 1.5 * (attempt + 1)
                            logger.warning(f"429 rate limit on {sym}, pacing {backoff:.1f}s...")
                            await asyncio.sleep(backoff)
                        else:
                            logger.debug(f"HTTP {resp.status_code} for {sym}")
                            break
                    except Exception as e:
                        logger.debug(f"Error fetching daily {sym}: {e}")
                        await asyncio.sleep(1.0)
                await asyncio.sleep(0.08)  # Smooth token pacing

        tasks = [fetch_one(sym, item) for sym, item in missing_symbols.items()]
        t0 = time.time()
        logger.info(f"Starting async download for {len(tasks)} symbols...")
        await asyncio.gather(*tasks)
        elapsed = time.time() - t0
        logger.info(f"Downloaded daily candles for {len(results_raw)}/{len(missing_symbols)} symbols in {elapsed:.2f}s.")

    if results_raw:
        logger.info(f"Persisting {len(results_raw)} symbols into HistoricalCandleDatabase...")
        saved_rows = hist_db.save_all_daily_candles_bulk(results_raw)
        logger.info(f"Successfully cached {saved_rows} daily candle rows into SQLite DB!")

        final_cached = hist_db.get_all_daily_candles_map()
        logger.info(f"Updated total daily cached symbols in SQLite DB: {len(final_cached)} symbols.")


if __name__ == "__main__":
    asyncio.run(backfill_missing_nifty500_daily())
