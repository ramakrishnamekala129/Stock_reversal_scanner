"""
Automated unit tests for Nifty 500, Nifty 250 universes and ATM Option strike resolution.
"""

import unittest
from upstox.rest import UpstoxRestClient
from market.universe_loader import UniverseLoader
from market.instruments import InstrumentManager
from indicators.chartink_screener import ChartinkSignal
from indicators.hema_t3 import HemaT3Signal


class TestUniverseAndOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = UniverseLoader()
        cls.rest = UpstoxRestClient()
        cls.mgr = InstrumentManager(cls.rest)

    def test_01_universe_loader(self):
        n500 = self.loader.get_nifty_500_symbols()
        n250 = self.loader.get_nifty_250_symbols()
        self.assertGreaterEqual(len(n500), 400, "Nifty 500 should have >= 400 constituents")
        self.assertGreaterEqual(len(n250), 200, "Nifty 250 should have >= 200 constituents")

    def test_02_instrument_manager_universes(self):
        fno_u = self.mgr.load_universe("FNO", "FUTURES")
        self.assertGreaterEqual(len(fno_u), 200, "FNO universe should have >= 200 stocks")

        n250_u = self.mgr.load_universe("NIFTY250", "OPTIONS")
        self.assertGreaterEqual(len(n250_u), 200, "Nifty 250 should have >= 200 stocks")

        n500_u = self.mgr.load_universe("NIFTY500", "SPOT")
        self.assertGreaterEqual(len(n500_u), 400, "Nifty 500 should have >= 400 stocks")

    def test_03_atm_option_resolution(self):
        opt_ce = self.mgr.get_atm_option("ADANIENT", 3139.0, "CE")
        self.assertIsNotNone(opt_ce)
        self.assertEqual(opt_ce["option_type"], "CE")
        self.assertGreater(opt_ce["lot_size"], 0)
        self.assertEqual(opt_ce["strike_price"], 3150.0)

        opt_pe = self.mgr.get_atm_option("PAYTM", 1750.6, "PE")
        self.assertIsNotNone(opt_pe)
        self.assertEqual(opt_pe["option_type"], "PE")
        self.assertGreater(opt_pe["lot_size"], 0)
        self.assertEqual(opt_pe["strike_price"], 1760.0)

        # Non-FNO stock test
        opt_non_fno = self.mgr.get_atm_option("ACMESOLAR", 250.0, "CE")
        self.assertIsNone(opt_non_fno)

    def test_04_signal_option_fields(self):
        opt_ce = self.mgr.get_atm_option("ADANIENT", 3139.0, "CE")
        csig = ChartinkSignal(
            symbol="ADANIENT",
            timestamp="09:40:00",
            price=3139.0,
            matched_strategies=["Sub 3"],
            primary_strategy="Sub 3",
            median_pivot_diff_pct=1.03,
            turnover_cr=590.9,
            rsi_14=60.1,
            ma_crossed=[11, 12, 13],
            vol_surge_ratio=1.1,
            option_strike=f"{opt_ce['strike_price']:g} CE",
            option_symbol=opt_ce["trading_symbol"],
            option_lot_size=opt_ce["lot_size"],
            option_expiry=opt_ce["expiry_date"],
        )
        cdict = csig.to_dict()
        self.assertEqual(cdict["option_strike"], "3150 CE")
        self.assertEqual(cdict["option_lot_size"], 309)
        self.assertIn("ADANIENT", cdict["option_symbol"])


if __name__ == "__main__":
    unittest.main()
