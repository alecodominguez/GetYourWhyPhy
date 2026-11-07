"""
File: bssid_resolver.py
Purpose: Automated Mapping. Wi-Fi access points broadcast a BSSID (the MAC
address of the specific radio a device is associated with), and on a
campus network that BSSID is tied to a physical access point in a physical
room. Once enough contributors confirm "BSSID xx:xx:xx:xx:xx:xx = Main
Library", every future run from that same AP can skip the manual
building-name prompt entirely.

Important correction vs. the original ask: speedtest-cli itself has no
concept of BSSID - it only talks to speedtest.net servers, not the local
Wi-Fi radio. BSSID has to come from the OS Wi-Fi interface query (the same
netsh / system_profiler / nmcli calls WhyPhy.py already uses for SSID),
which is what get_bssid() below does.

Crowdsourced trust model:
  - A BSSID is only auto-applied once CONFIRMATION_THRESHOLD *distinct
    contributors* (anonymous per-install IDs, see contributor.py) have voted
    for the same building. The server enforces one vote per contributor per
    BSSID with a UNIQUE constraint, and the threshold lives on the server. Below that, the user
    is still asked to type/confirm their building, and that answer is sent
    up as a vote.
  - This prevents one mistyped location (or one malicious submission) from
    silently mislabeling an access point for everyone else.
"""
import json
import os
import time
from pathlib import Path

import requests

import contributor

SERVER_BASE = "https://whyphy.app"
# Absolute path under ~/.whyphy (it used to be a bare "bssid_cache.json", which
# landed in whatever directory the script happened to be launched from).
CACHE_FILE = Path.home() / ".whyphy" / "bssid_cache.json"
# The cache is a copy of server data, so it has to expire: a mapping can be
# corrected on the server (re-votes, an admin re-seed) after we cached it.
CACHE_TTL_SECONDS = 7 * 24 * 3600


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
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    """Write to a temp file then atomically rename, so a crash mid-write
    can't leave a half-written (unparseable) cache behind."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def _cached_building(cache, bssid):
    entry = cache.get(bssid)
    # Old cache format stored a bare building string with no timestamp:
    # treat it as expired so it gets re-validated against the server.
    if isinstance(entry, dict) and time.time() - entry.get("fetched_at", 0) < CACHE_TTL_SECONDS:
        return entry.get("building")
    return None


def resolve_building(bssid):
    """
    Looks up a BSSID against the server's crowdsourced registry.
    Uses a local cache entry if it is younger than CACHE_TTL_SECONDS, otherwise
    asks the server. If the server is unreachable, a stale cache entry is still
    better than nothing. Returns None if unresolved - caller falls back to
    asking the user.
    """
    if not bssid:
        return None

    cache = _load_cache()
    fresh = _cached_building(cache, bssid)
    if fresh:
        return fresh

    try:
        resp = requests.get(f"{SERVER_BASE}/resolve-bssid/{bssid}", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("confirmed"):
                cache[bssid] = {"building": data["building"], "fetched_at": time.time()}
                _save_cache(cache)
                return data["building"]
            # Server no longer vouches for it: invalidate our copy.
            if bssid in cache:
                del cache[bssid]
                _save_cache(cache)
            return None
    except requests.exceptions.RequestException:
        pass  # offline or server down - fall through to a stale entry, if any

    entry = cache.get(bssid)
    if isinstance(entry, dict):
        return entry.get("building")
    return entry if isinstance(entry, str) else None


def submit_bssid_vote(bssid, building):
    """
    Records this contributor's vote that `bssid` is in `building`.

    Only needed when no benchmark is being uploaded (e.g. the passive monitor's
    one-time building prompt). WhyPhy.py does NOT call this: /log-wifi already
    records the vote, and calling both used to double-count every run.
    The server keeps at most one vote per (contributor, BSSID).
    """
    if not bssid:
        return
    try:
        requests.post(
            f"{SERVER_BASE}/vote-bssid",
            json={"bssid": bssid, "building": building,
                  "contributor_id": contributor.get_contributor_id()},
            timeout=5,
        )
    except requests.exceptions.RequestException:
        pass
