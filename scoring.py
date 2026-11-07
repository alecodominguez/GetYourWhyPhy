"""
File: scoring.py
Purpose: The single source of truth for the WhyPhy Score.

Before this module, the weights/thresholds lived in WhyPhy.py, were copied
into static/lite.js, and passive_monitor.py re-implemented the weighted sum.
The server just trusted whatever `score` the client sent. Now:

  - WhyPhy.py and passive_monitor.py import from here (for the on-screen report).
  - server.py RECOMPUTES the score from the raw metrics it receives, so the
    stored `score` is a derived attribute the server controls, and any row can
    be re-scored later if the weights change (raw inputs are kept for exactly
    that reason).
  - static/lite.js still mirrors these numbers for its on-screen preview, but
    the server-side recompute is what lands in the database.

Missing/invalid metrics: if a metric couldn't be measured (e.g. jitter from a
client with the old ping-parsing bug), it is EXCLUDED and the remaining
weights are renormalised to sum to 100, rather than silently scoring the
missing value as perfect (which is what jitter = 0 used to do).
"""

WEIGHTS = {
    "download": 35,    # Mbps, more weight
    "upload": 20,      # Mbps
    "latency": 30,     # ms (lower is better), more weight
    "jitter": 10,      # ms (consistency)
    "packet_loss": 5,  # % (reliability)
}

# (floor, ceiling). For latency/jitter/loss the axis is reversed
# (e.g. 500 ms -> 0 points, 5 ms -> 100 points).
THRESHOLDS = {
    "download": (0, 200),
    "upload": (0, 100),
    "latency": (500, 5),
    "jitter": (100, 0),
    "packet_loss": (100, 0),
}

# Real-world single-stream throughput ceiling (Mbps) per Wi-Fi generation.
# Lives here (not device_profile.py) so the server can compute the
# device-adjusted score without importing psutil.
WIFI_STANDARD_CEILINGS = {
    "802.11n": 150,     # Wi-Fi 4
    "802.11ac": 433,    # Wi-Fi 5
    "802.11ax": 600,    # Wi-Fi 6 / 6E (conservative single-stream figure)
    "802.11be": 1000,   # Wi-Fi 7
    "unknown": None,
}


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


def score_metric(value, lo, hi):
    """Linearly maps a raw measurement to 0-100: ((value - floor) / range) * 100."""
    if hi == lo:
        return 0
    ratio = (value - lo) / (hi - lo)
    return round(clamp(ratio * 100, 0, 100), 1)


def sub_scores(raw):
    """Per-metric 0-100 sub-scores for every metric that has a value (not None)."""
    return {m: score_metric(raw[m], *THRESHOLDS[m])
            for m in WEIGHTS if raw.get(m) is not None}


def weighted_total(subs):
    """Weighted sum over the metrics present, renormalised to the weights used."""
    used = sum(WEIGHTS[m] for m in subs)
    if not used:
        return None
    return round(sum(subs[m] * WEIGHTS[m] for m in subs) / used, 1)


def compute_score(raw):
    """raw: dict with download/upload/latency/jitter/packet_loss; None = not measured."""
    return weighted_total(sub_scores(raw))


def adjusted_download_score(download_mbps, wifi_standard, floor=0):
    """0-100 download sub-score relative to the device's Wi-Fi generation
    ceiling instead of the fixed 0-200 Mbps scale. None when the standard is
    unknown, so an adjustment is never fabricated."""
    ceiling = WIFI_STANDARD_CEILINGS.get(wifi_standard or "unknown")
    if not ceiling or download_mbps is None:
        return None
    ratio = (download_mbps - floor) / (ceiling - floor)
    return round(clamp(ratio * 100, 0, 100), 1)


def compute_adjusted_score(raw, wifi_standard):
    """Same as compute_score, but with the device-relative download sub-score."""
    adjusted_dl = adjusted_download_score(raw.get("download"), wifi_standard)
    if adjusted_dl is None:
        return None
    subs = sub_scores(raw)
    subs["download"] = adjusted_dl
    return weighted_total(subs)
