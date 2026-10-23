"""
File: device_profile.py
Purpose: Fingerprint the contributing device's networking hardware so the
WhyPhy Score can be interpreted relative to what that hardware is actually
capable of, instead of penalizing someone on a 2015 laptop for not hitting
the same numbers as someone with a Wi-Fi 6E card.

This module answers two separate questions:
  1. What Wi-Fi standard / theoretical ceiling is this adapter capable of?
  2. Given that ceiling, how should we adjust (not replace) the raw score?

Design notes:
  - We NEVER hide or overwrite the raw, absolute metrics. Two scores are
    always kept: "score" (absolute, comparable across all devices - what's
    shown on the leaderboard by default) and "score_adjusted" (relative to
    the device's own ceiling - useful for a "is MY hardware the problem"
    view). Mixing these silently would make the leaderboard misleading.
  - Radio type detection reuses the same OS commands WhyPhy.py already
    shells out to (netsh / system_profiler / nmcli), so there's no new
    permission surface.
  - If detection fails, we fall back to a conservative "unknown" ceiling
    (draws no adjustment) rather than guessing.
"""
import platform
import re
import subprocess

import psutil

# Theoretical max PHY throughput (Mbps) per Wi-Fi generation, single-stream
# real-world achievable figure (not the marketing "up to X Gbps" number).
WIFI_STANDARD_CEILINGS = {
    "802.11n": 150,     # Wi-Fi 4
    "802.11ac": 433,    # Wi-Fi 5
    "802.11ax": 600,    # Wi-Fi 6 / 6E (conservative single-stream figure)
    "802.11be": 1000,   # Wi-Fi 7
    "unknown": None,
}


def _run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, timeout=5).decode(errors="ignore")
    except Exception:
        return ""


def get_wifi_standard():
    """
    Returns one of the WIFI_STANDARD_CEILINGS keys by parsing the same
    OS-level Wi-Fi interface info WhyPhy.py already reads for SSID/BSSID.
    """
    system = platform.system()
    try:
        if system == "Windows":
            out = _run("netsh wlan show interfaces")
            m = re.search(r"Radio type\s*:\s*(.+)", out)
            if m:
                token = m.group(1).strip().lower()
                if "ax" in token or "6" in token:
                    return "802.11ax"
                if "ac" in token:
                    return "802.11ac"
                if "n" in token:
                    return "802.11n"

        elif system == "Darwin":
            out = _run("/usr/sbin/system_profiler SPAirPortDataType")
            m = re.search(r"PHY Mode:\s*(.+)", out)
            if m:
                token = m.group(1).strip().lower()
                if "ax" in token:
                    return "802.11ax"
                if "ac" in token:
                    return "802.11ac"
                if "n" in token:
                    return "802.11n"

        elif system == "Linux":
            out = _run("iw dev 2>/dev/null | awk '/Interface/{print $2}'")
            iface = out.strip().splitlines()[0] if out.strip() else None
            if iface:
                link_out = _run(f"iw dev {iface} link")
                # HT = 802.11n, VHT = 802.11ac, HE = 802.11ax
                if "HE-MCS" in link_out or "HE " in link_out:
                    return "802.11ax"
                if "VHT-MCS" in link_out:
                    return "802.11ac"
                if "MCS" in link_out:
                    return "802.11n"
    except Exception:
        pass

    return "unknown"


def get_device_profile():
    """
    Collects a lightweight, non-identifying hardware profile:
    OS family, CPU core count, RAM (GB, rounded), and Wi-Fi standard.
    Deliberately excludes anything identifying (hostname, MAC address,
    serial numbers) - this is for score calibration, not fingerprinting
    individual users.
    """
    try:
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3))
    except Exception:
        ram_gb = None

    return {
        "os": platform.system(),
        "cpu_cores": psutil.cpu_count(logical=True) or None,
        "ram_gb": ram_gb,
        "wifi_standard": get_wifi_standard(),
    }


def adjusted_download_score(download_mbps, wifi_standard, floor=0):
    """
    Returns a 0-100 score for download throughput RELATIVE to what this
    device's Wi-Fi standard can theoretically achieve, instead of the
    fixed 0-200 Mbps scale used for the absolute score. Returns None
    (meaning: "no adjustment, use the absolute score as-is") when the
    standard is unknown, so we never fabricate an adjustment.
    """
    ceiling = WIFI_STANDARD_CEILINGS.get(wifi_standard)
    if not ceiling:
        return None
    ratio = (download_mbps - floor) / (ceiling - floor)
    return round(max(0, min(100, ratio * 100)), 1)


if __name__ == "__main__":
    import json
    print(json.dumps(get_device_profile(), indent=2))
