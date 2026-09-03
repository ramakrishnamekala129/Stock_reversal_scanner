"""
Unit tests for NSE F&O universe filtering and instrument extraction.
"""

from unittest.mock import MagicMock
from market.instruments import InstrumentManager


def test_fno_universe_filtering():
    mock_rest = MagicMock()
    mock_rest.download_nse_instruments.return_value = [
        # 1. Equity futures contract (near month)
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "underlying_type": "EQUITY",
            "trading_symbol": "RELIANCE FUT 29 SEP 26",
            "underlying_symbol": "RELIANCE",
            "instrument_key": "NSE_FO|68777",
            "expiry": 1790706599000,
            "lot_size": 250,
        },
        # 1b. Equity futures contract (far month)
        {
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "underlying_type": "EQUITY",
            "trading_symbol": "RELIANCE FUT 27 OCT 26",
            "underlying_symbol": "RELIANCE",
            "instrument_key": "NSE_FO|48987",
            "expiry": 1793125799000,
            "lot_size": 250,
        },
        # 1c. Equity cash stock
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018", "lot_size": 1},

        # 2. Equity stock NOT in F&O (cash only)
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "ZOMATO_NON_FNO", "instrument_key": "NSE_EQ|INE000000000", "lot_size": 1},

        # 3. Index in F&O (should be excluded from equity scanner)
        {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_type": "INDEX", "trading_symbol": "NIFTY FUT 29 SEP 26", "underlying_symbol": "NIFTY", "instrument_key": "NSE_FO|99999", "expiry": 1790706599000},
        {"segment": "NSE_INDEX", "instrument_type": "INDEX", "trading_symbol": "NIFTY 50", "instrument_key": "NSE_INDEX|Nifty 50"},

        # 4. ETF (should be excluded)
        {"segment": "NSE_EQ", "instrument_type": "ETF", "trading_symbol": "NIFTYBEES", "instrument_key": "NSE_EQ|INF732E01015"},
    ]

    mgr = InstrumentManager(mock_rest)

    # Test 1: Default FUTURES mode -> picks nearest contract (29 SEP)
    universe_fut = mgr.load_fno_universe(force_refresh=True, mode="FUTURES")
    assert len(universe_fut) == 1
    assert "RELIANCE" in universe_fut
    assert "ZOMATO_NON_FNO" not in universe_fut
    assert "NIFTY 50" not in universe_fut
    rel_fut = universe_fut["RELIANCE"]
    assert rel_fut["instrument_key"] == "NSE_FO|68777"  # Nearest contract
    assert rel_fut["trading_symbol"] == "RELIANCE FUT 29 SEP 26"
    assert rel_fut["segment"] == "NSE_FO"
    assert rel_fut["instrument_type"] == "FUT"

    # Test 2: SPOT mode -> picks cash equity instrument
    mgr.set_mode("SPOT")
    universe_spot = mgr.universe
    assert len(universe_spot) == 1
    rel_spot = universe_spot["RELIANCE"]
    assert rel_spot["instrument_key"] == "NSE_EQ|INE002A01018"
    assert rel_spot["trading_symbol"] == "RELIANCE"
    assert rel_spot["segment"] == "NSE_EQ"
    assert rel_spot["instrument_type"] == "EQ"
