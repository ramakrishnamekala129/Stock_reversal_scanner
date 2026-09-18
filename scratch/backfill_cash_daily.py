"""
Utility script to backfill multi-month daily candles for all missing NSE Cash equities
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
from market.rate_limiter import AsyncUpstoxRateLimiter
from upstox.auth import UpstoxAuth
from upstox.rest import UpstoxRestClient
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def backfill_missing_cash_daily():
    auth = UpstoxAuth()
    if not auth.validate_token():
        logger.error("No valid Upstox access token found!")
        return

    client = UpstoxRestClient(auth.get_api_client())
    mgr = InstrumentManager(client)
    univ_cash = mgr.load_universe("CASH", mode="SPOT")
    logger.info(f"Loaded NSE Cash Universe: {len(univ_cash)} stocks.")

    hist_db = HistoricalCandleDatabase()
    cached_map = hist_db.get_all_daily_candles_map()
    logger.info(f"Currently cached daily symbols in DB: {len(cached_map)}.")

    missing_symbols = {sym: info for sym, info in univ_cash.items() if sym not in cached_map or len(cached_map[sym]) < 15}
    logger.info(f"Missing daily symbols to download: {len(missing_symbols)}.")

    if not missing_symbols:
        logger.info("All Cash symbols already have daily candles cached!")
        return

    today_str = date.today().isoformat()
    from_date_str = "2024-01-01"
    headers = {"Authorization": f"Bearer {auth.access_token}", "Accept": "application/json"}
    sem = asyncio.Semaphore(12)
    rate_limiter = AsyncUpstoxRateLimiter(min(config.UPSTOX_RATE_LIMIT_PER_SEC, 12.0), 12.0)
    results_raw: Dict[str, List[Any]] = {}

    batch_size = 500
    missing_items = list(missing_symbols.items())
    total_batches = (len(missing_items) + batch_size - 1) // batch_size

    async with httpx.AsyncClient(headers=headers, timeout=12.0, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)) as http_client:
        async def fetch_one(sym: str, item: Dict[str, Any]):
            key = item.get("instrument_key", "")
            if not key:
                return
            encoded = urllib.parse.quote(key)
            url = f"https://api.upstox.com/v2/historical-candle/{encoded}/day/{today_str}/{from_date_str}"

            for attempt in range(3):
                async with sem:
                    await rate_limiter.acquire()
                    try:
                        resp = await http_client.get(url)
                        if resp.status_code == 200:
                            candles = resp.json().get("data", {}).get("candles", [])
                            if candles:
                                results_raw[sym] = candles
                            return
                        elif resp.status_code == 429:
                            raw_retry = float(resp.headers.get("Retry-After", 2.0))
                            retry_after, announced = rate_limiter.defer(raw_retry)
                            if announced:
                                logger.warning(f"429 rate limit on {sym}, pacing {retry_after:.1f}s...")
                        else:
                            break
                    except Exception as e:
                        logger.debug(f"Error fetching daily {sym}: {e}")
                        await asyncio.sleep(0.5)

        for b_idx in range(total_batches):
            chunk = missing_items[b_idx * batch_size : (b_idx + 1) * batch_size]
            logger.info(f"Processing Batch {b_idx + 1}/{total_batches} ({len(chunk)} symbols)...")
            tasks = [fetch_one(s, itm) for s, itm in chunk]
            await asyncio.gather(*tasks)
            # Save progress incrementally
            if results_raw:
                hist_db.save_all_daily_candles_bulk(results_raw)
                logger.info(f"Saved {len(results_raw)} newly downloaded symbols to DB.")
                results_raw.clear()

    final_cached = hist_db.get_all_daily_candles_map()
    logger.info(f"Complete! Total daily cached symbols in SQLite DB: {len(final_cached)}.")


if __name__ == "__main__":
    asyncio.run(backfill_missing_cash_daily())
