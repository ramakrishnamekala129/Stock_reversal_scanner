@echo off
title Upstox F&O Scanner (Dry Run / Today's History)
cd /d "%~dp0"

echo ============================================================
echo   UPSTOX F^&O SCANNER - DRY RUN / TODAY'S HISTORY
echo ============================================================
echo.
echo Launching full-day historical analysis ^& Native Tkinter Desktop Dashboard...
echo Mode: Native Desktop GUI (Tab 1: Reversal Signals | Tab 2: 210 Market Pivots)
echo.

python main.py --dry-run %*

echo.
echo Press any key to close...
pause >nul
