@echo off
echo =================================== >> debug_log.txt
echo %date% %time% >> debug_log.txt
echo =================================== >> debug_log.txt

echo === Checking Active Ports (8000) === >> debug_log.txt
netstat -ano | findstr :8000 >> debug_log.txt

echo === Checking Python Processes === >> debug_log.txt
tasklist /FI "IMAGENAME eq python.exe" >> debug_log.txt

echo === Testing Local Connection === >> debug_log.txt
powershell -Command "Invoke-WebRequest -Uri http://127.0.0.1:8000/health -Method Head -ErrorAction SilentlyContinue | Select-Object StatusCode" >> debug_log.txt

echo ----------------------------------- >> debug_log.txt
