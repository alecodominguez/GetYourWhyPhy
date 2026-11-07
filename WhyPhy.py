"""
File: WhyPhy.py
Purpose: This file mainly gets the current WiFi data from the connection between any given device
in the form of ssid, signal strength, upload speed, download speed, and latency using the 3rd party library
"speedtest.cli". Furthermore, the program has methods to measure jitter, and packet loss based off the
"host" measure. This requires more precise calculations that measure the ates of change over time but are
key to proper WiFi. All these data points are too much to process for a user so we create a rating that takes all of
these into a single value. We take this values, convert them to a value within a range (0-100) and
finally apply them to a weighted scale to have an accurate final value of WiFi quality. This file
prints the individual WiFi assesment report for the user to see. Lastly, we take the adapted raw
data and export it to the server as a "request".

CHANGELOG (Nov 2026, data-quality pass):
  - measure_jitter() parsed only the last ping line, so jitter was always 0.0.
  - measure_packet_loss() returned the packet COUNT (20) on macOS/Linux.
  - Removed the extra /vote-bssid call: /log-wifi already records the vote,
    so each run was counted as two confirmations.
  - Payload now carries an anonymous contributor_id and client_version.
  - Scoring moved to scoring.py (shared with the server).

CHANGELOG (previous revision):
  - Added BSSID capture to get_signal_info() so bssid_resolver.py can attempt
    Automated Mapping before falling back to the manual building prompt.
  - Added device_profile integration so the exported payload carries a
    device-relative score alongside the original absolute score.
  - export_to_server() now points at the whyphy.app deployment
    instead of the ngrok tunnel (see deploy/README.md for the migration).

requirements (install via conda/pip):
    1. clone the repository
    2. "cd [INSERT FILEPATH]"
    3. activate virtual environment
        a. "macOS --> source .venv/bin/activate"
        b. "Windows --> .\venv\Scripts\Activate"
            i. if error, run: "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process"
    4. "pip install -r requirements.txt"
        a. this contains speedtest-cli and other dependencies
    5. python WhyPhy.py
"""
import requests  # needed to "push" data collected to database
import time  # this is used to time how long the speed test takes
import socket  # low-level networking (imported but available for future use maybe)
import subprocess  # lets us run terminal commands like "ping" from within Python
import platform  # detects the OS (Windows / macOS / Linux) so we use the right commands
import psutil  # reads system info like network stats (this has to be installed separately)
import datetime  # gives each run a specific date
import re
import locations  # Verify the location is on campus
import os
import certifi

import bssid_resolver     # Automated Mapping
import contributor        # anonymous per-install ID (for one-vote-per-contributor)
import device_profile     # Device Profiling
import scoring

# fix to set the SSL certificate path to the one provided by the certifi bundle
os.environ['SSL_CERT_FILE'] = certifi.where()

# DEPENDENCIES
# ensure speedtest.cli is installed. We catch the error to help the user install it.
try:
    import speedtest
except ImportError:
    print(
        "Missing dependency. Install all dependencies by running ")
    raise SystemExit(1)

# SCORING
# Weights, thresholds and the 0-100 linear mapping now live in scoring.py so the
# client, the passive monitor and the server all use one definition (the
# server recomputes the stored score from the raw metrics).
from scoring import WEIGHTS, THRESHOLDS, clamp, score_metric  # noqa: E402  (re-exported)

