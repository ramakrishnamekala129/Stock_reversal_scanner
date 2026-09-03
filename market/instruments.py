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
    """Structure for an active NSE F&O Instrument (Spot Equity or Nearest Future)."""
    trading_symbol: str
    instrument_key: str
    underlying_symbol: str
    exchange: str = "NSE"
    segment: str = "NSE_FO"  # "NSE_FO" or "NSE_EQ"
    instrument_type: str = "FUT"  # "FUT" or "EQ"
    is_fno: bool = True
    expiry: Optional[int] = None
    expiry_date: Optional[str] = None
    lot_size: Optional[int] = None
    tick_size: Optional[float] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "underlying_symbol": self.underlying_symbol,
            "exchange": self.exchange,
            "segment": self.segment,
            "instrument_type": self.instrument_type,
            "is_fno": self.is_fno,
            "expiry": self.expiry,
            "expiry_date": self.expiry_date,
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "name": self.name,
        }


class InstrumentManager:
    """Manages downloading, filtering, caching, and pairing NSE F&O futures and spot universe."""

    def __init__(self, rest_client: UpstoxRestClient):
        self.rest_client = rest_client
        self._universe: Dict[str, FNOInstrument] = {}
        self._spot_universe: Dict[str, FNOInstrument] = {}
        self._futures_universe: Dict[str, FNOInstrument] = {}
        self._key_to_symbol: Dict[str, str] = {}
        self._current_mode: str = "FUTURES"

    @property
    def universe(self) -> Dict[str, Dict[str, Any]]:
        """Returns internal dictionary of active mode F&O instruments."""
        return {sym: inst.to_dict() for sym, inst in self._universe.items()}

    @property
    def key_to_symbol_map(self) -> Dict[str, str]:
        """Returns mapping from instrument_key to underlying_symbol."""
        return self._key_to_symbol

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def load_fno_universe(self, force_refresh: bool = False, mode: str = "FUTURES") -> Dict[str, Dict[str, Any]]:
        """
        Loads NSE instrument master, filters active F&O equities, pairs nearest
        monthly futures contracts with cash spot equity stocks, and sets active universe.
        Mode: 'FUTURES' (default, nearest active contract) or 'SPOT' (cash equity).
        """
        self._current_mode = mode.upper() if mode else "FUTURES"
        raw_instruments = self.rest_client.download_nse_instruments(force_refresh=force_refresh)
        if not raw_instruments:
            logger.error("No instruments retrieved from Upstox master.")
            return {}

        # 1. Collect all active equity futures grouped by underlying symbol
        fut_by_underlying: Dict[str, List[Dict[str, Any]]] = {}
        for item in raw_instruments:
            segment = item.get("segment")
            underlying_type = item.get("underlying_type") or item.get("asset_type")
            inst_type = item.get("instrument_type")

            if segment == "NSE_FO" and inst_type == "FUT" and underlying_type == "EQUITY":
                und_sym = item.get("underlying_symbol") or item.get("asset_symbol")
                if und_sym:
                    und_sym = und_sym.strip()
                    fut_by_underlying.setdefault(und_sym, []).append(item)

        logger.info(f"Identified {len(fut_by_underlying)} unique equity underlyings with active futures contracts.")

        # 2. Build Spot Equity mapping (NSE_EQ EQ type)
        self._spot_universe.clear()
        spot_by_sym: Dict[str, Dict[str, Any]] = {}
        for item in raw_instruments:
            if item.get("segment") == "NSE_EQ" and item.get("instrument_type") == "EQ":
                sym = item.get("trading_symbol")
                if sym and sym in fut_by_underlying:
                    spot_by_sym[sym] = item

        for sym, item in spot_by_sym.items():
            inst_key = item.get("instrument_key")
            if not inst_key:
                continue
            spot_inst = FNOInstrument(
                trading_symbol=sym,
                instrument_key=inst_key,
                underlying_symbol=sym,
                exchange="NSE",
                segment="NSE_EQ",
                instrument_type="EQ",
                is_fno=True,
                lot_size=item.get("lot_size") or 1,
                tick_size=item.get("tick_size"),
                name=item.get("name"),
            )
            self._spot_universe[sym] = spot_inst

        # 3. Build Nearest Futures mapping (sorted by expiry ascending)
        self._futures_universe.clear()
        for sym, contracts in fut_by_underlying.items():
            if sym not in self._spot_universe:
                continue
            # Sort contracts by expiry epoch timestamp ascending -> index 0 is nearest contract
            sorted_contracts = sorted(contracts, key=lambda x: x.get("expiry", 0))
            nearest = sorted_contracts[0]
            fut_inst = FNOInstrument(
                trading_symbol=nearest.get("trading_symbol", f"{sym} FUT"),
                instrument_key=nearest.get("instrument_key", ""),
                underlying_symbol=sym,
                exchange="NSE",
                segment="NSE_FO",
                instrument_type="FUT",
                is_fno=True,
                expiry=nearest.get("expiry"),
                expiry_date=nearest.get("expiry_date"),
                lot_size=nearest.get("lot_size"),
                tick_size=nearest.get("tick_size"),
                name=nearest.get("name"),
            )
            self._futures_universe[sym] = fut_inst

        # 4. Set active universe based on current mode
        self.set_mode(self._current_mode)
        logger.info(
            f"Successfully paired {len(self._universe)} F&O stocks. "
            f"Active Mode: {self._current_mode} ({len(self._universe)} instruments configured)."
        )
        return self.universe

    def set_mode(self, mode: str):
        """Switches active universe between 'FUTURES' and 'SPOT'."""
        self._current_mode = mode.upper()
        if self._current_mode == "SPOT":
            self._universe = dict(self._spot_universe)
        else:
            self._current_mode = "FUTURES"
            self._universe = dict(self._futures_universe)

        self._key_to_symbol.clear()
        for sym, inst in self._universe.items():
            self._key_to_symbol[inst.instrument_key] = sym

    def get_futures_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Returns nearest futures instrument for underlying symbol."""
        return self._futures_universe.get(symbol)

    def get_spot_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Returns cash spot equity instrument for underlying symbol."""
        return self._spot_universe.get(symbol)

    def get_instrument_keys(self) -> List[str]:
        """Returns list of active instrument keys for current mode."""
        return [inst.instrument_key for inst in self._universe.values()]

    def get_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Retrieves active instrument dataclass for a given symbol."""
        return self._universe.get(symbol)
