import logging
from datetime import date
import pandas as pd
from scanner.scanner import FNOIntradayScanner
from chartink_cash_v13_backtest import prescreen_candidate_symbols, resolve_backtest_window

logging.basicConfig(level=logging.WARNING)

scanner = FNOIntradayScanner(universe_name="CASH", market_mode="SPOT")
scanner._universe = scanner.instrument_mgr.load_universe("CASH", mode="OPTIONS")
scanner._load_daily_candles_cache()

start_date, end_date = resolve_backtest_window("current_day")
print(f"Active Universe Total: {len(scanner._universe)} stocks")
print(f"Cached Daily DFS Total: {len(scanner._daily_dfs_cache)} stocks")

screened, cand_map = prescreen_candidate_symbols(
    scanner._universe,
    scanner._daily_dfs_cache,
    start_date=start_date,
    end_date=end_date,
)

print(f"=== FULL CASH SEGMENT CANDIDATE SCREEN (2,653 STOCKS) ===")
print(f"Date: {start_date}")
print(f"Total Cash Stocks Screened: {len(scanner._universe)}")
print(f"Candidate Stocks Passing Daily Pre-Filter: {len(screened)}")
print(f"Symbols Filtered Out / Skipped: {len(scanner._universe) - len(screened)}")

res = scanner.run_cash_v13_backtest(mode="current_day")
print("\n=== CASH V1.3 5M INTRADAY TRIGGERS TODAY ===")
print(f"Total Candidate Stocks Tested on 5M: {res.get('symbols_tested')}")
print(f"Total 5M Bars Evaluated: {res.get('bars_evaluated')}")
print(f"Total Trades Triggered Today: {res.get('total_trades')}")

trades = res.get("trades", [])
if trades:
    print(f"\nTriggered Stocks ({len(trades)}):")
    for idx, t in enumerate(trades, 1):
        print(f"  {idx:2d}. {t.get('symbol'):<12} | Entry: {t.get('entry_time')} @ {t.get('entry_price'):<8.2f} | Exit: {t.get('exit_time')} @ {t.get('exit_price'):<8.2f} | PnL: {t.get('pnl_pct'):>+6.2f}% ({t.get('exit_reason')})")
else:
    print("No 5M entries triggered yet.")
