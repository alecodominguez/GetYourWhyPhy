"""
File: passive_monitor.py
Purpose: Passive Testing - a software revival of the original "IoT Ghost" idea,
running as a lightweight background process on a contributor's own machine.

Consent and transparency:
  - Must be explicitly installed by the user (`install`), visible (`status`, or
    the tray icon if pystray/Pillow are installed) and stoppable with one
    command (`uninstall`, or the tray menu). Never bundled with or started
    silently by WhyPhy.py.
  - Measures NETWORK health only (gateway reachability, periodic benchmark).
    No traffic, browsing, DNS or other on-device inspection.

CLI:
    python passive_monitor.py run           # foreground (Ctrl+C to stop)
    python passive_monitor.py install       # register as a login/boot service
    python passive_monitor.py uninstall     # remove that service
    python passive_monitor.py status        # installed/running? queued events?
    python passive_monitor.py run --no-tray # no tray icon even if available

What `run` does:
  - Every CHECK_INTERVAL seconds: one ping to the default gateway.
  - FAILURE_THRESHOLD consecutive failures = a "drop event"; when connectivity
    returns it is reported to the server. If the server is unreachable, the
    event is queued locally (LocalDropQueue) and retried every tick.
  - Every FULL_TEST_INTERVAL seconds: one full WhyPhy benchmark, reusing
    WhyPhy.py's measurement and scoring so passive data stays comparable.
"""
import argparse
import getpass
import json
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import requests

import WhyPhy
import bssid_resolver
from locations import get_standard_name

CHECK_INTERVAL = 30           # seconds between lightweight gateway pings
FAILURE_THRESHOLD = 3         # consecutive failures before we call it a "drop"
FULL_TEST_INTERVAL = 30 * 60  # seconds between full benchmarks
SERVER_BASE = "https://whyphy.app"

WHYPHY_HOME = Path.home() / ".whyphy"
QUEUE_FILE = WHYPHY_HOME / "drop_queue.jsonl"
LOG_DIR = WHYPHY_HOME / "logs"

SERVICE_NAME = "com.whyphy.monitor"     # macOS launchd label
SYSTEMD_UNIT_NAME = "whyphy-monitor"    # Linux systemd --user unit
WINDOWS_TASK_NAME = "WhyPhyMonitor"     # Windows Task Scheduler task


# ---------------------------------------------------------------------------
# Local drop-event queue (survives the WhyPhy server being unreachable)
# ---------------------------------------------------------------------------
class LocalDropQueue:
    """One JSON object per line in ~/.whyphy/drop_queue.jsonl - easy to inspect
    by hand. Every loop tick tries to flush it, so a queued event is delivered
    within one CHECK_INTERVAL of the server coming back."""

    def __init__(self, path=QUEUE_FILE):
        self.path = path

    def enqueue(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(payload) + "\n")

    def flush(self):
        """Resend every queued event. Returns (sent, still_queued)."""
        if not self.path.exists():
            return 0, 0
        lines = [l for l in self.path.read_text().splitlines() if l.strip()]
        if not lines:
            return 0, 0

        still_queued, sent = [], 0
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue  # drop an unparseable line rather than retry it forever
            try:
                resp = requests.post(f"{SERVER_BASE}/log-drop", json=payload, timeout=5)
                if resp.status_code == 200:
                    sent += 1
                else:
                    still_queued.append(line)
            except requests.exceptions.RequestException:
                still_queued.append(line)

        if still_queued:
            self.path.write_text("\n".join(still_queued) + "\n")
        else:
            self.path.unlink(missing_ok=True)
        return sent, len(still_queued)

    def pending_count(self):
        if not self.path.exists():
            return 0
        return len([l for l in self.path.read_text().splitlines() if l.strip()])


# ---------------------------------------------------------------------------
# Network measurement helpers
# ---------------------------------------------------------------------------
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
    return "8.8.8.8"  # stable public fallback if the gateway can't be determined


def _ping_once(host):
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", "2000", host]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-t", "2", host]   # macOS: -t is the timeout
    else:
        cmd = ["ping", "-c", "1", "-W", "2", host]   # Linux: -W is the timeout
    try:
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def run_full_benchmark(building):
    """Same measurement pipeline as WhyPhy.py's interactive flow, no prompts."""
    download, upload, latency = WhyPhy.measure_speed()
    jitter = WhyPhy.measure_jitter()
    packet_loss = WhyPhy.measure_packet_loss()
    raw = {"download": download, "upload": upload, "latency": latency,
           "jitter": jitter, "packet_loss": packet_loss}
    sub_scores = {m: WhyPhy.score_metric(raw[m], *WhyPhy.THRESHOLDS[m]) for m in raw}
    raw["score"] = round(sum(sub_scores[m] * WhyPhy.WEIGHTS[m] / 100 for m in WhyPhy.WEIGHTS), 1)
    ssid, _, bssid = WhyPhy.get_signal_info()
    raw["ssid"] = ssid
    raw["bssid"] = bssid
    WhyPhy.export_to_server(raw, building)


