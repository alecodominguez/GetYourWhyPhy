"""
File: device_profile.py
Purpose: Reviews the contributing device's networking hardware so the
WhyPhy Score is produced relative to what that hardware is actually
capable of. It gives the score a weight contribution to teh average so
someone on a 2015 laptop will not produce a result of the same weight as
someone with a Wi-Fi 6E card. This now really emphazised the "PHY" from WHYPHY.

This stems from the "future work" section:
  1. What Wi-Fi standard ceiling is this adapter capable of?
  2. Given that ceiling, how should we adjust the raw score?

Design notes:
  1. We NEVER hide or overwrite the raw, absolute metrics. Two scores are
    always kept: "score" and "score_adjusted" (ie. is hardware the problem?).
    Mixing these silently would make the leaderboard misleading.
  2. Radio type detection reuses the same OS commands WhyPhy.py already
    shells out to. No new permissions needed.
  3. If detection fails, we fall back to a conservative "unknown" ceiling.
"""
import platform
import re
import subprocess
import psutil

# Theoretical max PHY throughput (in Mbps) per Wi-Fi generation, single-stream
# real-world achievable figure, not the max Gbps.
WIFI_STANDARD_CEILINGS = {
    "802.11n": 150,     # Wi-Fi 4
    "802.11ac": 433,    # Wi-Fi 5
    "802.11ax": 600,    # Wi-Fi 6 / 6E
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
    Method returns one of the WIFI_STANDARD_CEILINGS keys after parsing the same
    OS-level Wi-Fi interface info that WhyPhy.py reads for the SSID/BSSID data.
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
    This method collects a lightweight, non-identifying hardware profile:
    OS family, CPU core count, RAM (in GB), and Wi-Fi standard.
    Deliberately excludes anything identifying. Only used to calibrate the hardware.
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
    Method returns a 0-100 score for download capabilities relative to
    what this the device's Wi-Fi standard is. It replaces teh absolute
    score used for all devices. If standard is unknown we just use the
    absolute score
    """
    ceiling = WIFI_STANDARD_CEILINGS.get(wifi_standard)
    if not ceiling:
        return None
    ratio = (download_mbps - floor) / (ceiling - floor)
    return round(max(0, min(100, ratio * 100)), 1)

if __name__ == "__main__":
    import json
    print(json.dumps(get_device_profile(), indent=2))
