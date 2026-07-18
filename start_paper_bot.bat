@echo off
REM ============================================================
REM  Dhan PAPER trading bot - double-click to start the day.
REM  Runs the evidence-backed winner set (6 tickers, 3 strategies)
REM  with Rs 1,00,000 virtual capital. No real money, no keys.
REM ============================================================

REM The repo currently lives under C:\WINDOWS\system32, which needs
REM Administrator rights to git-pull. Auto-elevate if not already admin.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator access...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

REM Move into this .bat file's own folder (works wherever you put it).
cd /d "%~dp0"

echo.
echo ==== Updating bot to latest code ====
git pull

echo.
echo ==== Starting PAPER trading (leave this window open all day) ====
echo     Stop anytime with Ctrl+C. Check status in a second window with:
echo     python cli.py paper-status
echo.

python cli.py dhan-live --paper --capital 100000 -t AXISBANK.NS -t ASIANPAINT.NS -t BAJFINANCE.NS -t "M&M.NS" -t SUNPHARMA.NS -t KOTAKBANK.NS -s rsi_mean_revert -s bollinger_bands -s ma_crossover

echo.
echo ==== Bot stopped. Press any key to close this window. ====
pause >nul
