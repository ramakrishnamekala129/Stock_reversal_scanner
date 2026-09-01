"""
Unit tests for NSE F&O universe filtering and instrument extraction.
"""

from unittest.mock import MagicMock
from market.instruments import InstrumentManager


def test_fno_universe_filtering():
    mock_rest = MagicMock()
    mock_rest.download_nse_instruments.return_value = [
        # 1. Equity stock in F&O
        {"segment": "NSE_FO", "underlying_type": "EQUITY", "trading_symbol": "RELIANCE 26OCT FUT", "underlying_symbol": "RELIANCE"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018", "lot_size": 250},

        # 2. Equity stock NOT in F&O (cash only)
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "ZOMATO_NON_FNO", "instrument_key": "NSE_EQ|INE000000000", "lot_size": 1},

        # 3. Index in F&O (should be excluded from cash equity scanner)
        {"segment": "NSE_FO", "underlying_type": "INDEX", "trading_symbol": "NIFTY 26OCT FUT", "underlying_symbol": "NIFTY"},
        {"segment": "NSE_INDEX", "instrument_type": "INDEX", "trading_symbol": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50"},

        # 4. ETF (should be excluded)
        {"segment": "NSE_EQ", "instrument_type": "ETF", "trading_symbol": "NIFTYBEES", "instrument_key": "NSE_EQ|INF732E01015"},
    ]

    mgr = InstrumentManager(mock_rest)
    universe = mgr.load_fno_universe(force_refresh=True)

    # Only RELIANCE should be present
    assert len(universe) == 1
    assert "RELIANCE" in universe
    assert "ZOMATO_NON_FNO" not in universe
    assert "NIFTY 50" not in universe
    assert "NIFTYBEES" not in universe

    rel_info = universe["RELIANCE"]
    assert rel_info["is_fno"] is True
    assert rel_info["instrument_key"] == "NSE_EQ|INE002A01018"
    assert rel_info["exchange"] == "NSE"
