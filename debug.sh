#!/bin/bash
echo "=== Checking Active Ports ==="
# Lists what is actually listening on port 8000
lsof -i :8000 || netstat -tuln | grep 8000

echo "=== Checking Application Process ==="
# Adjust 'python' or 'node' based on your backend language
ps aux | grep -E 'python|server.py' | grep -v grep

echo "=== Testing Local Connection ==="
curl -s -I http://127.0.0.1:8000/health | head -n 1
