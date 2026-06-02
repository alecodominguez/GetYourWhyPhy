@echo off
echo ===================================
echo === Checking Active Ports (8000) ==
echo ===================================
:: Finds if any process is bound to port 8000
netstat -ano | findstr :8000

echo.
echo ===================================
echo === Checking Python Processes =====
echo ===================================
:: Lists running python processes to see if server.py is alive
tasklist /FI "IMAGENAME eq python.exe"

echo.
echo ===================================
echo === Testing Local Connection ======
echo ===================================
:: Windows alternative to curl -I to check the local health endpoint
powershell -Command "Invoke-WebRequest -Uri http://127.0.0.1:8000/health -Method Head -ErrorAction SilentlyContinue | Select-Object StatusCode"

pause
