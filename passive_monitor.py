"""
File: passive_monitor.py
Purpose: Passive Testing is a software revival of the original "IoT Ghost"
idea, but running as a lightweight background process on a contributor's
own machine instead of dedicated hardware. It does NOT run a full
speedtest-cli throughput test on a loop (that would hammer both the
user's bandwidth and the speedtest.net servers); instead it does cheap,
frequent liveness checks and only escalates to a full WhyPhy test
occasionally.

What it does:
  1. Every CHECK_INTERVAL seconds ther is one ping to the default gateway.
  2. If N consecutive pings fail it logs a "drop event" and, once connectivity
  returns, reports the event to the server.
  3. Every FULL_TEST_INTERVAL seconds (30min) it runs one real
    WhyPhy.py-style benchmark, reusing the same measure_speed() and score
    pipeline, so passive data stays comparable to manually-submitted data.
"""
import platform
import subprocess
import time
from datetime import datetime, timezone
import requests
import WhyPhy  # reuse measure_speed / measure_jitter / scoring, no duplication
import bssid_resolver
from locations import get_standard_name

CHECK_INTERVAL = 30           # 30 seconds between lightweight gateway pings
FAILURE_THRESHOLD = 3         # 3 consecutive failed pings before we call it a "drop"
FULL_TEST_INTERVAL = 30 * 60  # 30 minutes between full speedtest benchmarks
SERVER_BASE = "https://whyphy.app"

def _default_gateway():
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output("ipconfig", shell=True).decode(errors="ignore")
            for line in out.splitlines():
                if "Default Gateway" in line and ":" in line:
                    val = line.split(":", 1)[1].strip()
                    if val:
                        return val
        else:
            out = subprocess.check_output("netstat -rn", shell=True).decode(errors="ignore")
            for line in out.splitlines():
                if line.startswith("default") or line.startswith("0.0.0.0"):
                    return line.split()[1]
    except Exception:
        pass
    return "8.8.8.8"  # fall back to a stable public host if gateway can't be determined


def _ping_once(host):
    system = platform.system()
    cmd = ["ping", "-n" if system == "Windows" else "-c", "1", "-W" if system != "Windows" else "-w",
           "2" if system != "Windows" else "2000", host]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

def report_drop_event(started_at, ended_at, building):
    payload = {
        "building": building,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
    }
    try:
        requests.post(f"{SERVER_BASE}/log-drop", json=payload, timeout=5,
                       headers={"ngrok-skip-browser-warning": "true"})
    except requests.exceptions.RequestException:
        pass

def run_full_benchmark(building):
    """Runs the same measurement pipeline as WhyPhy.py's interactive flow,
    without prompting and is used for scheduled passive samples."""
    download, upload, latency = WhyPhy.measure_speed()
    jitter = WhyPhy.measure_jitter()
    packet_loss = WhyPhy.measure_packet_loss()
    raw = {"download": download, "upload": upload, "latency": latency,
           "jitter": jitter, "packet_loss": packet_loss}
    sub_scores = {m: WhyPhy.score_metric(raw[m], *WhyPhy.THRESHOLDS[m]) for m in raw}
    total = round(sum(sub_scores[m] * WhyPhy.WEIGHTS[m] / 100 for m in WhyPhy.WEIGHTS), 1)
    ssid, _ = WhyPhy.get_signal_info()
    raw["score"] = total
    raw["ssid"] = ssid
    WhyPhy.export_to_server(raw, building)


def main(building_override=None):
    #Lets user know about this IoT process
    print("[WhyPhy Passive Monitor] Starting. Press Ctrl+C or run "
          "'whyphy-monitor stop' to end.")

    gateway = _default_gateway()
    bssid = bssid_resolver.get_bssid()
    building = building_override or bssid_resolver.resolve_building(bssid)

    if not building:
        building = input("Couldn't auto-detect your building yet. "
                          "Enter it once so this session can be tagged: ").strip()
        building = get_standard_name(building) or building
        bssid_resolver.submit_bssid_vote(bssid, building)

    consecutive_failures = 0
    drop_started = None
    last_full_test = time.time()

    try:
        while True:
            ok = _ping_once(gateway)
            if ok:
                if drop_started:
                    report_drop_event(drop_started, datetime.now(timezone.utc), building)
                    print(f"[✓] Connectivity restored after "
                          f"{(datetime.now(timezone.utc) - drop_started).total_seconds():.0f}s")
                    drop_started = None
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= FAILURE_THRESHOLD and not drop_started:
                    drop_started = datetime.now(timezone.utc)
                    print(f"[!] Connectivity drop detected at {drop_started.isoformat()}")

            if time.time() - last_full_test >= FULL_TEST_INTERVAL:
                print("[i] Running scheduled full benchmark...")
                try:
                    run_full_benchmark(building)
                except Exception as e:
                    print(f"[!] Scheduled benchmark failed: {e}")
                last_full_test = time.time()

            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        print("\n[WhyPhy Passive Monitor] Stopped.")

if __name__ == "__main__":
    main()
