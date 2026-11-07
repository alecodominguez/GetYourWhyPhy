"""
File: contributor.py
Purpose: An anonymous, per-install contributor ID.

"Three independent confirmations" of a BSSID only means something if the
database can tell contributors apart. This module creates a random ID the
first time any WhyPhy client runs and stores it in ~/.whyphy/contributor_id.

Privacy: the ID is random (uuid4). It is NOT derived from the hostname, MAC
address, serial number, or anything else about the machine, consistent with
device_profile.py's no-fingerprinting rule. Deleting the file simply makes
the next run a "new" contributor.
"""
import uuid
from pathlib import Path

WHYPHY_HOME = Path.home() / ".whyphy"
ID_FILE = WHYPHY_HOME / "contributor_id"


def get_contributor_id():
    try:
        existing = ID_FILE.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    new_id = uuid.uuid4().hex
    try:
        WHYPHY_HOME.mkdir(parents=True, exist_ok=True)
        ID_FILE.write_text(new_id + "\n")
    except OSError:
        pass  # read-only home: still return an ID for this run
    return new_id
