@echo off
REM ============================================================
REM  Dhan PAPER trading bot - double-click to start the day.
REM  Runs the evidence-backed winner set (6 tickers, 3 strategies)
REM  with Rs 1,00,000 virtual capital. No real money, no keys.
REM
REM  Keep this repo OUTSIDE C:\Windows\System32 (e.g. C:\tradingBot)
REM  so no Administrator rights are needed.
REM ============================================================

REM Move into this .bat file's own folder (works wherever you put it).
cd /d "%~dp0"

echo.
echo ==== Updating bot to latest code ====
git pull

echo.
echo ==== Starting PAPER trading (leave this window open all day) ====
echo     Stop anytime with Ctrl+C. Check status by double-clicking
echo     check_paper_status.bat in a second window.
echo.

python cli.py dhan-live --paper --capital 100000 -t AXISBANK.NS -t ASIANPAINT.NS -t BAJFINANCE.NS -t "M&M.NS" -t SUNPHARMA.NS -t KOTAKBANK.NS -s rsi_mean_revert -s bollinger_bands -s ma_crossover

echo.
echo ==== Bot stopped. Press any key to close this window. ====
pause >nul
