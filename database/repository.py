"""
SQLite Database Repository for Intraday Candles, Daily Levels, and Scanner Signals.
"""

import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

import pandas as pd
import pytz

import config

logger = logging.getLogger(__name__)


class DatabaseRepository:
    """Thread-safe SQLite storage for scanner artifacts."""

    def __init__(self, db_path: Path = config.DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.init_schema()

    def _get_connection(self) -> sqlite3.Connection:
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
                conn.execute("PRAGMA cache_size = -64000;")
                conn.execute("PRAGMA temp_store = MEMORY;")
            except Exception:
                pass
            self._local.conn = conn
        return self._local.conn

    def init_schema(self):
        """Initializes tables and indexes."""
        conn = self._get_connection()
        with conn:
            # 1. candles_5m table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles_5m (
                    symbol TEXT NOT NULL,
                    instrument_key TEXT,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    is_closed INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_ts ON candles_5m(timestamp)")

            # 2. daily_levels table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_levels (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    pdo REAL,
                    pdh REAL,
                    pdl REAL,
                    pdc REAL,
                    pdv INTEGER,
                    pivot REAL,
                    r1 REAL,
                    r2 REAL,
                    r3 REAL,
                    s1 REAL,
                    s2 REAL,
                    s3 REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)

            # 3. scanner_signals table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scanner_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    score INTEGER NOT NULL,
                    pivot REAL,
                    pdh REAL,
                    pdl REAL,
                    r1 REAL,
                    r2 REAL,
                    s1 REAL,
                    s2 REAL,
                    relative_volume REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, timestamp, pattern)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_sym ON scanner_signals(symbol)")

    def save_candle(self, candle_dict: Dict[str, Any]):
        """Persists or updates a 5-minute candle."""
        if not config.ENABLE_DB_STORAGE:
            return
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO candles_5m (symbol, instrument_key, timestamp, open, high, low, close, volume, is_closed)
                    VALUES (:symbol, :instrument_key, :timestamp, :open, :high, :low, :close, :volume, :is_closed)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        is_closed = excluded.is_closed
                """, candle_dict)
        except Exception as e:
            logger.debug(f"DB error saving candle for {candle_dict.get('symbol')}: {e}")

    def save_candles_batch(self, candle_records: List[Dict[str, Any]]):
        """Batch inserts or updates multiple 5-minute candles in a single transaction."""
        if not config.ENABLE_DB_STORAGE or not candle_records:
            return
        conn = self._get_connection()
        try:
            with conn:
                conn.executemany("""
                    INSERT INTO candles_5m (symbol, instrument_key, timestamp, open, high, low, close, volume, is_closed)
                    VALUES (:symbol, :instrument_key, :timestamp, :open, :high, :low, :close, :volume, :is_closed)
                    ON CONFLICT(symbol, timestamp) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        is_closed = excluded.is_closed
                """, candle_records)
        except Exception as e:
            logger.error(f"DB error in save_candles_batch: {e}")

    def get_candles_by_date(self, date_str: str) -> Dict[str, pd.DataFrame]:
        """
        Retrieves all 5-minute candles for a specific date (YYYY-MM-DD) from SQLite,
        returning a mapping of symbol -> pd.DataFrame with Kolkata timezone timestamps.
        Optimized with vectorized pandas SQL retrieval (sub-50ms for entire universe).
        """
        conn = self._get_connection()
        query = """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM candles_5m
            WHERE timestamp LIKE ?
            ORDER BY symbol, timestamp ASC
        """
        try:
            df_all = pd.read_sql_query(query, conn, params=(f"{date_str}%",))
            if df_all.empty:
                return {}

            kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
            df_all["timestamp"] = pd.to_datetime(df_all["timestamp"], utc=True).dt.tz_convert(kolkata_tz)
            return {sym: group.copy().reset_index(drop=True) for sym, group in df_all.groupby("symbol")}
        except Exception as e:
            logger.error(f"Error querying candles from SQLite: {e}")
            return {}

    def get_candles_by_symbol(self, symbol: str, limit: int = 150) -> pd.DataFrame:
        """Retrieves 5-minute candles for a specific symbol ordered chronologically."""
        conn = self._get_connection()
        query = """
            SELECT timestamp, open, high, low, close, volume, is_closed
            FROM candles_5m
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        try:
            df = pd.read_sql_query(query, conn, params=(symbol, limit))
            if df.empty:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "is_closed"])
            kolkata_tz = pytz.timezone(config.MARKET_TIMEZONE)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(kolkata_tz)
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error querying candles for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "is_closed"])

    def save_daily_levels(self, levels_dict: Dict[str, Any]):
        """Persists daily pivot levels."""
        if not config.ENABLE_DB_STORAGE:
            return
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT INTO daily_levels (
                        symbol, date, pdo, pdh, pdl, pdc, pdv,
                        pivot, r1, r2, r3, s1, s2, s3
                    )
                    VALUES (
                        :symbol, :date, :pdo, :pdh, :pdl, :pdc, :pdv,
                        :pivot, :r1, :r2, :r3, :s1, :s2, :s3
                    )
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        pdo = excluded.pdo,
                        pdh = excluded.pdh,
                        pdl = excluded.pdl,
                        pdc = excluded.pdc,
                        pdv = excluded.pdv,
                        pivot = excluded.pivot,
                        r1 = excluded.r1,
                        r2 = excluded.r2,
                        r3 = excluded.r3,
                        s1 = excluded.s1,
                        s2 = excluded.s2,
                        s3 = excluded.s3
                """, levels_dict)
        except Exception as e:
            logger.debug(f"DB error saving daily levels: {e}")

    def save_signal(self, signal_dict: Dict[str, Any]):
        """Persists an alert signal."""
        if not config.ENABLE_DB_STORAGE:
            return
        conn = self._get_connection()
        try:
            with conn:
                conn.execute("""
                    INSERT OR IGNORE INTO scanner_signals (
                        symbol, timestamp, pattern, direction, price, score,
                        pivot, pdh, pdl, r1, r2, s1, s2, relative_volume
                    )
                    VALUES (
                        :symbol, :timestamp, :pattern, :direction, :price, :score,
                        :pivot, :pdh, :pdl, :r1, :r2, :s1, :s2, :relative_volume
                    )
                """, signal_dict)
        except Exception as e:
            logger.error(f"DB error saving signal: {e}")

    def get_top_signals(self, min_score: int = 4, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns highest scored reversal setups ordered by timestamp descending."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM scanner_signals
            WHERE score >= ?
            ORDER BY timestamp DESC, score DESC
            LIMIT ?
        """, (min_score, limit))
        return [dict(row) for row in cur.fetchall()]

    def get_signals_by_symbol(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns all reversal signals for a specific symbol."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM scanner_signals
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (symbol.upper(), limit))
        return [dict(row) for row in cur.fetchall()]

    def get_signal_stats(self) -> Dict[str, Any]:
        """Returns aggregated signal counts grouped by direction and pattern."""
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total_signals,
                SUM(CASE WHEN direction LIKE '%BULLISH%' THEN 1 ELSE 0 END) as bullish_count,
                SUM(CASE WHEN direction LIKE '%BEARISH%' THEN 1 ELSE 0 END) as bearish_count,
                AVG(score) as avg_score,
                MAX(score) as max_score
            FROM scanner_signals
        """)
        row = cur.fetchone()
        return dict(row) if row else {}
