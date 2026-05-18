@echo off
REM ============================================================================
REM stop_server.bat  -  Stop the detached Chatform server.
REM
REM Finds whatever process is LISTENING on the Streamlit port (default 8501)
REM and kills it. Pass a different port as the first argument if needed.
REM
REM Usage:
REM   scripts\stop_server.bat
REM   scripts\stop_server.bat 9000
REM ============================================================================
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=8501

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo Stopping PID %%a (port %PORT%)...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="0" (
    echo No Chatform server found listening on port %PORT%.
    exit /b 1
)

echo Server stopped.
endlocal
