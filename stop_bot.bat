@echo off
REM ICT Trading Bot - Stop Script
REM This script stops the backend and dashboard processes

echo ========================================
echo   ICT Trading Bot - Stopping...
echo ========================================
echo.

REM Kill Python/uvicorn processes on port 8000
echo Stopping Backend API...
for /f "tokens=5" %%a in ('netstat -ano ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Backend stopped.

REM Kill Node processes on port 3000
echo Stopping Dashboard...
for /f "tokens=5" %%a in ('netstat -ano ^| find ":3000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Dashboard stopped.

echo.
echo All services stopped.
echo.
pause
