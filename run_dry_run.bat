@echo off
title Upstox F&O Scanner (Dry Run / Today's History)
cd /d "%~dp0"

echo ============================================================
echo   UPSTOX F^&O SCANNER - DRY RUN / TODAY'S HISTORY
echo ============================================================
echo.

python main.py --dry-run %*

echo.
echo Press any key to close...
pause >nul
