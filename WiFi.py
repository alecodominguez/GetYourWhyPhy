"""
requirements (install via conda/pip):
    pip install speedtest-cli psutil
    OR
    conda install -c conda-forge speedtest-cli psutil

    to run this, install dependencies and run in bash: python Wifi.py
    - - - MAKE SURE YOU ARE IN THE FOLDER WITH Wifi.py - - -
"""
import requests  # needed to "push" data collected to database
import time # this is used to time how long the speed test takes
import socket # low-level networking (imported but available for future use maybe)
import subprocess # lets us run terminal commands like "ping" from within Python
import platform # detects the OS (Windows / macOS / Linux) so we use the right commands
import psutil # reads system info like network stats (this has to be installed separately)
import datetime # gives each run a specific date

# DEPENDENCY CHECK
# Speedtest is a third-party library; we catch the error to help the user install it.
try:
    import speedtest
except ImportError:
    print("Missing dependency. Install it with: pip install speedtest-cli or conda install -c conda-forge speedtest-cli psutil")
    raise SystemExit(1)

# SCORING CONFIGURATION
# These weights determine how much each metric affects the final 0–100 linear score.
# We focus on Download and Latency
WEIGHTS = {
    "download": 35,    # Mbps
    "upload": 20,      # Mbps
    "latency": 30,     # ms (Lower is better)
    "jitter": 10,      # ms (Consistency)
    "packet_loss": 5,  # %  (Reliability)
}

# Thresholds define the range for each metric.
# For latency/jitter/loss, the values are reversed (e.g., 500ms is 0 points, 5ms is 100 points).
THRESHOLDS = {
    "download": (0, 200),
    "upload": (0, 100),
    "latency": (500, 5),
    "jitter": (100, 0),
    "packet_loss": (100, 0),
}

# MATHEMATICAL HELPERS
def clamp(val, lo, hi):
    """Ensures a number stays within a specific range (eg. prevents a score of 110/100)."""
    return max(lo, min(hi, val))

def score_metric(value, lo, hi):
    """
    Linearly maps a raw measurement to a 0–100 scale.
    Formula: ((Value - Floor) / (Range)) * 100
    """
    if hi == lo:
        return 0
    ratio = (value - lo) / (hi - lo)
    return round(clamp(ratio * 100, 0, 100), 1)

# NETWORK MEASUREMENT FUNCTIONS
def measure_speed():
    """Uses the speedtest-cli to find the nearest server and test throughput."""
    st = speedtest.Speedtest(secure=True)
    st.get_best_server()
    # speedtest-cli returns bits, we divide by 1M to get Megabits (Mbps)
    dl = st.download() / 1_000_000
    ul = st.upload() / 1_000_000
    latency = st.results.ping
    return round(dl, 2), round(ul, 2), round(latency, 2)

def measure_jitter(host="8.8.8.8", count=10):
    """
    Calculates Jitter by measuring the standard deviation of Ping responses.
    High Jitter = unstable connection (bad for voice calls).
    """
    system = platform.system()
    # OS Command variation: Windows uses '-n', Unix uses '-c'
    cmd = ["ping", "-n" if system == "Windows" else "-c", str(count), host]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.splitlines()
        rtts = []

        # Regex-less parsing: looks for 'time=' in the ping output strings
        for line in lines:
            line_lower = line.lower()
            for marker in ["time=", "time<"]:
                if marker in line_lower:
                    idx = line_lower.index(marker) + len(marker)
                    num = "".join(ch for ch in line[idx:] if ch.isdigit() or ch == ".")
                    if num:
                        rtts.append(float(num))

        if len(rtts) < 2: return 0.0

        # Standard Deviation calculation for Jitter
        mean = sum(rtts) / len(rtts)
        variance = sum((r - mean) ** 2 for r in rtts) / len(rtts)
        return round(variance ** 0.5, 2)
    except Exception:
        return 0.0

