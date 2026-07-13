@echo off
REM ICT Trading Bot - Development Start Script
REM Starts backend (with hot-reload) and dashboard (dev server)

cd /d "%~dp0"

echo ========================================
echo   ICT Trading Bot - Starting (Dev)
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
    echo You can copy from .env.example and edit it.
    pause
    exit /b 1
)

echo Checking if MetaTrader 5 is running...
tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I /N "terminal64.exe">NUL
if errorlevel 1 (
    echo.
    echo WARNING: MetaTrader 5 does not appear to be running!
    echo Please start MT5 and log in before the bot can trade.
    echo The bot will start in SIMULATION MODE.
    echo.
    timeout /t 3
)

echo.
echo Starting Dashboard in a new window...
start "ICT Dashboard" cmd /k pushd "%~dp0dashboard" ^&^& npm run dev

timeout /t 2 /nobreak >nul

call venv\Scripts\activate.bat

echo.
echo ========================================
echo   Backend API Starting...
echo ========================================
echo.
echo API will be available at: http://localhost:8000
echo Dashboard will be at:     http://localhost:3000
echo.
echo For production/VPS use: start_bot_production.bat
echo Press Ctrl+C to stop the server
echo.

python -m uvicorn trading_bot.api.main:app --reload --host 0.0.0.0 --port 8000
