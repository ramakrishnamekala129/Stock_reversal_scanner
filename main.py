"""
Main Command-Line Entry Point for Upstox 5-Minute F&O Intraday Scanner.
Backend Only - No UI.
"""

import argparse
import logging
import sys
import threading
import time

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
from market.session import MarketSessionManager
from scanner.scanner import FNOIntradayScanner
from upstox.auth import UpstoxAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Silence verbose HTTP connection logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run without desktop GUI (console terminal only).",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run FastAPI Web Dashboard instead of Tkinter Desktop GUI.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Set logging level
    numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(numeric_level)

    if args.web:
        config.ENABLE_WEB_DASHBOARD = True
        config.ENABLE_TKINTER_GUI = False
    elif args.no_gui:
        config.ENABLE_TKINTER_GUI = False
        config.ENABLE_WEB_DASHBOARD = False

    auth = UpstoxAuth()
    scanner = FNOIntradayScanner(auth=auth)

    success = scanner.startup(force_refresh=args.refresh_cache, symbols=args.symbols)
    if not success:
        logger.error("Failed to start scanner.")
        sys.exit(1)

    # Launch Desktop GUI if enabled
    if config.ENABLE_TKINTER_GUI:
        try:
            import tkinter as tk
            from gui.app import ScannerTkinterGUI

            logger.info("Launching Native Tkinter Desktop Dashboard...")
            root = tk.Tk()
            gui = ScannerTkinterGUI(root, scanner=scanner)

            if not args.dry_run:
                # Start live WebSocket feed in background
                threading.Thread(target=scanner.run_live, daemon=True).start()

            def on_closing():
                logger.info("Closing Tkinter GUI and stopping scanner...")
                try:
                    scanner.stop()
                except Exception:
                    pass
                root.destroy()

            root.protocol("WM_DELETE_WINDOW", on_closing)
            root.mainloop()
            return
        except Exception as e:
            logger.error(f"Failed to start Tkinter GUI: {e}")

    # Fallback / Web / Headless Mode
    if args.dry_run:
        logger.info("Dry-run candle scan completed successfully.")
        if config.ENABLE_WEB_DASHBOARD:
            print("\n============================================================")
            print(f"  WEB DASHBOARD ACTIVE: http://{config.WEB_HOST}:{config.WEB_PORT}")
            print("  Review all detected signals & pivots in your browser.")
            print("  Press Ctrl+C in terminal to stop server.")
            print("============================================================\n")
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass

        scanner.stop()
        return

    # Live Mode (Headless / Web)
    scanner.run_live()


if __name__ == "__main__":
    main()
