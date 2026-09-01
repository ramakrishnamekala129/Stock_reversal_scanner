@echo off
title Upstox F&O Scanner (Dry Run / Today's History)
cd /d "%~dp0"

echo ============================================================
echo   UPSTOX F^&O SCANNER - DRY RUN / TODAY'S HISTORY
echo ============================================================
echo.
echo Launching full-day historical analysis & Web Dashboard...
echo Web Dashboard URL: http://127.0.0.1:8000
echo.

python main.py --dry-run %*

echo.
echo Press any key to close...
pause >nul