# Bumped whenever the measurement code changes in a way the server should know
# about. 2 = jitter/packet-loss parsing fixed (Nov 2026); clients that send no
# version are treated as having the old parsing bugs.
CLIENT_VERSION = 2

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
    Jitter = standard deviation of the individual ping round-trip times.
    High jitter = unstable connection (bad for voice/video calls).

    Returns None (not 0.0) when fewer than 2 replies could be parsed, so a
    failed measurement is never scored as perfect jitter.

    Bug fix (Nov 2026): the marker loop used to sit OUTSIDE the
    `for line in lines` loop, so only the last line of ping output (the summary,
    which never contains "time=") was checked. rtts was always empty and every
    native report was sent with jitter = 0.0.
    """
    system = platform.system()
    # OS Command variation: Windows uses '-n', Unix uses '-c'
    cmd = ["ping", "-n" if system == "Windows" else "-c", str(count), host]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None

    # Matches "time=12.3 ms" (macOS/Linux), "time=12ms" and "time<1ms" (Windows)
    rtts = [float(m) for m in re.findall(r"time[=<]\s*([\d.]+)", result.stdout.lower())]
    if len(rtts) < 2:
        return None

    mean = sum(rtts) / len(rtts)
    variance = sum((r - mean) ** 2 for r in rtts) / len(rtts)
    return round(variance ** 0.5, 2)


def measure_packet_loss(host="8.8.8.8", count=20):
    """
    Runs a longer ping test and returns the % of packets lost, or None if the
    summary line couldn't be parsed.

    Bug fix (Nov 2026): the old parser returned the FIRST number on the
    summary line. On macOS/Linux that line is "20 packets transmitted, 20
    received, 0.0% packet loss", so it returned 20.0 (the packet count) every
    time. Windows ("Lost = 0 (0% loss)") happened to work. Now we read the
    number directly in front of the "%".
    """
    system = platform.system()
    cmd = ["ping", "-n" if system == "Windows" else "-c", str(count), host]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return None

    m = re.search(r"([\d.]+)%\s*(?:packet\s+)?loss", result.stdout.lower())
    return round(float(m.group(1)), 1) if m else None


def get_signal_info():
    """Returns (ssid, signal, bssid). BSSID is what powers Automated Mapping
    in bssid_resolver.py - it's the MAC address of the specific access point
    radio the device is associated with, which stays fixed to a physical
    location even though signal strength and SSID name don't."""
    os_name = platform.system()
    ssid = "Unknown"
    signal = "N/A"
    bssid = None

    try:
        if os_name == "Windows":
            # Windows: uses 'netsh'
            cmd = "netsh wlan show interfaces"
            output = subprocess.check_output(cmd, shell=True).decode('cp1252')

            for line in output.split('\n'):
                if " SSID" in line and "BSSID" not in line:
                    ssid = line.split(":")[1].strip()
                if "Signal" in line:
                    signal = line.split(":")[1].strip()  # Returns percentage (e.g. 90%)
                if "BSSID" in line:
                    bssid = line.split(":", 1)[1].strip().lower()

        elif os_name == "Darwin":
            try:
                cmd = "/usr/sbin/system_profiler SPAirPortDataType"
                output = subprocess.check_output(cmd, shell=True).decode()
                lines = [line.strip() for line in output.split('\n') if line.strip()]
                for i in range(len(lines)):
                    # 1. Improved SSID Parsing
                    # 'Current Network Information:' is the parent label.
                    # The very next line in system_profiler is usually the SSID itself.
                    if "Current Network Information:" in lines[i]:
                        if i + 1 < len(lines):
                            # The SSID usually ends in a colon in the profiler; we remove it
                            potential_ssid = lines[i + 1].replace(":", "").strip()
                            if "Network Type" not in potential_ssid:
                                ssid = potential_ssid
                    # 2. Signal to Percentage Math
                    if "Signal / Noise:" in lines[i]:
                        raw_dbm = lines[i].split(":")[1].split("/")[0].strip().replace(" dBm", "")
                        dbm_int = int(raw_dbm)
                        # Linear mapping: -50dBm (100%) to -100dBm (0%)
                        quality = max(0, min(100, 2 * (dbm_int + 100)))
                        signal = f"{quality}%"
                    # 3. BSSID
                    if "BSSID:" in lines[i]:
                        bssid = lines[i].split(":", 1)[1].strip().lower()
            except Exception:
                pass

        elif os_name == "Linux":
            # Linux: uses 'nmcli' (Network Manager)
            cmd = "nmcli -t -f active,ssid,signal,bssid dev wifi"
            output = subprocess.check_output(cmd, shell=True).decode()

            for line in output.split('\n'):
                if line.startswith("yes"):
                    parts = line.split(":")
                    ssid = parts[1]
                    signal = f"{parts[2]}%"
                    if len(parts) > 3:
                        bssid = parts[3].lower()

    except Exception:
        # If a command fails or a tool isn't installed, we fail gracefully
        pass

    return ssid, signal, bssid


# UI / FORMATTIng
def bar(score, width=30):
    """Generates a visual progress bar: [████░░░]"""
    filled = int(clamp(score, 0, 100) / 100 * width)
    return "█" * filled + "░" * (width - filled)


def grade(score):
    """Maps the 0-100 numerical score to a traditional letter grade."""
    if score >= 90: return "A+", "Excellent"
    if score >= 80: return "A", "Great"
    if score >= 70: return "B", "Good"
    if score >= 60: return "C", "Fair"
    if score >= 45: return "D", "Poor"
    return "F", "Very Poor"


