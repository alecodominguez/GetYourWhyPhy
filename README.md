# WhyPhy
[Click here to check it out](https://whyphy.app/)

**WhyPhy** (get it — the PHY layer, decoded for students) is a distributed WiFi performance mapping tool built for the University of Arizona campus. It crowdsources network metrics—such as Signal Strength, Download/Upload speeds, Latency, and Packet Loss—to identify the best (and worst) study spots across campus buildings.

The system consists of three main components working together to map campus connectivity:
1.  **The Client (`WhyPhy.py`):** A Python script run by users to perform localized network diagnostics.
2.  **The Backend (`server.py`):** A FastAPI server that receives results and stores them in a database.
3.  **The Server:** Hosted at `whyphy.app` behind a reverse proxy with automatic HTTPS (see `deploy/README.md`) — no more ngrok tunnel or dependence on a laptop staying awake.

## Research Paper

Our full paper, detailing the distributed software architecture, network normalization algorithm, and evaluation metrics, was prepared for the *University of Arizona Research Showcase (May 2026)*.

* **[Read the Full PDF Paper](./paper/GetYourWhyPhy_Paper.pdf)**
* **[View LaTeX Source Files](./paper/)**

## Setup for Contributors
The users gain knowledge on campus WiFi in exchange for their current WiFi information and Campus building location. Follow these steps:

### 1. Install Dependencies
You will need Python 3 and a few libraries. Run the following command in your terminal:
```bash
pip install requests speedtest-cli psutil
```

### 2. Run the Collection Script
Execute the script and enter your current building or campus location when prompted:
```bash
python WhyPhy.py
```
*You get to know your WiFi rating plus tips in exchange for your WiFi speed data*

### 3. (Optional) Passive background monitoring
For continuous connectivity-drop detection instead of one-off manual runs:
```bash
python passive_monitor.py run --building "Main Library"   # try it in the foreground first
python passive_monitor.py install                         # register as a login-time service
python passive_monitor.py status                          # check it's running
python passive_monitor.py uninstall                       # remove it
```
Opt-in only, visibly running, network-liveness only (no traffic inspection) - see the top of `passive_monitor.py` for the full transparency notes. For a menu-bar/tray status indicator: `pip install -r requirements-monitor.txt` first. If the building can't be auto-detected from the Wi-Fi access point, the installed service has nobody to ask, so confirm it works with `run` first.

### 4. (Optional) No install at all - WhyPhy Web Lite
For a 30-second throughput/latency check with nothing to download, visit [whyphy.app/lite](https://whyphy.app/lite). It covers less ground than the full tool (no BSSID auto-mapping, no real jitter/packet-loss), but there's nothing to install and no OS security warning.

## Developer Setup (Server-side)
To run the central server on your own machine (needed to be running in real-time to gather data):

1.  **Environment:** Create and activate a virtual environment.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
2.  **Install Requirements:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Start FastAPI:**
    ```bash
    uvicorn server:app --host 0.0.0.0 --port 8000
    ```
4.  **Production deployment:** for the live `whyphy.app` deployment, see `deploy/README.md` — it covers hosting on an always-on VPS with Caddy + automatic HTTPS. For updating the live server after a push, see `docs/UPDATING.md`.

## Data & Analysis
The project stores logs in a SQLite database (`data/campus_wifi.db`, created automatically on first run). We use this data to:
* Generate a "Leaderboard" of the fastest campus buildings.
* Identify consistent dead zones in older campus infrastructure.
* **Potentially:** Build a Machine Learning model to predict network congestion based on the day of the week and time.

---
*Maintained by Aleco Dominguez, Jaden Beil, RJ Edwards | University of Arizona*
