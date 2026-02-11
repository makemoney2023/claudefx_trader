# ICT Trading Bot - Windows Setup Script
# Run this script in PowerShell as Administrator

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ICT Trading Bot - Windows Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Warning: Not running as Administrator. Some operations may fail." -ForegroundColor Yellow
    Write-Host ""
}

# Check Python installation
Write-Host "[1/7] Checking Python installation..." -ForegroundColor Yellow
$pythonVersion = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python is not installed!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Red
    exit 1
}
Write-Host "Found: $pythonVersion" -ForegroundColor Green

# Check Node.js installation
Write-Host ""
Write-Host "[2/7] Checking Node.js installation..." -ForegroundColor Yellow
$nodeVersion = node --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Node.js is not installed!" -ForegroundColor Red
    Write-Host "Please install Node.js 18+ from https://nodejs.org/" -ForegroundColor Red
    exit 1
}
Write-Host "Found: Node.js $nodeVersion" -ForegroundColor Green

# Create virtual environment
Write-Host ""
Write-Host "[3/7] Creating Python virtual environment..." -ForegroundColor Yellow
if (Test-Path "venv") {
    Write-Host "Virtual environment already exists, skipping..." -ForegroundColor Gray
} else {
    python -m venv venv
    Write-Host "Virtual environment created" -ForegroundColor Green
}

# Activate virtual environment
Write-Host ""
Write-Host "[4/7] Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1
Write-Host "Virtual environment activated" -ForegroundColor Green

# Install Python dependencies
Write-Host ""
Write-Host "[5/7] Installing Python dependencies..." -ForegroundColor Yellow
pip install --upgrade pip
pip install -r requirements.txt
pip install MetaTrader5
Write-Host "Python dependencies installed" -ForegroundColor Green

# Install Node.js dependencies
Write-Host ""
Write-Host "[6/7] Installing dashboard dependencies..." -ForegroundColor Yellow
Set-Location dashboard
npm install
Set-Location ..
Write-Host "Dashboard dependencies installed" -ForegroundColor Green

# Create .env.local if it doesn't exist
Write-Host ""
Write-Host "[7/7] Checking configuration..." -ForegroundColor Yellow
if (-not (Test-Path ".env.local")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env.local"
        Write-Host "Created .env.local from .env.example" -ForegroundColor Green
        Write-Host ""
        Write-Host "IMPORTANT: Edit .env.local with your credentials!" -ForegroundColor Yellow
    }
} else {
    Write-Host ".env.local already exists" -ForegroundColor Green
}

# Create logs directory
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
    Write-Host "Created logs directory" -ForegroundColor Green
}

# Create data directory for backtesting
if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" | Out-Null
    Write-Host "Created data directory" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host ""
Write-Host "1. Configure your credentials in .env.local:" -ForegroundColor Yellow
Write-Host "   - MT5_LOGIN=your_account_number" -ForegroundColor Gray
Write-Host "   - MT5_PASSWORD=your_password" -ForegroundColor Gray
Write-Host "   - MT5_SERVER=your_broker_server" -ForegroundColor Gray
Write-Host "   - ANTHROPIC_API_KEY=your_claude_api_key" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Make sure MetaTrader 5 is:" -ForegroundColor Yellow
Write-Host "   - Installed and logged into your account" -ForegroundColor Gray
Write-Host "   - AutoTrading is ENABLED (green button in toolbar)" -ForegroundColor Gray
Write-Host "   - Tools > Options > Expert Advisors > Allow automated trading" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Start the bot with:" -ForegroundColor Yellow
Write-Host "   .\start_bot.bat" -ForegroundColor Cyan
Write-Host ""
Write-Host "Or start components individually:" -ForegroundColor Yellow
Write-Host "   Backend:   .\venv\Scripts\python -m uvicorn trading_bot.api.main:app --reload" -ForegroundColor Gray
Write-Host "   Dashboard: cd dashboard && npm run dev" -ForegroundColor Gray
Write-Host ""
