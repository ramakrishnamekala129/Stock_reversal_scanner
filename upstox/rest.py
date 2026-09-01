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

logger = logging.getLogger(__name__)


class UpstoxRestClient:
    """REST API wrapper for Upstox V2 historical and market data endpoints."""

    INSTRUMENT_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    BASE_API_V2_URL = "https://api.upstox.com/v2"

    def __init__(self, api_client: Optional[upstox_client.ApiClient] = None):
        self.api_client = api_client
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Upstox-FNO-Scanner/1.0",
        })
        if api_client and api_client.configuration and api_client.configuration.access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {api_client.configuration.access_token}"
            })

    def download_nse_instruments(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Downloads and caches the official Upstox NSE instrument master.
        Returns a list of raw instrument dictionaries.
        """
        cache_file = config.CACHE_DIR / f"nse_instruments_{date.today().isoformat()}.json"

        # Check local cache first unless force_refresh is True
        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"Loaded {len(data)} NSE instruments from local cache: {cache_file.name}")
                    return data
            except Exception as e:
                logger.warning(f"Failed to read cached instruments: {e}. Downloading fresh copy...")

        logger.info(f"Downloading NSE instruments from {self.INSTRUMENT_NSE_URL}...")
        req = urllib.request.Request(self.INSTRUMENT_NSE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            with gzip.GzipFile(fileobj=resp) as gz:
                content = gz.read().decode("utf-8")
                data = json.loads(content)

        # Write to cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.info(f"Cached {len(data)} instruments to {cache_file.name}")
        except Exception as e:
            logger.warning(f"Could not write instruments to cache: {e}")

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
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    result = resp.json()
                    candles = result.get("data", {}).get("candles", [])
                    return candles
                elif resp.status_code == 429:
                    sleep_time = config.API_RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(f"Rate limited (429) on {instrument_key}, sleeping {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Error fetching daily candles for {instrument_key} (HTTP {resp.status_code}): {resp.text}")
                    break
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {instrument_key}: {e}")
                time.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))

        return []

    def get_intraday_1m_candles(self, instrument_key: str) -> List[List[Any]]:
        """
        Fetches today's intraday 1-minute historical candles directly from Upstox broker.
        Candle format: [timestamp, open, high, low, close, volume, open_interest]
        """
        encoded_key = urllib.parse.quote(instrument_key)
        url = f"{self.BASE_API_V2_URL}/historical-candle/intraday/{encoded_key}/1minute"

        for attempt in range(config.API_RETRY_ATTEMPTS):
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200:
                    result = resp.json()
                    candles = result.get("data", {}).get("candles", [])
                    return candles
                elif resp.status_code == 429:
                    sleep_time = config.API_RETRY_BACKOFF_BASE * (2 ** attempt)
                    time.sleep(sleep_time)
                else:
                    break
            except Exception as e:
                time.sleep(config.API_RETRY_BACKOFF_BASE * (attempt + 1))

        return []

    def get_broker_5m_history(
        self,
        instrument_key: str,
        to_date: Optional[str] = None,
        from_date: Optional[str] = None,
    ) -> List[List[Any]]:
        """
        Fetches official 5-minute historical candles from Upstox History V3 API.
        """
        if not self.api_client:
            return []

        try:
            history_api = upstox_client.HistoryV3Api(self.api_client)
            if not to_date:
                to_date = date.today().isoformat()
            if not from_date:
                from_date = (date.today() - timedelta(days=7)).isoformat()

            res = history_api.get_historical_candle_data1(
                instrument_key=instrument_key,
                unit="minutes",
                interval="5",
                to_date=to_date,
                from_date=from_date,
            )
            if res and res.data and res.data.candles:
                return res.data.candles
        except Exception as e:
            logger.debug(f"HistoryV3 5m fetch failed for {instrument_key}: {e}")

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