def measure_packet_loss(host="8.8.8.8", count=20):
    """Runs a longer ping test to see if any data packets were dropped entirely."""
    system = platform.system()
    cmd = ["ping", "-n" if system == "Windows" else "-c", str(count), host]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stdout.lower()
        # Searches the summary text for the percentage of lost packets
        for line in output.splitlines():
            if "loss" in line or "lost" in line:
                for word in line.split():
                    word = word.strip("(%)")
                    try:
                        return round(float(word), 1)
                    except ValueError:
                        continue
        return 0.0
    except Exception:
        return 0.0

def get_signal_info():
    """
    Platform-specific logic to extract the WiFi Network Name (SSID) and Signal Strength.
    - Windows: netsh wlan
    - macOS: airport utility
    - Linux: iwgetid / iwconfig
    """
    system = platform.system()
    ssid, signal = "Unknown", "N/A"

    try:
        if system == "Windows":
            out = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True)
            for line in out.splitlines():
                if "SSID" in line and "BSSID" not in line:
                    ssid = line.split(":", 1)[-1].strip()
                if "Signal" in line:
                    signal = line.split(":", 1)[-1].strip() # Returns as %
        elif system == "Darwin": # macOS
            airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
            out = subprocess.check_output([airport, "-I"], text=True)
            for line in out.splitlines():
                if " SSID:" in line:
                    ssid = line.split(":", 1)[-1].strip()
                if "agrCtlRSSI" in line:
                    signal = f"{line.split(':', 1)[-1].strip()} dBm"
    except Exception:
        pass
    return ssid, signal

# UI / FORMATTING
def bar(score, width=30):
    """Generates a visual progress bar: [████░░░]"""
    filled = int(clamp(score, 0, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)

def grade(score):
    """Maps the 0-100 numerical score to a traditional letter grade."""
    if score >= 90: return "A+", "Excellent"
    if score >= 80: return "A",  "Great"
    if score >= 70: return "B",  "Good"
    if score >= 60: return "C",  "Fair"
    if score >= 45: return "D",  "Poor"
    return "F", "Very Poor"

def export_to_server(data, location_name):
    """
    Sends the collected metrics to the FastAPI central server.
    """
    # Replace with your actual server URL (e.g., your PythonAnywhere or Pi address)
    URL = "http://your-server-address.com/log-wifi"

    payload = {
        "location": location_name,
        "download": data["download"],
        "upload": data["upload"],
        "latency": data["latency"],
        "jitter": data["jitter"],
        "packet_loss": data["packet_loss"],
        "score": data["total_score"],
        "ssid": data["ssid"]
    }

    try:
        response = requests.post(URL, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"\n[✓] Data successfully synced to central database.")
        else:
            print(f"\n[!] Server error: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"\n[!] Connection failed: Could not reach the server.")

# MAIN EXECUTION
def main():
    # 1. Initialization and Signal Check
    ssid, signal = get_signal_info()
    print(f"Network: {ssid} | Signal: {signal}")

    # NEW: Ask the user where they are so the data is labeled for ML
    location_name = input("Enter your current building/location: ").strip()

    # 2. Data Gathering
    print("[1/3] Speed Test...")
    download, upload, latency = measure_speed()

    print("[2/3] Jitter...")
    jitter = measure_jitter()

    print("[3/3] Packet Loss...")
    packet_loss = measure_packet_loss()

    # 3. Scoring Logic
    raw_data = {
        "download": download, "upload": upload,
        "latency": latency, "jitter": jitter, "packet_loss": packet_loss
    }

    sub_scores = {m: score_metric(raw_data[m], *THRESHOLDS[m]) for m in raw_data}
    total = round(sum(sub_scores[m] * WEIGHTS[m] / 100 for m in WEIGHTS), 1)
    letter, label = grade(total)

    # 4. Final Report Output
    print(f"\nOVERALL SCORE: {total}/100 [{letter}] - {label}")

    # 5. NEW: Export Step
    export_data = raw_data.copy()
    export_data["total_score"] = total
    export_data["ssid"] = ssid

    export_to_server(export_data, location_name)


if __name__ == "__main__":
    main()