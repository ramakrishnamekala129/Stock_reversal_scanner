"""
NSE Trading Calendar & Automated Historical Candle Gap Detector.
Detects missing trading days and incomplete intraday candle series across
the lookback horizon for F&O universe symbols.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pytz

import config
from database.historical_db import HistoricalCandleDatabase

logger = logging.getLogger(__name__)

# Official known NSE Trading Holidays (Year 2025 - 2026)
NSE_HOLIDAYS: Set[str] = {
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
    "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15", "2025-08-27",
    "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-02-17", "2026-03-03", "2026-03-20", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-08-28", "2026-10-02",
    "2026-10-20", "2026-11-08", "2026-11-24", "2026-12-25",
}


@dataclass
class GapWindow:
    """Represents a discrete date window requiring backfill."""
    symbol: str
    instrument_key: str
    from_date: str  # YYYY-MM-DD
    to_date: str    # YYYY-MM-DD
    missing_dates: List[str]


class GapDetector:
    """
    Identifies missing trading dates and incomplete intraday sessions in the
    historical candle database against the official NSE calendar.
    """

    def __init__(self, db: HistoricalCandleDatabase):
        self.db = db

    @staticmethod
    def get_expected_trading_days(lookback_days: int = 30, end_date: Optional[date] = None) -> List[str]:
        """
        Returns a sorted list of expected NSE trading day date strings (YYYY-MM-DD)
        excluding weekends (Saturday, Sunday) and official NSE holidays.
        """
        if end_date is None:
            kolkata_now = datetime.now(pytz.timezone(config.MARKET_TIMEZONE))
            end_date = kolkata_now.date()

        start_date = end_date - timedelta(days=lookback_days)
        expected = []
        curr = start_date

        while curr <= end_date:
            # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
            if curr.weekday() < 5:
                iso_str = curr.isoformat()
                if iso_str not in NSE_HOLIDAYS:
                    expected.append(iso_str)
            curr += timedelta(days=1)

        return expected

    def detect_symbol_gaps(
        self,
        symbol: str,
        instrument_key: str,
        lookback_days: int = 30,
        min_candles_per_day: int = 360,
    ) -> List[GapWindow]:
        """
        Detects missing or incomplete trading dates for a single symbol and groups
        contiguous missing dates into optimal GapWindow request chunks.
        """
        expected_days = self.get_expected_trading_days(lookback_days=lookback_days)
        if not expected_days:
            return []

        recorded = self.db.get_recorded_dates(symbol)
        today_str = date.today().isoformat()

        missing_days: List[str] = []
        for d in expected_days:
            # For today's date during live market, candles may still be forming so accept whatever is recorded
            if d == today_str:
                if recorded.get(d, 0) == 0:
                    missing_days.append(d)
            else:
                count = recorded.get(d, 0)
                if count < min_candles_per_day:
                    missing_days.append(d)

        if not missing_days:
            return []

        # Group adjacent dates into contiguous windows to minimize Upstox REST calls
        windows: List[GapWindow] = []
        current_group: List[str] = []

        for d in missing_days:
            if not current_group:
                current_group.append(d)
            else:
                prev_d = datetime.strptime(current_group[-1], "%Y-%m-%d").date()
                curr_d = datetime.strptime(d, "%Y-%m-%d").date()
                # If within 4 calendar days (accounting for weekend gaps)
                if (curr_d - prev_d).days <= 4:
                    current_group.append(d)
                else:
                    windows.append(GapWindow(
                        symbol=symbol,
                        instrument_key=instrument_key,
                        from_date=current_group[0],
                        to_date=current_group[-1],
                        missing_dates=list(current_group),
                    ))
                    current_group = [d]

        if current_group:
            windows.append(GapWindow(
                symbol=symbol,
                instrument_key=instrument_key,
                from_date=current_group[0],
                to_date=current_group[-1],
                missing_dates=list(current_group),
            ))

        return windows

    def detect_universe_gaps(
        self,
        universe: Dict[str, Dict[str, Any]],
        lookback_days: int = 30,
    ) -> Dict[str, List[GapWindow]]:
        """
        Scans all symbols in universe and returns mapping of {symbol: [GapWindow]}.
        """
        universe_gaps: Dict[str, List[GapWindow]] = {}
        for sym, item in universe.items():
            inst_key = item.get("instrument_key", "")
            if not inst_key:
                continue
            gaps = self.detect_symbol_gaps(sym, inst_key, lookback_days=lookback_days)
            if gaps:
                universe_gaps[sym] = gaps

        return universe_gaps
