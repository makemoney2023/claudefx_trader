@echo off
REM ICT Trading Bot - Windows Setup Script (Batch version)
REM Run this script from the project directory

echo ========================================
echo   ICT Trading Bot - Windows Setup
echo ========================================
echo.

REM Check Python installation
echo [1/7] Checking Python installation...
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
echo [2/7] Checking Node.js installation...
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
echo [3/7] Creating Python virtual environment...
if exist venv (
    echo Virtual environment already exists, skipping...
) else (
    python -m venv venv
    echo Virtual environment created
)
echo.

REM Activate virtual environment
echo [4/7] Activating virtual environment...
call venv\Scripts\activate.bat
echo Virtual environment activated
echo.

REM Install Python dependencies
echo [5/7] Installing Python dependencies...
pip install --upgrade pip
pip install -r requirements.txt
pip install MetaTrader5
echo Python dependencies installed
echo.

REM Install Node.js dependencies
echo [6/7] Installing dashboard dependencies...
cd dashboard
call npm install
cd ..
echo Dashboard dependencies installed
echo.

REM Create .env.local if it doesn't exist
echo [7/7] Checking configuration...
if not exist .env.local (
    if exist .env.example (
        copy .env.example .env.local
        echo Created .env.local from .env.example
        echo.
        echo IMPORTANT: Edit .env.local with your credentials!
    )
) else (
    echo .env.local already exists
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
echo 1. Configure your credentials in .env.local:
echo    - MT5_LOGIN=your_account_number
echo    - MT5_PASSWORD=your_password
echo    - MT5_SERVER=your_broker_server
echo    - ANTHROPIC_API_KEY=your_claude_api_key
echo.
echo 2. Make sure MetaTrader 5 is:
echo    - Installed and logged into your account
echo    - AutoTrading is ENABLED (green button in toolbar)
echo    - Tools ^> Options ^> Expert Advisors ^> Allow automated trading
echo.
echo 3. Start the bot with:
echo    start_bot.bat
echo.
pause
