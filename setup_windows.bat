@echo off
REM ICT Trading Bot - Windows Setup Script (Batch version)
REM Run from any directory — script paths are resolved automatically

cd /d "%~dp0"

echo ========================================
echo   ICT Trading Bot - Windows Setup
echo ========================================
echo.

REM Check Python installation
echo [1/8] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed!
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check 'Add Python to PATH' during installation
    pause
    exit /b 1
)
python --version
echo Python found!
echo.

REM Check Node.js installation
echo [2/8] Checking Node.js installation...
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed!
    echo Please install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
node --version
echo Node.js found!
echo.

REM Create virtual environment
echo [3/8] Creating Python virtual environment...
if exist venv (
    echo Virtual environment already exists, skipping...
) else (
    python -m venv venv
    echo Virtual environment created
)
echo.

REM Activate virtual environment
echo [4/8] Activating virtual environment...
call venv\Scripts\activate.bat
echo Virtual environment activated
echo.

REM Install Python dependencies
echo [5/8] Installing Python dependencies...
pip install --upgrade pip
pip install -r requirements.txt
pip install MetaTrader5
echo Python dependencies installed
echo.

REM Install Node.js dependencies
echo [6/8] Installing dashboard dependencies...
cd dashboard
call npm install
cd ..
echo Dashboard dependencies installed
echo.

REM Create root .env.local if it doesn't exist
echo [7/8] Checking backend configuration...
if not exist .env.local (
    if exist .env.example (
        copy .env.example .env.local >nul
        echo Created .env.local from .env.example
    )
) else (
    echo .env.local already exists
)

REM Create dashboard .env.local if it doesn't exist
echo [8/8] Checking dashboard configuration...
if not exist dashboard\.env.local (
    if exist dashboard\.env.example (
        copy dashboard\.env.example dashboard\.env.local >nul
        echo Created dashboard\.env.local from dashboard\.env.example
    )
) else (
    echo dashboard\.env.local already exists
)

REM Create directories
if not exist logs mkdir logs
if not exist data mkdir data

echo.
echo ========================================
echo   Setup Complete!
echo ========================================
echo.
echo Next steps:
echo.
echo 1. Configure credentials in .env.local:
echo    - MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
echo    - ANTHROPIC_API_KEY
echo    - BOT_API_KEY (generate a long random secret)
echo.
echo 2. Configure dashboard\.env.local:
echo    - Set NEXT_PUBLIC_API_URL to your VPS IP if accessing remotely
echo    - Set NEXT_PUBLIC_BOT_API_KEY to match BOT_API_KEY
echo.
echo 3. Make sure MetaTrader 5 is:
echo    - Installed and logged into your account
echo    - AutoTrading is ENABLED (green button in toolbar)
echo    - Tools ^> Options ^> Expert Advisors ^> Allow automated trading
echo.
echo 4. Test MT5:     venv\Scripts\activate ^&^& python test_mt5_connection.py
echo    Test Telegram: python test_telegram_connection.py
echo.
echo 5. Start the bot:
echo    Dev:        start_bot.bat
echo    Production: start_bot_production.bat
echo.
pause
