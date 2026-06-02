@echo off
cd C:\Users\ual-laptop\GetYourWhyPhy

:loop
echo [REMOTE CONTROL] Checking GitHub for new changes...
git pull origin main

echo [REMOTE CONTROL] Running diagnostics...
call debug.bat

echo [REMOTE CONTROL] Activating Virtual Environment...
call venv\Scripts\activate

echo [REMOTE CONTROL] Starting Uvicorn Server...
:: Running without 'start' means if uvicorn crashes, the script moves to the next line
uvicorn server:app --reload --port 8000

echo [REMOTE CONTROL] Server stopped or crashed. Restarting loop in 10 seconds...
timeout /t 10
goto loop
