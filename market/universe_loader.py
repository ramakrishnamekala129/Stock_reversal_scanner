"""
NSE Index Universe Loader and Offline Cache Manager.
Fetches and caches official NSE index constituent lists for Nifty 500, Nifty LargeMidcap 250,
and F&O Option Stocks.
"""

import csv
import io
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
import urllib.request

import config

logger = logging.getLogger(__name__)

UNIVERSE_DIR = config.DATA_DIR / "universes"
UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)

NIFTY_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
NIFTY_250_URL = "https://archives.nseindia.com/content/indices/ind_niftylargemidcap250list.csv"


class UniverseLoader:
    """Manages downloading, parsing, and caching of NSE equity universes."""

    def __init__(self, cache_dir: Path = UNIVERSE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_url_with_fallback(self, url: str) -> Optional[str]:
        """Fetches CSV content from NSE archives with appropriate headers."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None

    def get_nifty_500_symbols(self, force_refresh: bool = False) -> List[str]:
        """
        Returns list of trading symbols for NIFTY 500 constituents.
        Uses cached CSV if present, otherwise downloads from NSE.
        """
        cache_file = self.cache_dir / "nifty_500.csv"
        if not force_refresh and cache_file.exists():
            symbols = self._parse_csv_symbols(cache_file.read_text(encoding="utf-8"))
            if len(symbols) >= 400:
                return symbols

        content = self._fetch_url_with_fallback(NIFTY_500_URL)
        if content:
            cache_file.write_text(content, encoding="utf-8")
            symbols = self._parse_csv_symbols(content)
            if symbols:
                logger.info(f"Loaded {len(symbols)} NIFTY 500 constituents from NSE.")
                return symbols

        # If download fails, check fallback cache
        if cache_file.exists():
            return self._parse_csv_symbols(cache_file.read_text(encoding="utf-8"))
        return []

    def get_nifty_250_symbols(self, force_refresh: bool = False) -> List[str]:
        """
        Returns list of trading symbols for NIFTY LargeMidcap 250 constituents.
        Uses cached CSV if present, otherwise downloads from NSE.
        """
        cache_file = self.cache_dir / "nifty_250.csv"
        if not force_refresh and cache_file.exists():
            symbols = self._parse_csv_symbols(cache_file.read_text(encoding="utf-8"))
            if len(symbols) >= 200:
                return symbols

        content = self._fetch_url_with_fallback(NIFTY_250_URL)
        if content:
            cache_file.write_text(content, encoding="utf-8")
            symbols = self._parse_csv_symbols(content)
            if symbols:
                logger.info(f"Loaded {len(symbols)} NIFTY 250 constituents from NSE.")
                return symbols

        if cache_file.exists():
            return self._parse_csv_symbols(cache_file.read_text(encoding="utf-8"))
        return []

    @staticmethod
    def _parse_csv_symbols(csv_text: str) -> List[str]:
        """Parses trading symbols from NSE constituent CSV."""
        symbols = []
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                # NSE index CSVs typically use 'Symbol' column
                sym = row.get("Symbol") or row.get("symbol") or row.get("SYMBOL")
                if sym:
                    clean_sym = sym.strip().upper()
                    if clean_sym and clean_sym not in symbols:
                        symbols.append(clean_sym)
        except Exception as e:
            logger.warning(f"Error parsing NSE CSV symbols: {e}")
        return symbols
