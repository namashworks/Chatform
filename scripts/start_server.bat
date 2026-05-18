@echo off
REM ============================================================================
REM start_server.bat  -  Launch the Form Chatbot so it survives this terminal.
REM
REM Streamlit IS the web server, so when you press Ctrl+C or close the cmd
REM window that runs `streamlit run`, the chatbot dies with it. This script
REM spawns Streamlit in a separate minimized window that has its own process
REM group, so closing your original terminal won't take it down.
REM
REM Usage:
REM   scripts\start_server.bat        (uses default port 8501)
REM   scripts\start_server.bat 9000   (custom port)
REM
REM To stop it:  scripts\stop_server.bat
REM Logs:        logs\streamlit.log
REM ============================================================================
setlocal
cd /d "%~dp0\.."

set PORT=%1
if "%PORT%"=="" set PORT=8501

if not exist logs mkdir logs
if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run: python -m venv .venv ^&^& pip install -r requirements.txt
    exit /b 1
)

REM /MIN starts the cmd minimized; cmd /c keeps it alive only as long as
REM streamlit is running. Output goes to logs\streamlit.log.
start "Form Chatbot Server (port %PORT%)" /MIN cmd /c ^
    ".venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.port %PORT% --browser.gatherUsageStats false >> logs\streamlit.log 2>&1"

echo.
echo Form Chatbot server starting on http://localhost:%PORT%
echo A minimized window titled "Form Chatbot Server (port %PORT%)" now runs the server.
echo Closing THIS terminal will NOT kill it.
echo.
echo Stop with:  scripts\stop_server.bat
echo Logs:       logs\streamlit.log
endlocal
