import hmac
import os
import subprocess

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func  # import to get building longitudinal data
from pydantic import BaseModel
from datetime import datetime, timezone
from fastapi.staticfiles import StaticFiles  # Allows to add download button to index.html
from locations import CAMPUS_BUILDINGS  # getter to foreard the info to the html
from locations import get_standard_name #  function call to prevent non-campus data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
downloads_path = os.path.join(BASE_DIR, "downloads")
templates_path = os.path.join(BASE_DIR, "templates")

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

# Database Configuration
DATABASE_URL = "sqlite:///./data/campus_wifi.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class WiFiLog(Base):
    __tablename__ = "wifi_logs"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String)
    ssid = Column(String)
    bssid = Column(String, nullable=True, index=True)  # new bssid for automated mapping
    download = Column(Float)
    upload = Column(Float)
    latency = Column(Float)
    jitter = Column(Float)
    packet_loss = Column(Float)
    score = Column(Float)
    score_adjusted = Column(Float, nullable=True)  # new device-relative score
    device_os = Column(String, nullable=True)       # new device profiling
    device_wifi_standard = Column(String, nullable=True)
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
    jitter: float
    packet_loss: float
    score: float
    ssid: str
    bssid: str | None = None
    score_adjusted: float | None = None
    device_os: str | None = None
    device_wifi_standard: str | None = None

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
    # 1. Normalize and Validate the location
    standard_name = get_standard_name(data.location)

    if not standard_name:
        raise HTTPException(status_code=400, detail=f"'{data.location}' is not a recognized UofA building.")

    db = SessionLocal()
    try:
        # 2. Create the entry, but explicitly OVERWRITE the location with the standard name
        log_entry_data = data.model_dump()
        log_entry_data["location"] = standard_name

        new_entry = WiFiLog(**log_entry_data)
        db.add(new_entry)

        # Automated Mapping: every human-confirmed submission
        # that carries a BSSID is a vote towards the CONFIRMATION_THRESHOLD
        # of 3 for that BSSID's building.
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
    """Function to add a vote of confirmation towards a specific bssid mapped location"""
    existing = db.query(BSSIDMapping).filter(BSSIDMapping.bssid == bssid).first()
    if existing:
        if existing.building == building:
            existing.confirmations += 1
        else:
            # ERROR: someone's BSSID moved, or was mislabeled.
            # Reset confidence rather than silently overwriting
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

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        # Gets the last 20 raw logs for the "Recent Activity" table
        logs = db.query(WiFiLog).order_by(desc(WiFiLog.timestamp)).limit(20).all()

        # Calculate averages for the "Building Leaderboard"
        # Group by 'location' and average the 'score'
        building_stats = db.query(
            WiFiLog.location,
            func.avg(WiFiLog.score).label('avg_score'),
            func.count(WiFiLog.location).label('test_count')
        ).group_by(WiFiLog.location).order_by(desc('avg_score')).all()

        return templates.TemplateResponse(
            request=request,  # Pass request as its own argument
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

# Remote administration
# These are DELIBERATELY narrow: they can report status and trigger a
# `systemctl restart`, and nothing else. They do NOT expose a shell, do NOT
# accept arbitrary commands, and are disabled unless WHYPHY_ADMIN_TOKEN is
# set. Treat this as a convenience on top of SSH access, not a replacement
# for it.
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
    # Requires the deploy user to have passwordless sudo scoped ONLY to
    # `systemctl restart whyphy` - see deploy/README.md. Restarting via
    # systemd (not os.execv or similar) means a bad restart can't wedge
    # the box with no supervisor left to bring it back.
    subprocess.Popen(["sudo", "/bin/systemctl", "restart", "whyphy"])
    return {"status": "restart_triggered"}
