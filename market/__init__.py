"""
Market data, instrument universes, and candle engine package.
"""

from importlib import import_module

__all__ = [
    "InstrumentManager",
    "FNOInstrument",
    "HistoricalDataLoader",
    "PreviousDayOHLCV",
    "CandleEngine",
    "Candle",
    "CandleStatus",
    "MarketSessionManager",
]

_EXPORT_MODULES = {
    "InstrumentManager": "market.instruments",
    "FNOInstrument": "market.instruments",
    "HistoricalDataLoader": "market.historical",
    "PreviousDayOHLCV": "market.historical",
    "CandleEngine": "market.candle_engine",
    "Candle": "market.candle_engine",
    "CandleStatus": "market.candle_engine",
    "MarketSessionManager": "market.session",
}


def __getattr__(name):
    """Load public classes lazily so submodules cannot create import cycles."""
    module_name = _EXPORT_MODULES.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
