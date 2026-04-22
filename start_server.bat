@echo off
cd C:\Users\ual-laptop\GetYourWhyPhy
call venv\Scripts\activate
start "WhyPhy Server" uvicorn server:app --reload --port 8000
start "Ngrok Tunnel" ngrok http --url=https://opacity-cadillac-emporium.ngrok-free.dev 8000