def export_to_server(data, location_name, source="native"):
    """
    Sends the collected metrics to the FastAPI central server.

    The server records the BSSID -> building vote itself from this one request
    (once per contributor per BSSID), so there is no separate /vote-bssid call
    any more. Sending both used to count every run as two confirmations.
    """
    URL = "https://whyphy.app/log-wifi"
    payload = {
        "location": location_name,
        "download": data["download"],
        "upload": data["upload"],
        "latency": data["latency"],
        "jitter": data["jitter"],            # None = couldn't be measured
        "packet_loss": data["packet_loss"],  # None = couldn't be measured
        "score": data["score"],              # informational; server recomputes
        "ssid": data["ssid"],
        "bssid": data.get("bssid"),
        "score_adjusted": data.get("score_adjusted"),
        "device_os": data.get("device_os"),
        "device_wifi_standard": data.get("device_wifi_standard"),
        "contributor_id": contributor.get_contributor_id(),
        "client_version": CLIENT_VERSION,
        "source": source,
    }

    try:
        response = requests.post(URL, json=payload, timeout=10)
        try:
            server_response = response.json()
        except ValueError:
            server_response = {}
        if response.status_code == 200 and server_response.get("status") == "success":
            print(f"\n[✓] Data successfully synced to central database.")
        elif response.status_code in (400, 422):
            print(f"\n[!] Rejected: {server_response.get('detail')}")
        else:
            print(f"\n[!] Server error: {response.status_code}")

    except requests.exceptions.RequestException:
        print(f"\n[!] Connection failed: Could not reach the server.")


# MAIN EXECUTION
def main():
    # 1. Initialization and Signal Check
    ssid, signal, bssid = get_signal_info()
    print("-" * 30)
    print(f"Connected to: {ssid}")
    print(f"Signal Strength: {signal}")
    print("-" * 30)

    # NEW: try Automated Mapping via BSSID before falling back to manual entry
    location_name = bssid_resolver.resolve_building(bssid)
    if location_name:
        print(f"[i] Auto-detected building from BSSID: {location_name}")
        confirm = input("Press Enter to confirm, or type a different building: ").strip()
        if confirm:
            location_name = confirm
    else:
        location_name = input("Enter your current building/location: ").strip()

    # 2. Data Gathering
    print("[1/3] Speed Test...")
    download, upload, latency = measure_speed()

    if latency <= 0 or latency >= 10_000:
      print("Speed test couldn't reach a server (latency check failed). Try again.")
      return

    print("[2/3] Jitter...")
    jitter = measure_jitter()

    print("[3/3] Packet Loss...")
    packet_loss = measure_packet_loss()

    # 3. Scoring Logic (shared with the server via scoring.py; a metric that
    # couldn't be measured is left out and the remaining weights renormalised)
    raw_data = {
        "download": download, "upload": upload,
        "latency": latency, "jitter": jitter, "packet_loss": packet_loss
    }

    sub_scores = scoring.sub_scores(raw_data)
    total = scoring.weighted_total(sub_scores)
    letter, label = grade(total)

    # Device Profiling - device-relative score, shown alongside (never
    # instead of) the absolute score so the leaderboard stays comparable.
    profile = device_profile.get_device_profile()
    score_adjusted = scoring.compute_adjusted_score(raw_data, profile["wifi_standard"])

    # 4. Final Report Output
    SEP = "═" * 58
    print(f"\n{SEP}\n  DETAILED NETWORK REPORT\n{SEP}")

    # This dictionary maps your raw keys to human-readable names and units
    display_labels = {
        "download": ("Download Speed", f"{download} Mbps"),
        "upload": ("Upload Speed", f"{upload} Mbps"),
        "latency": ("Latency (Ping)", f"{latency} ms"),
        "jitter": ("Jitter", f"{jitter} ms" if jitter is not None else "n/a"),
        "packet_loss": ("Packet Loss", f"{packet_loss}%" if packet_loss is not None else "n/a"),
    }

    # The loop that brings back the [███░░] bars
    for metric, (name, measured) in display_labels.items():
        w = WEIGHTS[metric]
        if metric not in sub_scores:
            print(f"  {name:<18} {measured:>12}   (not measured - left out of the score, weight {w}%)")
            continue
        s = sub_scores[metric]
        print(f"  {name:<18} {measured:>12}   [{bar(s, 20)}] {s:5.1f}/100  (weight {w}%)")

    print(SEP)
    print(f"  OVERALL SCORE: {total}/100 [{letter}] - {label}")
    print(f"  {bar(total, 50)}")
    if score_adjusted is not None:
        print(f"  (Relative to your {profile['wifi_standard']} adapter: {score_adjusted}/100)")
    print(SEP)

    matched_building = locations.get_standard_name(location_name)

    if matched_building:
        # If found, use the matched_building (e.g., "Student Recreation Center")
        # export Step
        export_data = raw_data.copy()
        export_data["score"] = total
        export_data["ssid"] = ssid
        export_data["bssid"] = bssid
        export_data["score_adjusted"] = score_adjusted
        export_data["device_os"] = profile["os"]
        export_data["device_wifi_standard"] = profile["wifi_standard"]

        # /log-wifi also records this run as this contributor's BSSID vote.
        export_to_server(export_data, matched_building)
    else:
        print(f"\n[!] '{location_name}' isn't a recognized UofA building, so this run wasn't uploaded.")

    # keeps terminal open so user can view personalized report
    input("\nPress Enter to close this window...")

if __name__ == "__main__":
    main()
