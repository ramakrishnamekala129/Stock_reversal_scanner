"""
Dedicated High-Performance SQLite Historical Candle Database.
Stores multi-day 1-minute historical candles for all F&O universe symbols with
automatic deduplication, indexing, and sync metadata tracking.
"""

import logging
import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pandas as pd
import pytz

import config

logger = logging.getLogger(__name__)


class HistoricalCandleDatabase:
    """Thread-safe, WAL-accelerated SQLite storage for multi-day historical candles."""

    def __init__(self, db_path: Path = config.HISTORICAL_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Retrieves or establishes a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("PRAGMA cache_size = -64000;")  # 64MB RAM cache
                conn.execute("PRAGMA temp_store = MEMORY;")
                conn.execute("PRAGMA mmap_size = 268435456;")  # 256MB memory-mapped I/O
            except Exception:
                pass
            self._local.conn = conn
        return self._local.conn

    def init_schema(self):
        """Creates tables and indexes for historical candles and sync tracking."""
        conn = self._get_connection()
        with conn:
            # 1. Multi-Day 1-Minute Historical Candles
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles_history_1m (
                    symbol TEXT NOT NULL,
                    instrument_key TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    oi INTEGER DEFAULT 0,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_sym_ts ON candles_history_1m(symbol, timestamp)")

            # 2. Daily Sync Metadata for Gap Detection
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candle_sync_meta (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    candle_count INTEGER NOT NULL,
                    is_complete INTEGER NOT NULL,
                    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_sym_date ON candle_sync_meta(symbol, date)")

            # 3. Multi-Year Daily Historical Candles Cache (replaces all_daily_candles_2026.json)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles_history_daily (
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    oi INTEGER DEFAULT 0,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_daily_sym_ts ON candles_history_daily(symbol, timestamp)")

    def save_candles_batch(
        self,
        symbol: str,
        instrument_key: str,
        candle_records: List[Union[List[Any], Dict[str, Any]]],
    ) -> int:
        """
        Batch saves raw 1-minute historical candles from Upstox REST or broker feed.
        Candle format can be [timestamp, open, high, low, close, volume, oi] or dict.
        Returns the number of candles inserted/updated.
        """
        if not candle_records:
            return 0

        rows = []
        affected_dates: Set[str] = set()

        for c in candle_records:
            if isinstance(c, (list, tuple)):
                if len(c) < 6:
                    continue
                ts_str = str(c[0])
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                v = int(c[5])
                oi = int(c[6]) if len(c) > 6 else 0
            elif isinstance(c, dict):
                ts_str = str(c.get("timestamp", ""))
                o, h, l, cl = float(c.get("open", 0.0)), float(c.get("high", 0.0)), float(c.get("low", 0.0)), float(c.get("close", 0.0))
                v = int(c.get("volume", 0))
                oi = int(c.get("oi", 0))
            else:
                continue

            # Standardize timestamp string to ISO
            date_part = ts_str[:10]
            affected_dates.add(date_part)
            rows.append((symbol, instrument_key, ts_str, o, h, l, cl, v, oi))

        if not rows:
            return 0

        conn = self._get_connection()
        try:
            with conn:
                conn.executemany("""
                    INSERT INTO candles_history_1m (symbol, instrument_key, timestamp, open, high, low, close, volume, oi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        oi = excluded.oi
                """, rows)

                # Recompute sync metadata for affected dates
                for d_str in affected_dates:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT COUNT(*) FROM candles_history_1m
                        WHERE symbol = ? AND timestamp LIKE ?
                    """, (symbol, f"{d_str}%"))
                    cnt = cur.fetchone()[0]
                    # Full NSE session is 375 1-minute bars (09:15 to 15:30)
                    is_complete = 1 if cnt >= 360 else 0
                    conn.execute("""
                        INSERT INTO candle_sync_meta (symbol, date, candle_count, is_complete, last_synced_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(symbol, date) DO UPDATE SET
                            candle_count = excluded.candle_count,
                            is_complete = excluded.is_complete,
                            last_synced_at = CURRENT_TIMESTAMP
                    """, (symbol, d_str, cnt, is_complete))

            return len(rows)
        except Exception as e:
            logger.error(f"Failed to batch insert historical candles for {symbol}: {e}")
            return 0

    def get_candles_by_symbol(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Retrieves 1-minute historical candles for a symbol ordered chronologically.
        Returns pd.DataFrame with tz-aware timestamps in Asia/Kolkata.
        """
        conn = self._get_connection()
        query = "SELECT timestamp, open, high, low, close, volume, oi FROM candles_history_1m WHERE symbol = ?"
        params: List[Any] = [symbol]

        if from_date:
            query += " AND timestamp >= ?"
            params.append(f"{from_date}T00:00:00" if "T" not in from_date else from_date)
        if to_date:
            query += " AND timestamp <= ?"
            params.append(f"{to_date}T23:59:59" if "T" not in to_date else to_date)

        query += " ORDER BY timestamp ASC"
        if limit:
            query += f" LIMIT {int(limit)}"

        try:
            df = pd.read_sql_query(query, conn, params=params)
            if df.empty:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

            kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None).dt.tz_localize(kolkata_tz)
            return df
        except Exception as e:
            logger.error(f"Error querying historical candles for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    def get_recorded_dates(self, symbol: str) -> Dict[str, int]:
        """Returns a dict of {date_str: candle_count} recorded in SQLite for this symbol."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT date, candle_count FROM candle_sync_meta WHERE symbol = ?", (symbol,))
            return {row["date"]: row["candle_count"] for row in cur.fetchall()}
        except Exception as e:
            logger.debug(f"Error checking sync meta for {symbol}: {e}")
            return {}

    def get_date_candle_count(self, symbol: str, date_str: str) -> int:
        """Returns the number of candles present for a specific symbol on a specific date."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT candle_count FROM candle_sync_meta
                WHERE symbol = ? AND date = ?
            """, (symbol, date_str))
            row = cur.fetchone()
            if row:
                return row[0]
            # Fallback direct count
            cur.execute("""
                SELECT COUNT(*) FROM candles_history_1m
                WHERE symbol = ? AND timestamp LIKE ?
            """, (symbol, f"{date_str}%"))
            r = cur.fetchone()
            return r[0] if r else 0
        except Exception:
            return 0

    def get_overall_stats(self) -> Dict[str, Any]:
        """Returns total symbols, total candles, and date range in database."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles_history_1m")
            row = cur.fetchone()
            return {
                "total_symbols": row[0] if row else 0,
                "total_candles": row[1] if row else 0,
                "earliest_candle": row[2] if row else None,
                "latest_candle": row[3] if row else None,
            }
        except Exception as e:
            logger.debug(f"Error retrieving DB stats: {e}")
            return {"total_symbols": 0, "total_candles": 0}

    def save_daily_candles_batch(
        self,
        symbol: str,
        candle_records: List[Union[List[Any], Dict[str, Any]]],
    ) -> int:
        """Batch saves daily historical candles into SQLite candles_history_daily table."""
        if not candle_records:
            return 0
        rows = []
        for c in candle_records:
            if isinstance(c, (list, tuple)):
                if len(c) < 6:
                    continue
                ts_str = str(c[0])
                o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                v = int(c[5])
                oi = int(c[6]) if len(c) > 6 else 0
            elif isinstance(c, dict):
                ts_str = str(c.get("timestamp", ""))
                o = float(c.get("open", 0.0))
                h = float(c.get("high", 0.0))
                l = float(c.get("low", 0.0))
                cl = float(c.get("close", 0.0))
                v = int(c.get("volume", 0))
                oi = int(c.get("oi", 0))
            else:
                continue
            rows.append((symbol, ts_str, o, h, l, cl, v, oi))

        if not rows:
            return 0

        conn = self._get_connection()
        try:
            with conn:
                conn.executemany("""
                    INSERT INTO candles_history_daily (symbol, timestamp, open, high, low, close, volume, oi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        oi = excluded.oi
                """, rows)
            return len(rows)
        except Exception as e:
            logger.error(f"Failed to batch insert daily candles for {symbol}: {e}")
            return 0

    def save_all_daily_candles_bulk(self, all_candles_map: Dict[str, List[Any]]) -> int:
        """Bulk inserts multi-year daily candles across entire universe in a single SQLite transaction."""
        total_rows = []
        for sym, candles in all_candles_map.items():
            for c in candles:
                if isinstance(c, (list, tuple)) and len(c) >= 6:
                    total_rows.append((sym, str(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5]), int(c[6]) if len(c) > 6 else 0))
                elif isinstance(c, dict):
                    total_rows.append((sym, str(c.get("timestamp", "")), float(c.get("open", 0)), float(c.get("high", 0)), float(c.get("low", 0)), float(c.get("close", 0)), int(c.get("volume", 0)), int(c.get("oi", 0))))

        if not total_rows:
            return 0

        conn = self._get_connection()
        try:
            with conn:
                conn.executemany("""
                    INSERT INTO candles_history_daily (symbol, timestamp, open, high, low, close, volume, oi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        oi = excluded.oi
                """, total_rows)
            return len(total_rows)
        except Exception as e:
            logger.error(f"Failed to bulk insert daily candles: {e}")
            return 0

    def get_daily_candles(self, symbol: str) -> pd.DataFrame:
        """Retrieves daily candles for a symbol sorted chronologically."""
        conn = self._get_connection()
        try:
            df = pd.read_sql_query(
                "SELECT timestamp, open, high, low, close, volume, oi FROM candles_history_daily WHERE symbol = ? ORDER BY timestamp ASC",
                conn,
                params=[symbol],
            )
            if df.empty:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df
        except Exception as e:
            logger.error(f"Failed to get daily candles for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    def get_all_daily_candles_map(self) -> Dict[str, pd.DataFrame]:
        """Loads all daily candles from SQLite DB across all symbols into DataFrames."""
        conn = self._get_connection()
        try:
            df = pd.read_sql_query(
                "SELECT symbol, timestamp, open, high, low, close, volume, oi FROM candles_history_daily ORDER BY symbol, timestamp ASC",
                conn,
            )
            if df.empty:
                return {}
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            dfs = {}
            for sym, group in df.groupby("symbol"):
                dfs[sym] = group.reset_index(drop=True)
            return dfs
        except Exception as e:
            logger.error(f"Failed to load daily candles from DB: {e}")
            return {}

    def get_daily_candles_count(self) -> int:
        """Returns total daily candles in SQLite DB."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM candles_history_daily")
            row = cur.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
