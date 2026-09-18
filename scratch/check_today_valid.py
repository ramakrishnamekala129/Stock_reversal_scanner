from scanner.scanner import FNOIntradayScanner
import logging

logging.basicConfig(level=logging.WARNING)
scanner = FNOIntradayScanner()
scanner._universe = scanner.instrument_mgr.load_universe('NIFTY500', mode='OPTIONS')
scanner._load_daily_candles_cache()
from chartink_cash_v13_backtest import prescreen_candidate_symbols, resolve_backtest_window
from datetime import date
start_date, end_date = resolve_backtest_window('current_day')
screened, cand_map = prescreen_candidate_symbols(scanner._universe, scanner._daily_dfs_cache, start_date=start_date, end_date=end_date)
print(f"start_date: {start_date}, end_date: {end_date}")
print(f"Screened count: {len(screened)}")
print(f"Cand map length: {len(cand_map)}")
res = scanner.run_cash_v13_backtest(mode='current_day')
print("=== CASH V1.3 TODAY (2026-09-18) ===")
print("Total Universe Screened:", res.get("symbols_screened_total"))
print("Symbols Skipped by Daily Pre-filter:", res.get("symbols_skipped_prefilter"))
print("Candidate Stocks Valid for 5m Evaluation (symbols_tested):", res.get("symbols_tested"))
print("Candidate 5m Bars Evaluated:", res.get("bars_evaluated"))
print("Total Trades Triggered Today:", res.get("total_trades"))
if res.get("trades"):
    print("Trades today:")
    for t in res.get("trades"):
        print(f"  {t.get('symbol')} | {t.get('strategy')} | Entry: {t.get('entry_time')} @ {t.get('entry_price')} | Exit: {t.get('exit_time')} @ {t.get('exit_price')} | PnL: {t.get('pnl_pct'):+.2f}% ({t.get('exit_reason')})")
else:
    print("No strategy entries triggered yet across today's 5m candles.")
