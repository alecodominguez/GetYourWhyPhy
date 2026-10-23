import hmac
import os
import subprocess

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime, timezone
from fastapi.staticfiles import StaticFiles
from locations import CAMPUS_BUILDINGS
from locations import get_standard_name

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
downloads_path = os.path.join(BASE_DIR, "downloads")
templates_path = os.path.join(BASE_DIR, "templates")
data_path = os.path.join(BASE_DIR, "data")

# Ensure the SQLite data directory exists before the engine tries to open a
# file inside it. It's empty, so it's never tracked in git - a fresh clone
# (like on the VPS) won't have it, and SQLite will refuse to create the .db
# file inside a directory that doesn't exist yet.
os.makedirs(data_path, exist_ok=True)

# Remote-admin auth token, the digital key to modify the server. Set as an
# environment variable on the server (e.g. in the systemd unit's
# Environment= line - see deploy/whyphy.service).
# NEVER hardcode a real token in source control.
ADMIN_TOKEN = os.environ.get("WHYPHY_ADMIN_TOKEN")


def _check_admin(x_admin_token):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin endpoints disabled: WHYPHY_ADMIN_TOKEN not set.")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


# Database Configuration (absolute path, so it doesn't depend on the
# working directory the service happens to be started from)
DATABASE_URL = f"sqlite:///{os.path.join(data_path, 'campus_wifi.db')}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Models
class WiFiLog(Base):
    __tablename__ = "wifi_logs"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String)
    ssid = Column(String)
    bssid = Column(String, nullable=True, index=True)  # automated mapping
    download = Column(Float)
    upload = Column(Float)
    latency = Column(Float)
    jitter = Column(Float)
    packet_loss = Column(Float)
    score = Column(Float)
    score_adjusted = Column(Float, nullable=True)  # device-relative score
    device_os = Column(String, nullable=True)       # device profiling
    device_wifi_standard = Column(String, nullable=True)
    source = Column(String, default="native")       # NEW: "native" or "web_lite"
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class BSSIDMapping(Base):
    """Crowdsourced BSSID -> building registry used for Automated Mapping."""
    __tablename__ = "bssid_mappings"
    bssid = Column(String, primary_key=True, index=True)
    building = Column(String)
    confirmations = Column(Integer, default=1)


class DropEvent(Base):
    """Connectivity-drop events reported by the passive monitor."""
    __tablename__ = "drop_events"
    id = Column(Integer, primary_key=True, index=True)
    building = Column(String)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    duration_seconds = Column(Float)
    reported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

# FastAPI App Initialization
app = FastAPI()
app.mount("/static", StaticFiles(directory=static_path), name="static")
app.mount("/downloads", StaticFiles(directory=downloads_path), name="downloads")
templates = Jinja2Templates(directory=templates_path)

CONFIRMATION_THRESHOLD = 3  # BSSID votes needed before auto-resolution is trusted


class WiFiData(BaseModel):
    location: str
    download: float
    upload: float
    latency: float
    # jitter/packet_loss/ssid default rather than being required, because the
    # WhyPhy Web Lite browser client has no OS-level access to ICMP or the
    # SSID. It approximates jitter/packet loss from fetch() round-trips.
    jitter: float = 0.0
    packet_loss: float = 0.0
    score: float
    ssid: str = "Unknown (Web Lite)"
    bssid: str | None = None
    score_adjusted: float | None = None
    device_os: str | None = None
    device_wifi_standard: str | None = None
    source: str = "native"  # "native" (WhyPhy.py) or "web_lite"


class BSSIDVote(BaseModel):
    bssid: str
    building: str


class DropEventIn(BaseModel):
    building: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float


@app.post("/log-wifi")
def log_wifi(data: WiFiData):
    standard_name = get_standard_name(data.location)
    if not standard_name:
        raise HTTPException(status_code=400, detail=f"'{data.location}' is not a recognized UofA building.")

    db = SessionLocal()
    try:
        log_entry_data = data.model_dump()
        log_entry_data["location"] = standard_name

        new_entry = WiFiLog(**log_entry_data)
        db.add(new_entry)

        # Automated Mapping: every successful, human-confirmed submission
        # that carries a BSSID is itself a vote for that BSSID's building.
        if data.bssid:
            _record_bssid_vote(db, data.bssid, standard_name)

        db.commit()
        return {"status": "success", "recorded_as": standard_name}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


def _record_bssid_vote(db, bssid, building):
    existing = db.query(BSSIDMapping).filter(BSSIDMapping.bssid == bssid).first()
    if existing:
        if existing.building == building:
            existing.confirmations += 1
        else:
            # Conflicting report: someone's BSSID moved, or was mislabeled.
            # Reset confidence rather than silently overwriting - a single
            # dissenting vote shouldn't flip an established mapping.
            existing.confirmations = max(1, existing.confirmations - 1)
            if existing.confirmations <= 1:
                existing.building = building
                existing.confirmations = 1
    else:
        db.add(BSSIDMapping(bssid=bssid, building=building, confirmations=1))


@app.get("/resolve-bssid/{bssid}")
def resolve_bssid(bssid: str):
    db = SessionLocal()
    try:
        mapping = db.query(BSSIDMapping).filter(BSSIDMapping.bssid == bssid.lower()).first()
        if mapping and mapping.confirmations >= CONFIRMATION_THRESHOLD:
            return {"confirmed": True, "building": mapping.building, "confirmations": mapping.confirmations}
        return {"confirmed": False}
    finally:
        db.close()


