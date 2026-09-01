@echo off
title Upstox 5-Minute F&O Intraday Reversal Scanner
cd /d "%~dp0"

echo ============================================================
echo   UPSTOX 5-MINUTE F^&O INTRADAY REVERSAL SCANNER
echo ============================================================
echo Starting scanner and launching Native Tkinter Desktop Dashboard...
echo Mode: Native Desktop GUI (Tab 1: Reversal Signals | Tab 2: 210 Market Pivots)
echo.

python main.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo [ERROR] Scanner exited with error code %ERRORLEVEL%
    echo ============================================================
    pause
)
