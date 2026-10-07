@echo off
REM ============================================================
REM  Show the PAPER trading portfolio: cash, positions, P&L,
REM  and the recent trade log. Safe to double-click anytime.
REM ============================================================
cd /d "%~dp0"
python cli.py paper-status
echo.
pause >nul
