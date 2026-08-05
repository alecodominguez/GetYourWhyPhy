# GetYourWhyPhy

![Python](https://img.shields.io/badge/Python-3.x-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688) ![License](https://img.shields.io/badge/license-MIT-green)

**[Try the live demo →](https://opacity-cadillac-emporium.ngrok-free.dev/)** · **[Watch the demo video →](https://youtu.be/VJthtA6CzGs)** · **[Read the research paper →](./paper/GetYourWhyPhy_Paper.pdf)**

GetYourWhyPhy is a distributed Wi-Fi performance mapping tool built for the University of Arizona campus. It crowdsources network metrics — signal strength, download/upload speed, latency, and packet loss — to identify the best (and worst) study spots across campus buildings.

The name is a play on the "PHY" (physical) layer of the OSI network stack — the layer that determines whether your Wi-Fi is fast and stable or slow and dropping. WhyPhy measures that layer directly, so instead of guessing which building has good Wi-Fi, you can see it.

## Table of Contents

- [How it works](#how-it-works)
- [My contributions](#my-contributions)
- [Quick start (as a user)](#quick-start-as-a-user)
- [Developer setup (server-side)](#developer-setup-server-side)
- [Data & analysis](#data--analysis)
- [Roadmap](#roadmap)

## How it works

Three components work together to map campus connectivity:

1. **The Client (`WhyPhy.py`):** a Python script users run locally to perform network diagnostics and report results.
2. **The Backend (`server.py`):** a FastAPI server that receives results and stores them in a SQLite database.
3. **The Tunnel (ngrok):** a secure bridge that lets campus users reach the local server through university firewalls.

## My contributions

This was a 3-person team project. My scope:

- Designed and authored the server-side architecture and full repository structure.
- Built `export_to_server` in `WhyPhy.py`, packaging five raw network metrics (download, upload, latency, jitter, packet loss) captured across Windows/macOS/Linux into a standardized payload.
- Built `server.py` (FastAPI + SQLite + Pydantic) to handle concurrent client requests.
- Designed and integrated the fuzzy-matching engine in `locations.py` that standardizes messy, user-typed building names into verified campus locations.
- Owned end-to-end deployment and hosting (PyInstaller packaging, GitHub + ngrok).
- Piloted an initial ESP8266 IoT hardware approach, then made the call to pivot to a software-only architecture after hitting WPA2-Enterprise authentication limits — cut infrastructure cost to zero without losing functionality.

Teammates Jaden Beil and RJ Edwards built the client-side Terminal User Interface (TUI) and the 0–100 network quality normalization algorithm that these components integrate with.

## Quick start (as a user)

You get your building's Wi-Fi rating (plus tips) in exchange for sharing your current Wi-Fi speed data.

```bash
pip install requests speedtest-cli psutil
python WhyPhy.py
```

Follow the prompts to enter your current building or campus location.

## Developer setup (server-side)

Needed if you want to run the central server yourself in real time:

```bash
# 1. Environment
python3 -m venv venv
source venv/bin/activate

# 2. Install requirements
pip install -r requirements.txt

# 3. Start FastAPI
uvicorn server:app --host 0.0.0.0 --port 8000
```

If running on a restricted network (e.g., campus Wi-Fi), use ngrok to expose the server on a public domain.

## Data & analysis

Logs are stored in a SQLite database (`campus_wifi.db`). We use this data to:

- Generate a leaderboard of the fastest campus buildings.
- Identify consistent dead zones in older campus infrastructure.

## Roadmap

- Passive background testing and automated building mapping using BSSID data.
- A machine learning model to predict network congestion by day/time.
- Migrate the server to a Raspberry Pi for always-on hosting.

---

*Maintained by Aleco Dominguez, Jaden Beil, and RJ Edwards | University of Arizona*
