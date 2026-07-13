# GetYourWhyPhy
[Click here to check it out](https://whyphy.app/)

**GetYourWhyPhy** is a distributed WiFi performance mapping tool built for the University of Arizona campus. It crowdsources network metrics—such as Signal Strength, Download/Upload speeds, Latency, and Packet Loss—to identify the best (and worst) study spots across campus buildings.

The name is a play on the "PHY" (physical) layer of the OSI (Open Systems Interconnection) network stack. This layer determines whether your Wi-Fi is fast and stable or slow and dropping. WhyPhy measures that layer directly, so instead of guessing which building has good Wi-Fi, you can see it.

The system consists of three main components working together to map campus connectivity:
1.  **The Client (`WiFi.py`):** A Python script run by users to perform localized network diagnostics.
2.  **The Backend (`server.py`):** A FastAPI server that receives results and stores them in a database.
3.  **The Tunnel (ngrok):** A secure bridge that allows campus users to reach the local server through university firewalls.

## Demo Video
<p align="center">
  <a href="https://youtu.be/VJthtA6CzGs">
    <img src="https://img.youtube.com/vi/VJthtA6CzGs/0.jpg" alt="Watch the Demo Video" width="70%">
  </a>
</p>

## Research Paper

Our full paper, detailing the distributed software architecture, network normalization algorithm, and evaluation metrics, was prepared for the *University of Arizona Research Showcase (May 2026)*.

* **[Read the Full PDF Paper](./paper/GetYourWhyPhy_Paper.pdf)**
* **[View LaTeX Source Files](./paper/)**

## Setup for Contributors
The users gain kjnowledge on campus WiFi in exchange for their current WiFi information and Campus building location. Follow these steps:

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
4.  **Expose the Server:** If running on a restricted network (UAWiFi), use ngrok to have your own public domain.

## Data & Analysis
The project stores logs in a SQLite database (`campus_wifi.db`). We use this data to:
* Generate a "Leaderboard" of the fastest campus buildings.
* Identify consistent dead zones in older campus infrastructure.
* **Potentially:** Build a Machine Learning model to predict network congestion based on the day of the week and time. Also, transfer server to Raspberry Pi to run the server non-stop.

---
*Maintained by Aleco Dominguez, Jaden Beil, RJ Edwards | University of Arizona*
