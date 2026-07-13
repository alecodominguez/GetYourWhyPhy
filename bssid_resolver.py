"""
File: bssid_resolver.py
Purpose: Automated Mapping. Wi-Fi access points broadcast a BSSID (the MAC
address of the specific radio a device is associated with), and on a
campus network that BSSID is tied to a physical access point in a physical
room. Once enough contributors confirm "BSSID xx:xx:xx:xx:xx:xx = Main
Library", every future run from that same AP can skip the manual
building-name prompt entirely.

Dependencies:
  - A BSSID is only auto-applied once it has reached CONFIRMATION_THRESHOLD
    independent confirmations for the building. Below that, the user
    is still asked to type/confirm their building, and that answer is sent
    up as a vote.
  - This prevents one mistyped location (or one malicious submission) from
    silently mislabeling an access point for everyone else.
"""
import requests

SERVER_BASE = "https://whyphy.app"
CACHE_FILE = "bssid_cache.json"
CONFIRMATION_THRESHOLD = 3


def get_bssid():
    """
    Reads the BSSID of the currently associated access point.
    Reuses the OS-specific parsing already present in WhyPhy.get_signal_info,
    duplicated narrowly here so this module has no import-time dependency
    on WhyPhy.py (keeps it testable / usable standalone).
    """
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.check_output("netsh wlan show interfaces", shell=True).decode("cp1252", errors="ignore")
            for line in out.splitlines():
                if "BSSID" in line:
                    return line.split(":", 1)[1].strip().lower()

        elif system == "Darwin":
            out = subprocess.check_output(
                "/usr/sbin/system_profiler SPAirPortDataType", shell=True
            ).decode(errors="ignore")
            for line in out.splitlines():
                if "BSSID" in line:
                    return line.split(":", 1)[1].strip().lower()

        elif system == "Linux":
            iface_out = subprocess.check_output(
                "iw dev 2>/dev/null | awk '/Interface/{print $2}'", shell=True
            ).decode(errors="ignore")
            iface = iface_out.strip().splitlines()[0] if iface_out.strip() else None
            if iface:
                link_out = subprocess.check_output(f"iw dev {iface} link", shell=True).decode(errors="ignore")
                for line in link_out.splitlines():
                    if "Connected to" in line:
                        return line.split("Connected to")[1].strip().split()[0].lower()
    except Exception:
        pass
    return None


def _load_cache():
    import json
    import os
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    import json
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def resolve_building(bssid):
    """
    Looks up a BSSID against the server's crowdsourced registry.
    Checks a local cache first (fast, works offline once seeded), then
    falls back to a server round-trip. Returns None if unresolved -
    caller should fall back to asking the user, exactly like today.
    """
    if not bssid:
        return None

    cache = _load_cache()
    if bssid in cache:
        return cache[bssid]

    try:
        resp = requests.get(
            f"{SERVER_BASE}/resolve-bssid/{bssid}",
            headers={"ngrok-skip-browser-warning": "true"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("confirmed"):
                cache[bssid] = data["building"]
                _save_cache(cache)
                return data["building"]
    except requests.exceptions.RequestException:
        pass  # offline or server down - fall back silently to manual entry

    return None


def submit_bssid_vote(bssid, building):
    """
    Sends a (bssid, building) pair to the server as a confirmation vote.
    Called after a user manually types/confirms their building, so the
    registry improves over time without ever trusting a single report.
    """
    if not bssid:
        return
    try:
        requests.post(
            f"{SERVER_BASE}/vote-bssid",
            json={"bssid": bssid, "building": building},
            headers={"ngrok-skip-browser-warning": "true"},
            timeout=5,
        )
    except requests.exceptions.RequestException:
        pass