# ---------------------------------------------------------------------------
# The monitor loop
# ---------------------------------------------------------------------------
class Monitor:
    def __init__(self, building_override=None, status_callback=None):
        self.queue = LocalDropQueue()
        self.stop_event = threading.Event()
        self.building_override = building_override
        self.status_callback = status_callback or (lambda text, dropped=False: None)

    def _resolve_building(self):
        bssid = bssid_resolver.get_bssid()
        building = self.building_override or bssid_resolver.resolve_building(bssid)
        if not building:
            if not sys.stdin or not sys.stdin.isatty():
                # Running as a background service: there is nobody to answer a
                # prompt. Fail loudly instead of hanging forever.
                raise SystemExit(
                    "Couldn't auto-detect your building and there's no terminal to ask. "
                    "Run `python passive_monitor.py run --building \"Main Library\"` once "
                    "(or seed this BSSID), then re-run install.")
            building = input("Couldn't auto-detect your building yet. "
                             "Enter it once so this session can be tagged: ").strip()
            building = get_standard_name(building) or building
            bssid_resolver.submit_bssid_vote(bssid, building)
        return building

    def report_drop_event(self, started_at, ended_at, building):
        payload = {
            "building": building,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": (ended_at - started_at).total_seconds(),
        }
        try:
            if requests.post(f"{SERVER_BASE}/log-drop", json=payload, timeout=5).status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        self.queue.enqueue(payload)  # don't lose the report

    def stop(self):
        self.stop_event.set()

    def run(self):
        print("[WhyPhy Passive Monitor] Starting. Ctrl+C, the tray menu, or "
              "'python passive_monitor.py uninstall' will stop it.")

        gateway = _default_gateway()
        building = self._resolve_building()

        sent, remaining = self.queue.flush()
        if sent:
            print(f"[i] Delivered {sent} previously-queued drop event(s).")
        if remaining:
            print(f"[i] {remaining} drop event(s) still queued - will keep retrying.")

        consecutive_failures = 0
        drop_started = None
        last_full_test = time.time()
        self.status_callback("monitoring")

        while not self.stop_event.is_set():
            if _ping_once(gateway):
                if drop_started:
                    now = datetime.now(timezone.utc)
                    self.report_drop_event(drop_started, now, building)
                    print(f"[OK] Connectivity restored after {(now - drop_started).total_seconds():.0f}s")
                    drop_started = None
                    self.status_callback("monitoring")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= FAILURE_THRESHOLD and not drop_started:
                    drop_started = datetime.now(timezone.utc)
                    print(f"[!] Connectivity drop detected at {drop_started.isoformat()}")
                    self.status_callback("connection dropped", dropped=True)

            self.queue.flush()  # cheap no-op when the queue is empty

            if time.time() - last_full_test >= FULL_TEST_INTERVAL:
                print("[i] Running scheduled full benchmark...")
                try:
                    run_full_benchmark(building)
                except Exception as e:
                    print(f"[!] Scheduled benchmark failed: {e}")
                last_full_test = time.time()

            self.stop_event.wait(CHECK_INTERVAL)

        print("\n[WhyPhy Passive Monitor] Stopped.")


# ---------------------------------------------------------------------------
# `run` - with or without the tray icon
# ---------------------------------------------------------------------------
def cmd_run(args):
    monitor = Monitor(building_override=args.building)

    use_tray = not args.no_tray
    if use_tray:
        try:
            import tray_icon
            use_tray = tray_icon.TRAY_AVAILABLE
        except ImportError:
            use_tray = False

    if not use_tray:
        if not args.no_tray:
            print("[i] pystray/Pillow not installed - running without a tray icon. "
                  "Install with 'pip install -r requirements-monitor.txt' for a status indicator.")
        try:
            monitor.run()
        except KeyboardInterrupt:
            monitor.stop()
        return

    import tray_icon
    tray = tray_icon.WhyPhyTray(
        on_stop=monitor.stop,
        on_open_dashboard=webbrowser.open,
        dashboard_url=SERVER_BASE,
    )
    monitor.status_callback = tray.set_status
    tray.run(setup=lambda: tray_icon.run_in_background_thread(monitor.run))


