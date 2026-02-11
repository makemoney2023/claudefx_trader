@echo off
REM ICT Trading Bot - Start Script
REM This script starts both the backend API and the dashboard

echo ========================================
echo   ICT Trading Bot - Starting...
echo ========================================
echo.

REM Check if virtual environment exists
if not exist venv (
    echo ERROR: Virtual environment not found!
    echo Please run setup_windows.bat first.
    pause
    exit /b 1
)

REM Check if .env.local exists
if not exist .env.local (
    echo ERROR: .env.local not found!
    echo Please create .env.local with your credentials.
    echo You can copy from .env.example and edit it.
    pause
    exit /b 1
)

REM Check if MT5 is running
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
echo Starting Backend API server...
echo (This window will run the FastAPI backend)
echo.
echo Starting Dashboard in a new window...
echo.

REM Start dashboard in new window
start "ICT Dashboard" cmd /k "cd dashboard && npm run dev"

REM Small delay to let dashboard start
timeout /t 2 /nobreak >nul

REM Activate venv and start backend (this window)
call venv\Scripts\activate.bat

echo.
echo ========================================
echo   Backend API Starting...
echo ========================================
echo.
echo API will be available at: http://localhost:8000
echo Dashboard will be at:     http://localhost:3000
echo.
echo Press Ctrl+C to stop the server
echo.

python -m uvicorn trading_bot.api.main:app --reload --host 0.0.0.0 --port 8000
