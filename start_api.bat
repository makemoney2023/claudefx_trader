@echo off
REM ICT Trading Bot - Production Backend API
REM Starts the FastAPI backend without hot-reload (for VPS / 24-7 use)

cd /d "%~dp0"

echo ========================================
echo   ICT Trading Bot - Backend API
echo ========================================
echo.

if not exist venv (
    echo ERROR: Virtual environment not found!
    echo Please run setup_windows.bat first.
    pause
    exit /b 1
)

if not exist .env.local (
    echo ERROR: .env.local not found!
    echo Please create .env.local with your credentials.
    pause
    exit /b 1
)

echo Checking if MetaTrader 5 is running...
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I /N "terminal64.exe">NUL
if errorlevel 1 (
    echo.
    echo WARNING: MetaTrader 5 does not appear to be running!
    echo The bot will start in SIMULATION MODE until MT5 is available.
    echo.
    timeout /t 3
)

call venv\Scripts\activate.bat

echo.
echo API starting at http://0.0.0.0:8000
echo Press Ctrl+C to stop
echo.

python -m uvicorn trading_bot.api.main:app --host 0.0.0.0 --port 8000
