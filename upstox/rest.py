"""
Upstox REST API Client Module.
Handles instrument master retrieval, previous-day OHLCV, intraday historical candles,
and WebSocket authorization.
"""

import gzip
import json
import logging
import time
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import requests
import upstox_client
from upstox_client.rest import ApiException

import config
from market.rate_limiter import AsyncUpstoxRateLimiter

logger = logging.getLogger(__name__)


class UpstoxRestClient:
    """REST API wrapper for Upstox V2 historical and market data endpoints."""

    INSTRUMENT_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    BASE_API_V2_URL = "https://api.upstox.com/v2"

    def __init__(self, api_client: Optional[upstox_client.ApiClient] = None):
        self.api_client = api_client
        self.rate_limiter = AsyncUpstoxRateLimiter(config.UPSTOX_RATE_LIMIT_PER_SEC)
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Upstox-FNO-Scanner/1.0",
        })
        if self.api_client is None and config.UPSTOX_ACCESS_TOKEN:
            conf = upstox_client.Configuration()
            conf.access_token = config.UPSTOX_ACCESS_TOKEN
            self.api_client = upstox_client.ApiClient(conf)

        if self.access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}"
            })

    @property
    def access_token(self) -> str:
        """Returns the active Upstox access token string."""
        if self.api_client and self.api_client.configuration and self.api_client.configuration.access_token:
            return self.api_client.configuration.access_token
        return config.UPSTOX_ACCESS_TOKEN or ""

    def download_nse_instruments(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Downloads and caches the official Upstox NSE instrument master in SQLite DB.
        Returns a list of raw instrument dictionaries.
        """
        today_str = date.today().isoformat()
        from database.repository import DatabaseRepository
        db_repo = DatabaseRepository()

        # Check SQLite DB cache first unless force_refresh is True
        if not force_refresh:
            cached_instruments = db_repo.load_instruments_master(today_str)
            if cached_instruments:
                logger.info(f"Loaded {len(cached_instruments)} NSE instruments from SQLite DB cache.")
                return cached_instruments

        logger.info(f"Downloading NSE instruments from {self.INSTRUMENT_NSE_URL}...")
        req = urllib.request.Request(self.INSTRUMENT_NSE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            with gzip.GzipFile(fileobj=resp) as gz:
                content = gz.read().decode("utf-8")
                data = json.loads(content)

        # Write to SQLite DB cache
        try:
            inserted = db_repo.save_instruments_master(data, today_str)
            logger.info(f"Cached {inserted} instruments into SQLite DB table (instruments_master_cache).")
        except Exception as e:
            logger.warning(f"Could not write instruments to DB cache: {e}")

        return data

    def get_historical_daily_candles(
        self,
        instrument_key: str,
        to_date: Optional[str] = None,
        from_date: Optional[str] = None,
    ) -> List[List[Any]]:
        """
        Fetches daily historical candles for an instrument.
        Candle format: [timestamp, open, high, low, close, volume, open_interest]
        """
        if not to_date:
            to_date = date.today().isoformat()
        if not from_date:
            # Default to 30 days back to guarantee previous trading days across holidays
            from_date = (date.today() - timedelta(days=30)).isoformat()

        encoded_key = urllib.parse.quote(instrument_key)
        url = f"{self.BASE_API_V2_URL}/historical-candle/{encoded_key}/day/{to_date}/{from_date}"

        for attempt in range(config.API_RETRY_ATTEMPTS):
            try:
                self.rate_limiter.acquire_sync()
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    result = resp.json()
                    candles = result.get("data", {}).get("candles", [])
                    return candles
                elif resp.status_code == 429:
                    raw_retry = float(resp.headers.get("Retry-After", config.API_RETRY_BACKOFF_BASE * (2 ** attempt)))
                    sleep_time, announced = self.rate_limiter.defer(raw_retry)
                    if announced:
                        logger.warning("Upstox rate limit active; pausing requests for %.0fs.", sleep_time)
                else:
                    logger.error(f"Error fetching daily candles for {instrument_key} (HTTP {resp.status_code}): {resp.text}")
                    break
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {instrument_key}: {e}")
                time.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))

        return []

    def get_intraday_5m_candles(self, instrument_key: str) -> List[List[Any]]:
        """
        Fetches today's native 5-minute candles directly from Upstox History V3 intraday endpoint.
        Candle format: [timestamp, open, high, low, close, volume, open_interest]
        """
        if not self.api_client:
            return []

        try:
            self.rate_limiter.acquire_sync()
            history_api = upstox_client.HistoryV3Api(self.api_client)
            res = history_api.get_intra_day_candle_data(
                instrument_key=instrument_key,
                unit="minutes",
                interval="5",
            )
            if res and res.data and res.data.candles:
                return res.data.candles
        except Exception as e:
            logger.debug(f"HistoryV3 intraday 5m fetch failed for {instrument_key}: {e}")

        return []

    def get_broker_5m_history(
        self,
        instrument_key: str,
        to_date: Optional[str] = None,
        from_date: Optional[str] = None,
    ) -> List[List[Any]]:
        """
        Fetches official 5-minute historical candles from Upstox History V3 API.
        Automatically includes live intraday candles if to_date is today.
        """
        if not self.api_client:
            return []

        today_str = date.today().isoformat()
        if not to_date:
            to_date = today_str
        if not from_date:
            from_date = (date.today() - timedelta(days=7)).isoformat()

        # If requesting only today, use the dedicated intraday endpoint
        if from_date == today_str and to_date == today_str:
            return self.get_intraday_5m_candles(instrument_key)

        try:
            self.rate_limiter.acquire_sync()
            history_api = upstox_client.HistoryV3Api(self.api_client)

            res = history_api.get_historical_candle_data1(
                instrument_key=instrument_key,
                unit="minutes",
                interval="5",
                to_date=to_date,
                from_date=from_date,
            )
            candles = res.data.candles if res and res.data and res.data.candles else []

            # If to_date is today, append live intraday candles
            if to_date == today_str:
                live = self.get_intraday_5m_candles(instrument_key)
                if live:
                    # Deduplicate and combine
                    seen_ts = {c[0] for c in candles}
                    for lc in live:
                        if lc[0] not in seen_ts:
                            candles.append(lc)
            return candles
        except ApiException as e:
            if getattr(e, "status", None) == 429:
                headers = getattr(e, "headers", {}) or {}
                raw_retry = float(headers.get("Retry-After", 60.0))
                retry_after, announced = self.rate_limiter.defer(raw_retry)
                if announced:
                    logger.warning("Upstox rate limit active; pausing requests for %.0fs.", retry_after)
            else:
                logger.debug(f"HistoryV3 5m fetch failed for {instrument_key}: {e}")
        except Exception as e:
            logger.debug(f"HistoryV3 5m fetch failed for {instrument_key}: {e}")

        return []

    def get_quarterly_share_holdings(self, isin: str) -> List[Dict[str, Any]]:
        """Return Upstox quarterly shareholding history for a cash-equity ISIN."""
        isin = str(isin or "").strip().upper()
        if not isin or not self.access_token:
            return []

        encoded_isin = urllib.parse.quote(isin)
        url = f"{self.BASE_API_V2_URL}/fundamentals/{encoded_isin}/share-holdings"
        for attempt in range(config.API_RETRY_ATTEMPTS):
            try:
                self.rate_limiter.acquire_sync()
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    payload = resp.json()
                    data = payload.get("data", [])
                    return data if isinstance(data, list) else []
                if resp.status_code == 429:
                    raw_retry = float(
                        resp.headers.get(
                            "Retry-After",
                            config.API_RETRY_BACKOFF_BASE * (2 ** attempt),
                        )
                    )
                    retry_after, announced = self.rate_limiter.defer(raw_retry)
                    if announced:
                        logger.warning(
                            "Upstox rate limit active; pausing requests for %.0fs.",
                            retry_after,
                        )
                    continue
                logger.warning(
                    "Shareholding fetch failed for %s (HTTP %s): %s",
                    isin,
                    resp.status_code,
                    resp.text[:300],
                )
                break
            except Exception as exc:
                logger.warning(
                    "Shareholding attempt %d failed for %s: %s",
                    attempt + 1,
                    isin,
                    exc,
                )
                time.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))
        return []

    def get_ws_auth_redirect_url(self) -> Optional[str]:
        """
        Retrieves the WebSocket authorized redirect URL for market data streaming.
        """
        if not self.api_client:
            return None

        try:
            ws_api = upstox_client.WebsocketApi(self.api_client)
            res = ws_api.get_market_data_feed_authorize("2.0")
            if res and res.data and res.data.authorized_redirect_uri:
                return res.data.authorized_redirect_uri
        except ApiException as e:
            logger.error(f"Failed to get WebSocket auth redirect URI: (HTTP {e.status}) {e.reason}")
        except Exception as e:
            logger.error(f"WebSocket auth error: {e}")

        return None
