"""
NSE F&O, Nifty 250, and Nifty 500 Instrument Universe Manager.
Downloads Upstox instrument master, extracts active equity F&O symbols,
indexes options contracts (CE/PE), manages broad-market universes (Nifty 500, Nifty 250, F&O),
and provides automated At-The-Money (ATM) option strike resolution.
"""

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set
import time

from upstox.rest import UpstoxRestClient
from market.universe_loader import UniverseLoader

logger = logging.getLogger(__name__)


@dataclass
class FNOInstrument:
    """Structure for an active NSE Instrument (Spot Equity, Nearest Future, or Option)."""
    trading_symbol: str
    instrument_key: str
    underlying_symbol: str
    exchange: str = "NSE"
    segment: str = "NSE_FO"  # "NSE_FO" or "NSE_EQ"
    instrument_type: str = "FUT"  # "FUT", "EQ", "CE", or "PE"
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
    """Manages downloading, filtering, caching, and pairing NSE F&O, Nifty 250, and Nifty 500 instruments."""

    def __init__(self, rest_client: UpstoxRestClient):
        self.rest_client = rest_client
        self.universe_loader = UniverseLoader()
        self._universe: Dict[str, FNOInstrument] = {}
        self._spot_universe: Dict[str, FNOInstrument] = {}
        self._all_spot_equities: Dict[str, FNOInstrument] = {}
        self._futures_universe: Dict[str, FNOInstrument] = {}
        self._options_by_underlying: Dict[str, List[Dict[str, Any]]] = {}
        self._fno_symbols: Set[str] = set()
        self._key_to_symbol: Dict[str, str] = {}
        self._current_mode: str = "FUTURES"
        self._current_universe: str = "FNO"
        self._raw_instruments_cached: Optional[List[Dict[str, Any]]] = None

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

    @property
    def current_universe(self) -> str:
        return self._current_universe

    @property
    def fno_symbols(self) -> Set[str]:
        """Returns set of symbols that have active F&O contracts."""
        return self._fno_symbols

    def load_universe(
        self,
        universe_name: str = "FNO",
        mode: str = "FUTURES",
        force_refresh: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Loads and switches active universe ('FNO', 'NIFTY250', or 'NIFTY500')
        and execution mode ('FUTURES', 'OPTIONS', or 'SPOT').
        """
        self._current_universe = universe_name.upper() if universe_name else "FNO"
        self._current_mode = mode.upper() if mode else "FUTURES"

        if not self._raw_instruments_cached or force_refresh:
            raw_instruments = self.rest_client.download_nse_instruments(force_refresh=force_refresh)
            if not raw_instruments:
                logger.error("No instruments retrieved from Upstox master.")
                return {}
            self._raw_instruments_cached = raw_instruments
            self._index_master_instruments(raw_instruments)

        # Determine target symbol list based on selected universe
        if self._current_universe == "NIFTY500":
            target_symbols = self.universe_loader.get_nifty_500_symbols(force_refresh=force_refresh)
            if not target_symbols:
                logger.warning("Nifty 500 symbols empty, falling back to F&O universe.")
                target_symbols = list(self._fno_symbols)
        elif self._current_universe == "NIFTY250":
            target_symbols = self.universe_loader.get_nifty_250_symbols(force_refresh=force_refresh)
            if not target_symbols:
                logger.warning("Nifty 250 symbols empty, falling back to F&O universe.")
                target_symbols = list(self._fno_symbols)
        else:
            self._current_universe = "FNO"
            target_symbols = list(self._fno_symbols)

        # Build active universe mapping for target symbols
        self._universe.clear()
        for sym in target_symbols:
            is_fno = sym in self._fno_symbols
            spot_inst = self._all_spot_equities.get(sym) or self._spot_universe.get(sym)
            fut_inst = self._futures_universe.get(sym)

            if self._current_mode == "FUTURES":
                # If stock has futures contract, use nearest future; otherwise fall back to spot
                if fut_inst:
                    self._universe[sym] = fut_inst
                elif spot_inst:
                    self._universe[sym] = spot_inst
            elif self._current_mode == "OPTIONS":
                # In OPTIONS mode, underlying spot is monitored for setups/breakouts
                if spot_inst:
                    self._universe[sym] = spot_inst
                elif fut_inst:
                    self._universe[sym] = fut_inst
            else:
                # SPOT mode
                if spot_inst:
                    self._universe[sym] = spot_inst
                elif fut_inst:
                    self._universe[sym] = fut_inst

        self._key_to_symbol.clear()
        for sym, inst in self._universe.items():
            self._key_to_symbol[inst.instrument_key] = sym

        logger.info(
            f"Configured Universe '{self._current_universe}' ({len(self._universe)} stocks). "
            f"Active Mode: {self._current_mode}."
        )
        return self.universe

    def load_fno_universe(self, force_refresh: bool = False, mode: str = "FUTURES") -> Dict[str, Dict[str, Any]]:
        """Backwards-compatible wrapper for load_universe('FNO', mode)."""
        return self.load_universe(universe_name="FNO", mode=mode, force_refresh=force_refresh)

    def _index_master_instruments(self, raw_instruments: List[Dict[str, Any]]):
        """Indexes raw Upstox instruments into Spot, Futures, and Options directories."""
        fut_by_underlying: Dict[str, List[Dict[str, Any]]] = {}
        self._options_by_underlying.clear()
        self._all_spot_equities.clear()
        self._spot_universe.clear()
        self._futures_universe.clear()
        self._fno_symbols.clear()

        # 1. Collect all active equity futures and options
        for item in raw_instruments:
            segment = item.get("segment")
            underlying_type = item.get("underlying_type") or item.get("asset_type")
            inst_type = item.get("instrument_type")

            # Equity Futures
            if segment == "NSE_FO" and inst_type == "FUT" and underlying_type == "EQUITY":
                und_sym = (item.get("underlying_symbol") or item.get("asset_symbol") or "").strip()
                if und_sym:
                    fut_by_underlying.setdefault(und_sym, []).append(item)
                    self._fno_symbols.add(und_sym)

            # Equity Options (OPTSTK)
            elif segment == "NSE_FO" and inst_type in ("CE", "PE"):
                und_sym = (item.get("underlying_symbol") or item.get("asset_symbol") or "").strip()
                if und_sym:
                    self._options_by_underlying.setdefault(und_sym, []).append(item)
                    self._fno_symbols.add(und_sym)

            # Cash Equities
            elif segment == "NSE_EQ" and inst_type == "EQ":
                sym = item.get("trading_symbol")
                inst_key = item.get("instrument_key")
                if sym and inst_key:
                    clean_sym = sym.strip().upper()
                    eq_inst = FNOInstrument(
                        trading_symbol=clean_sym,
                        instrument_key=inst_key,
                        underlying_symbol=clean_sym,
                        exchange="NSE",
                        segment="NSE_EQ",
                        instrument_type="EQ",
                        is_fno=False,
                        lot_size=item.get("lot_size") or 1,
                        tick_size=item.get("tick_size"),
                        name=item.get("name"),
                    )
                    self._all_spot_equities[clean_sym] = eq_inst

        # 2. Build Spot Equity mapping for F&O universe
        for sym in self._fno_symbols:
            if sym in self._all_spot_equities:
                inst = self._all_spot_equities[sym]
                inst.is_fno = True
                self._spot_universe[sym] = inst

        # 3. Build Nearest Futures mapping (sorted by expiry ascending)
        for sym, contracts in fut_by_underlying.items():
            if sym not in self._all_spot_equities:
                continue
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

        logger.info(
            f"Indexed {len(self._all_spot_equities)} Cash Equities, "
            f"{len(self._futures_universe)} Futures underlyings, and "
            f"{len(self._options_by_underlying)} Option underlyings."
        )

    def set_mode(self, mode: str):
        """Switches active execution mode ('FUTURES', 'OPTIONS', or 'SPOT')."""
        self.load_universe(universe_name=self._current_universe, mode=mode)

    def get_atm_option(
        self,
        symbol: str,
        current_price: float,
        option_type: str = "CE",
        expiry_preference: str = "nearest",
    ) -> Optional[Dict[str, Any]]:
        """
        Finds the exact At-The-Money (ATM) option contract for a given stock.
        Returns strike price, trading symbol, lot size, expiry, and instrument key.
        """
        contracts = self._options_by_underlying.get(symbol.upper())
        if not contracts or current_price <= 0:
            return None

        opt_type = option_type.upper()
        typed_contracts = [c for c in contracts if c.get("instrument_type") == opt_type]
        if not typed_contracts:
            return None

        # Group by expiry timestamp
        valid_expiries = sorted(set(c.get("expiry") for c in typed_contracts if c.get("expiry")))
        if not valid_expiries:
            return None

        now_ms = int(time.time() * 1000)
        # Select active nearest expiry (must be >= current time or closest future)
        future_expiries = [exp for exp in valid_expiries if exp >= (now_ms - 86400000)]
        target_expiry = future_expiries[0] if future_expiries else valid_expiries[0]

        expiry_contracts = [c for c in typed_contracts if c.get("expiry") == target_expiry]
        if not expiry_contracts:
            expiry_contracts = typed_contracts

        # Sort contracts by absolute distance to current underlying price -> ATM strike is at index 0
        expiry_contracts.sort(key=lambda c: abs(float(c.get("strike_price", 0.0)) - current_price))
        atm = expiry_contracts[0]

        # Format human-friendly expiry date
        exp_val = atm.get("expiry")
        exp_str = atm.get("expiry_date")
        if not exp_str and exp_val:
            try:
                import datetime
                exp_dt = datetime.datetime.fromtimestamp(exp_val / 1000.0)
                exp_str = exp_dt.strftime("%d-%b-%Y")
            except Exception:
                exp_str = "--"

        strike = float(atm.get("strike_price", 0.0))
        return {
            "underlying_symbol": symbol.upper(),
            "trading_symbol": atm.get("trading_symbol", f"{symbol} {strike:g} {opt_type}"),
            "strike_price": strike,
            "option_type": opt_type,
            "lot_size": int(atm.get("lot_size", 0) or 0),
            "expiry_date": exp_str or "--",
            "expiry_timestamp": exp_val,
            "instrument_key": atm.get("instrument_key", ""),
            "is_atm": True,
        }

    def get_futures_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Returns nearest futures instrument for underlying symbol."""
        return self._futures_universe.get(symbol)

    def get_spot_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Returns cash spot equity instrument for underlying symbol."""
        return self._all_spot_equities.get(symbol) or self._spot_universe.get(symbol)

    def get_instrument_keys(self) -> List[str]:
        """Returns list of active instrument keys for current mode."""
        return [inst.instrument_key for inst in self._universe.values()]

    def get_instrument(self, symbol: str) -> Optional[FNOInstrument]:
        """Retrieves active instrument dataclass for a given symbol."""
        return self._universe.get(symbol)
