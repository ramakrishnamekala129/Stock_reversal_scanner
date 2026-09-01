@echo off
title Upstox 5-Minute F&O Intraday Reversal Scanner
cd /d "%~dp0"

echo ============================================================
echo   UPSTOX 5-MINUTE F^&O INTRADAY REVERSAL SCANNER
echo ============================================================
echo.
echo Starting scanner and launching FastAPI Web Dashboard...
echo Web Dashboard URL: http://127.0.0.1:8000
echo.

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

python main.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo [ERROR] Scanner exited with error code %ERRORLEVEL%
    echo ============================================================
    pause
)
