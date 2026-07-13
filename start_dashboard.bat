@echo off
REM ICT Trading Bot - Production Dashboard
REM Serves the built Next.js dashboard (run npm run build first)

cd /d "%~dp0dashboard"

echo ========================================
echo   ICT Trading Bot - Dashboard
echo ========================================
echo.

if not exist node_modules (
    echo ERROR: Dashboard dependencies not installed!
    echo Please run setup_windows.bat first.
    pause
    exit /b 1
)

if not exist .next (
    echo ERROR: Dashboard has not been built!
    echo Run: cd dashboard ^&^& npm run build
    echo Or use start_bot_production.bat to build automatically.
    pause
    exit /b 1
)

echo Dashboard starting at http://0.0.0.0:3000
echo Press Ctrl+C to stop
echo.

npm run start
