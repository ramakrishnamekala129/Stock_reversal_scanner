"""
NSE F&O Instrument Universe Manager.
Downloads Upstox instrument master, extracts active equity F&O symbols,
and constructs clean internal lookup dictionaries.
"""

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set
from upstox.rest import UpstoxRestClient

logger = logging.getLogger(__name__)


@dataclass
class FNOInstrument:
    """Structure for an active NSE F&O Equity Stock."""
    trading_symbol: str
    instrument_key: str
    exchange: str = "NSE"
    is_fno: bool = True
    lot_size: Optional[int] = None
    tick_size: Optional[float] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "exchange": self.exchange,
            "is_fno": self.is_fno,
        }


class InstrumentManager:
    """Manages downloading, filtering, and caching NSE F&O equity universe."""

    def __init__(self, rest_client: UpstoxRestClient):
        self.rest_client = rest_client
        self._universe: Dict[str, FNOInstrument] = {}
        self._key_to_symbol: Dict[str, str] = {}

    @property
    def universe(self) -> Dict[str, Dict[str, Any]]:
        """Returns internal dictionary of F&O symbols."""
        return {sym: inst.to_dict() for sym, inst in self._universe.items()}

    @property
    def key_to_symbol_map(self) -> Dict[str, str]:
        """Returns mapping from instrument_key to trading_symbol."""
        return self._key_to_symbol

    def load_fno_universe(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        Loads NSE instrument master, filters active F&O equities,
        and constructs internal lookup tables.
        """
        raw_instruments = self.rest_client.download_nse_instruments(force_refresh=force_refresh)
        if not raw_instruments:
            logger.error("No instruments retrieved from Upstox master.")
            return {}

        # 1. Identify all active equity underlyings present in NSE_FO segment
        fno_equity_underlyings: Set[str] = set()
        for item in raw_instruments:
            segment = item.get("segment")
            underlying_type = item.get("underlying_type") or item.get("asset_type")
            
            # Only consider EQUITY derivatives (exclude INDEX, CURRENCY, COMMODITY)
            if segment == "NSE_FO" and underlying_type == "EQUITY":
                sym = item.get("underlying_symbol") or item.get("asset_symbol")
                if sym:
                    fno_equity_underlyings.add(sym.strip())

        logger.info(f"Identified {len(fno_equity_underlyings)} active equity symbols in NSE_FO segment.")

        # 2. Match with NSE_EQ equity cash instruments (to scan the underlying equity stock)
        self._universe.clear()
        self._key_to_symbol.clear()

        for item in raw_instruments:
            segment = item.get("segment")
            symbol = item.get("trading_symbol")
            instrument_type = item.get("instrument_type")

            # Must be NSE_EQ, EQ type (exclude BE, SM, SG, ETF, GS, etc.) and in F&O set
            if segment == "NSE_EQ" and instrument_type == "EQ" and symbol in fno_equity_underlyings:
                inst_key = item.get("instrument_key")
                if not inst_key or not symbol:
                    continue

                fno_inst = FNOInstrument(
                    trading_symbol=symbol,
                    instrument_key=inst_key,
                    exchange="NSE",
                    is_fno=True,
                    lot_size=item.get("lot_size"),
                    tick_size=item.get("tick_size"),
                    name=item.get("name"),
                )
                self._universe[symbol] = fno_inst
                self._key_to_symbol[inst_key] = symbol

        logger.info(f"Successfully loaded and verified {len(self._universe)} NSE F&O cash equity stocks.")
        return self.universe

    def get_instrument_keys(self) -> List[str]:
        """Returns list of all active F&O instrument keys."""
        return [inst.instrument_key for inst in self._universe.values()]

    def get_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Retrieves instrument dataclass for a given symbol."""
        return self._universe.get(symbol)
