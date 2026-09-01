"""
Main Command-Line Entry Point for Upstox 5-Minute F&O Intraday Scanner.
Backend Only - No UI.
"""

import argparse
import logging
import sys
import time

import config
from market.session import MarketSessionManager
from scanner.scanner import FNOIntradayScanner
from upstox.auth import UpstoxAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner_main")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upstox Backend F&O 5-Minute Intraday Reversal Scanner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run startup initialization, evaluate today's existing 5M candles, print detected signals and summary, then exit.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Force re-download of NSE instrument master and previous-day OHLCV cache.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="Optional list of specific F&O symbols to scan (e.g. --symbols RELIANCE SBIN TCS).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging verbosity level.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Set logging level
    numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(numeric_level)

    auth = UpstoxAuth()
    scanner = FNOIntradayScanner(auth=auth)

    success = scanner.startup(force_refresh=args.refresh_cache, symbols=args.symbols)
    if not success:
        logger.error("Failed to start scanner.")
        sys.exit(1)

    if args.dry_run:
        logger.info("Running dry-run evaluation across all 5M historical candles of today's session...")
        # Replay all 5M candles from 09:15 to current time across all symbols
        for sym, candles in list(scanner.candle_engine._history.items()):
            if args.symbols and sym not in args.symbols:
                continue
            df_full = scanner.candle_engine.get_candle_history_df(sym)
            if len(candles) >= 2:
                for i in range(2, len(candles) + 1):
                    sub_candle = candles[i - 1]
                    sub_df = df_full.iloc[:i]
                    scanner._handle_candle_closed(sym, sub_candle, sub_df)

        logger.info("Dry-run evaluation complete.")
        if config.ENABLE_WEB_DASHBOARD:
            print("\n============================================================")
            print(f"  WEB DASHBOARD ACTIVE: http://{config.WEB_HOST}:{config.WEB_PORT}")
            print("  Press Ctrl+C in terminal to stop server.")
            print("============================================================\n")
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass

        scanner.stop()
        return

    # Live Mode
    scanner.run_live()


if __name__ == "__main__":
    main()
