@echo off
REM ICT Trading Bot - Production Start Script
REM Builds the dashboard if needed, then starts backend + dashboard without hot-reload

cd /d "%~dp0"

echo ========================================
echo   ICT Trading Bot - Production Start
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

if not exist dashboard\node_modules (
    echo ERROR: Dashboard dependencies not installed!
    echo Please run setup_windows.bat first.
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

if not exist dashboard\.next (
    echo Building dashboard for production...
    cd dashboard
    call npm run build
    if errorlevel 1 (
        echo ERROR: Dashboard build failed!
        pause
        exit /b 1
    )
    cd ..
    echo Dashboard build complete.
    echo.
)

echo Starting Dashboard in a new window...
start "ICT Dashboard" cmd /k call "%~dp0start_dashboard.bat"

timeout /t 2 /nobreak >nul

echo Starting Backend API...
echo.
echo API:       http://localhost:8000
echo Dashboard: http://localhost:3000
echo.
echo Press Ctrl+C to stop the backend (use stop_bot.bat to stop both)
echo.

call "%~dp0start_api.bat"