# ---------------------------------------------------------------------------
# install / uninstall / status - OS service wrapper
# ---------------------------------------------------------------------------
def _template(name):
    return (Path(__file__).parent / "client_service" / name).read_text()


def _install_macos():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    filled = (_template("com.whyphy.monitor.plist.template")
              .replace("{{PYTHON}}", sys.executable)
              .replace("{{SCRIPT}}", str(Path(__file__).resolve()))
              .replace("{{LOG_DIR}}", str(LOG_DIR)))
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{SERVICE_NAME}.plist"
    plist_path.write_text(filled)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
    print(f"[OK] Installed as a LaunchAgent ({plist_path}); starts at every login.")


def _uninstall_macos():
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_NAME}.plist"
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink(missing_ok=True)
    print("[OK] Uninstalled.")


def _status_macos():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    print(f"[OK] {SERVICE_NAME} is registered with launchd." if SERVICE_NAME in out
          else f"[i] {SERVICE_NAME} is not installed.")


def _install_linux():
    filled = (_template("whyphy-monitor.service.template")
              .replace("{{PYTHON}}", sys.executable)
              .replace("{{SCRIPT}}", str(Path(__file__).resolve())))
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / f"{SYSTEMD_UNIT_NAME}.service"
    unit_path.write_text(filled)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME], check=True)
    # Keeps the service alive with no login session (survives logout).
    subprocess.run(["loginctl", "enable-linger", getpass.getuser()], capture_output=True)
    print(f"[OK] Installed as a systemd --user unit ({unit_path}); lingering enabled.")


def _uninstall_linux():
    subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME], capture_output=True)
    (Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_UNIT_NAME}.service").unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("[OK] Uninstalled.")


def _status_linux():
    out = subprocess.run(["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
                         capture_output=True, text=True).stdout.strip()
    print(f"[i] {SYSTEMD_UNIT_NAME}: {out or 'not installed'}")


def _install_windows():
    script = str(Path(__file__).resolve())
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    interpreter = str(pythonw) if pythonw.exists() else sys.executable
    subprocess.run(["schtasks", "/create", "/tn", WINDOWS_TASK_NAME,
                    "/tr", f'"{interpreter}" "{script}" run',
                    "/sc", "onlogon", "/rl", "limited", "/f"], check=True)
    subprocess.run(["schtasks", "/run", "/tn", WINDOWS_TASK_NAME], capture_output=True)
    print(f"[OK] Installed as a Scheduled Task ({WINDOWS_TASK_NAME}); starts at every login.")


def _uninstall_windows():
    subprocess.run(["schtasks", "/end", "/tn", WINDOWS_TASK_NAME], capture_output=True)
    subprocess.run(["schtasks", "/delete", "/tn", WINDOWS_TASK_NAME, "/f"], capture_output=True)
    print("[OK] Uninstalled.")


def _status_windows():
    result = subprocess.run(["schtasks", "/query", "/tn", WINDOWS_TASK_NAME],
                            capture_output=True, text=True)
    print(result.stdout if result.returncode == 0 else f"[i] {WINDOWS_TASK_NAME} is not installed.")


_OS_HANDLERS = {
    "Darwin": {"install": _install_macos, "uninstall": _uninstall_macos, "status": _status_macos},
    "Linux": {"install": _install_linux, "uninstall": _uninstall_linux, "status": _status_linux},
    "Windows": {"install": _install_windows, "uninstall": _uninstall_windows, "status": _status_windows},
}


def _dispatch(action):
    handlers = _OS_HANDLERS.get(platform.system())
    if not handlers:
        print(f"[!] Unsupported OS: {platform.system()}")
        sys.exit(1)
    handlers[action]()


def cmd_install(args):
    _dispatch("install")


def cmd_uninstall(args):
    _dispatch("uninstall")


def cmd_status(args):
    _dispatch("status")
    pending = LocalDropQueue().pending_count()
    if pending:
        print(f"[i] {pending} drop event(s) queued locally, waiting for the server.")


def main():
    parser = argparse.ArgumentParser(description="WhyPhy passive connectivity monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run in the foreground (also the installed service's entry point)")
    run_p.add_argument("--building", default=None, help="Skip auto-detection/prompt and use this building")
    run_p.add_argument("--no-tray", action="store_true", help="Disable the tray icon even if available")
    run_p.set_defaults(func=cmd_run)

    sub.add_parser("install", help="Start automatically at login/boot").set_defaults(func=cmd_install)
    sub.add_parser("uninstall", help="Remove the auto-start registration").set_defaults(func=cmd_uninstall)
    sub.add_parser("status", help="Is it installed/running?").set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