@app.post("/vote-bssid")
def vote_bssid(vote: BSSIDVote):
    standard_name = get_standard_name(vote.building)
    if not standard_name:
        raise HTTPException(status_code=400, detail="Unrecognized building.")
    db = SessionLocal()
    try:
        _record_bssid_vote(db, vote.bssid.lower(), standard_name)
        db.commit()
        return {"status": "recorded"}
    finally:
        db.close()


@app.get("/covers-building/{building}")
def covers_building(building: str):
    """
    Reverse lookup: given a building, list every BSSID currently associated
    with it. A room commonly has more than one access point (band-steering,
    enterprise Wi-Fi load-balancing clients across radios), so a building
    legitimately "covers" several BSSIDs.
    """
    standard_name = get_standard_name(building)
    if not standard_name:
        raise HTTPException(status_code=400, detail="Unrecognized building.")
    db = SessionLocal()
    try:
        mappings = db.query(BSSIDMapping).filter(BSSIDMapping.building == standard_name).all()
        return {
            "building": standard_name,
            "bssid_count": len(mappings),
            "bssids": [
                {
                    "bssid": m.bssid,
                    "confirmations": m.confirmations,
                    "confirmed": m.confirmations >= CONFIRMATION_THRESHOLD,
                }
                for m in mappings
            ],
        }
    finally:
        db.close()


@app.post("/admin/seed-bssid")
def admin_seed_bssid(vote: BSSIDVote, x_admin_token: str = Header(default=None)):
    """
    Bootstraps a BSSID -> building mapping straight to "trusted" without
    waiting on CONFIRMATION_THRESHOLD organic votes. Admin-gated because it
    can silently relabel a room going forward.
    """
    _check_admin(x_admin_token)
    standard_name = get_standard_name(vote.building)
    if not standard_name:
        raise HTTPException(status_code=400, detail="Unrecognized building.")
    db = SessionLocal()
    try:
        db.merge(BSSIDMapping(bssid=vote.bssid.lower(), building=standard_name,
                               confirmations=CONFIRMATION_THRESHOLD))
        db.commit()
        return {"status": "seeded", "bssid": vote.bssid.lower(), "building": standard_name}
    finally:
        db.close()


@app.post("/log-drop")
def log_drop(event: DropEventIn):
    standard_name = get_standard_name(event.building) or event.building
    db = SessionLocal()
    try:
        db.add(DropEvent(
            building=standard_name,
            started_at=event.started_at,
            ended_at=event.ended_at,
            duration_seconds=event.duration_seconds,
        ))
        db.commit()
        return {"status": "success"}
    finally:
        db.close()


@app.get("/lite", response_class=HTMLResponse)
def lite(request: Request):
    """WhyPhy Web Lite: a browser-only, zero-install throughput/latency check."""
    return templates.TemplateResponse(
        request=request,
        name="lite.html",
        context={"buildings": sorted(CAMPUS_BUILDINGS)},
    )


@app.get("/ping")
def ping():
    """Tiny no-cache endpoint Web Lite hits repeatedly to approximate
    latency/jitter via fetch() round-trip timing (browsers can't send ICMP)."""
    return Response(content=b"{}", media_type="application/json",
                    headers={"Cache-Control": "no-store"})


# 4 MB generated once at import time so the download test measures the
# network, not server-side random generation.
_SPEEDTEST_PAYLOAD = os.urandom(4 * 1024 * 1024)


@app.get("/speedtest-download")
def speedtest_download():
    return Response(content=_SPEEDTEST_PAYLOAD, media_type="application/octet-stream",
                    headers={"Cache-Control": "no-store"})


@app.post("/speedtest-upload")
async def speedtest_upload(request: Request):
    # Reads and discards the body; the client times the transfer itself.
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
    return {"received_bytes": size}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        logs = db.query(WiFiLog).order_by(desc(WiFiLog.timestamp)).limit(20).all()
        building_stats = db.query(
            WiFiLog.location,
            func.avg(WiFiLog.score).label('avg_score'),
            func.avg(WiFiLog.score_adjusted).label('avg_score_adjusted'),
            func.count(WiFiLog.location).label('test_count')
        ).group_by(WiFiLog.location).order_by(desc('avg_score')).all()

        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "logs": logs,
                "building_stats": building_stats,
                "buildings": sorted(CAMPUS_BUILDINGS)
            }
        )
    finally:
        db.close()


@app.get("/view-log")
def view_log():
    with open("debug_log.txt", "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Remote administration (see roadmap doc for the full security discussion).
# Deliberately narrow: report status and trigger a `systemctl restart`, and
# nothing else. No shell, no arbitrary commands, disabled unless
# WHYPHY_ADMIN_TOKEN is set. A convenience on top of SSH, not a replacement.
# ---------------------------------------------------------------------------
@app.get("/admin/status")
def admin_status(x_admin_token: str = Header(default=None)):
    _check_admin(x_admin_token)
    try:
        out = subprocess.check_output(
            ["systemctl", "is-active", "whyphy"], text=True, timeout=5
        ).strip()
    except subprocess.CalledProcessError as e:
        out = e.output.strip() if e.output else "unknown"
    return {"service": "whyphy", "state": out}


@app.post("/admin/restart")
def admin_restart(x_admin_token: str = Header(default=None)):
    _check_admin(x_admin_token)
    # Requires passwordless sudo scoped ONLY to `systemctl restart whyphy`
    # (see deploy/README.md).
    subprocess.Popen(["sudo", "/bin/systemctl", "restart", "whyphy"])
    return {"status": "restart_triggered"}
