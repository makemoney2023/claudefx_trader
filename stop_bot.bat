@echo off
REM ICT Trading Bot - Stop Script
REM Stops backend and dashboard processes on ports 8000 and 3000

cd /d "%~dp0"

echo ========================================
echo   ICT Trading Bot - Stopping...
echo ========================================
echo.

echo Stopping Backend API...
for /f "tokens=5" %%a in ('netstat -ano ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Backend stopped.

echo Stopping Dashboard...
for /f "tokens=5" %%a in ('netstat -ano ^| find ":3000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Dashboard stopped.

echo.
echo All services stopped.
echo.
pause
