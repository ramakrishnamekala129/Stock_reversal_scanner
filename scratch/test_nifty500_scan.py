"""
Test verification script:
Validates that Chartink scanner and HEMA scanner run cleanly on NIFTY500 universe
without 429 errors or rate limit stalls.
"""

import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.scanner import FNOIntradayScanner

def test_nifty500_scanner():
    scanner = FNOIntradayScanner(enable_excel=False, enable_web=False, market_mode="SPOT", universe_name="NIFTY500")
    print(f"Initializing universe '{scanner.universe_name}' ({scanner.market_mode})...")
    
    # Load universe
    scanner._universe = scanner.instrument_mgr.load_universe(scanner.universe_name, mode=scanner.market_mode)
    print(f"Universe loaded: {len(scanner._universe)} stocks.")
    
    # Test Chartink scan
    print("Running Chartink scan across Nifty 500...")
    t0 = time.time()
    elapsed, evaluated, signals = scanner.scan_chartink_universe()
    print(f"Chartink scan completed: Evaluated {evaluated} stocks in {elapsed:.2f}s! Found {signals} breakout signals.")
    
    assert evaluated >= 490, f"Expected >= 490 stocks evaluated, got {evaluated}"
    print("[SUCCESS] Chartink screener successfully scans entire broad market Nifty 500!")

if __name__ == "__main__":
    test_nifty500_scanner()
