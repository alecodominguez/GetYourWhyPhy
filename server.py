import hmac
import os
import subprocess
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (create_engine, event, text, Column, Integer, String, Float, Boolean,
                        DateTime, ForeignKey, Index, TypeDecorator, desc, func)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import declarative_base, sessionmaker

import migrations
import scoring
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
# working directory the service happens to be started from).
# WHYPHY_DB_PATH lets tests point the app at a throwaway database.
DB_PATH = os.environ.get("WHYPHY_DB_PATH", os.path.join(data_path, "campus_wifi.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    # SQLite ignores REFERENCES clauses unless this is switched on per connection.
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UTCDateTime(TypeDecorator):
    """SQLite has no timezone-aware datetime type, and SQLAlchemy's DateTime
    silently drops tzinfo. That meant .isoformat() produced e.g.
    "2026-11-07T23:00:00" with no "Z", which browsers parse as LOCAL time, so
    in Arizona every report looked 7 hours younger than it was.

    Convention: the database stores UTC wall-clock time (same on-disk format as
    before, so SQL strftime() still works); this type converts to UTC on the
    way in and re-attaches UTC on the way out."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value  # naive values are assumed to already be UTC

    def process_result_value(self, value, dialect):
        if value is not None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Database Models
# ---------------------------------------------------------------------------
class Contributor(Base):
    """One anonymous WhyPhy install (random ID from contributor.py, or a
    per-browser random ID for Web Lite). Exists so "N independent
    confirmations" can mean N different contributors."""
    __tablename__ = "contributors"
    id = Column(String, primary_key=True)
    first_seen = Column(UTCDateTime, default=_utcnow)
    last_seen = Column(UTCDateTime, default=_utcnow)


class WiFiLog(Base):
    __tablename__ = "wifi_logs"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String)
    ssid = Column(String)
    bssid = Column(String, nullable=True, index=True)  # automated mapping
    download = Column(Float)
    upload = Column(Float)
    latency = Column(Float)
    jitter = Column(Float, nullable=True)
    packet_loss = Column(Float, nullable=True)
    # Validity flags: False = the raw value is known-bad (e.g. jitter = 0.0 from
    # a client with the old ping-parsing bug) and is excluded from `score`.
    jitter_valid = Column(Boolean, nullable=True)
    packet_loss_valid = Column(Boolean, nullable=True)
    score = Column(Float)                            # derived: recomputed by the server
    client_score = Column(Float, nullable=True)      # what the client claimed (audit only)
    score_adjusted = Column(Float, nullable=True)    # derived: device-relative score
    device_os = Column(String, nullable=True)        # device profiling
    device_wifi_standard = Column(String, nullable=True)
    source = Column(String, default="native")        # "native", "web_lite" or "passive"
    contributor_id = Column(String, ForeignKey("contributors.id"), nullable=True, index=True)
    client_version = Column(Integer, nullable=True)
    timestamp = Column(UTCDateTime, default=_utcnow)

    # Leaderboard + time-of-day queries filter/group by building, then time.
    __table_args__ = (Index("ix_wifi_logs_location_timestamp", "location", "timestamp"),)


class BSSIDVote(Base):
    """The M:N relationship between Contributor and access point (BSSID), with
    the building as an attribute of each vote. The composite primary key means
    the DATABASE refuses a second vote from the same contributor for the same
    BSSID; a re-vote replaces that contributor's earlier answer.

    `confirmations` is no longer stored anywhere: it's
        SELECT building, COUNT(*) FROM bssid_votes WHERE bssid = ? GROUP BY building
    so there's no counter to double-increment or lose an update on."""
    __tablename__ = "bssid_votes"
    contributor_id = Column(String, ForeignKey("contributors.id"), primary_key=True)
    bssid = Column(String, primary_key=True, index=True)
    building = Column(String, nullable=False)
    voted_at = Column(UTCDateTime, default=_utcnow)


class BSSIDSeed(Base):
    """Admin-asserted BSSID -> building mappings (POST /admin/seed-bssid).
    Kept separate from votes so a seed is never confused with crowd evidence."""
    __tablename__ = "bssid_seeds"
    bssid = Column(String, primary_key=True)
    building = Column(String, nullable=False)
    seeded_at = Column(UTCDateTime, default=_utcnow)


class DropEvent(Base):
    """Connectivity-drop events reported by the passive monitor."""
    __tablename__ = "drop_events"
    id = Column(Integer, primary_key=True, index=True)
    building = Column(String)
    contributor_id = Column(String, ForeignKey("contributors.id"), nullable=True)
    started_at = Column(UTCDateTime)
    ended_at = Column(UTCDateTime)
    duration_seconds = Column(Float)
    reported_at = Column(UTCDateTime, default=_utcnow)

    # Natural key: the monitor retries until it sees a 200, so the same event
    # can arrive twice (POST succeeded, response timed out). The database
    # rejects the duplicate instead of storing it twice.
    __table_args__ = (Index("uq_drop_events_building_started", "building", "started_at", unique=True),)


Base.metadata.create_all(bind=engine)   # creates any tables that don't exist yet
migrations.upgrade(DB_PATH)             # alters/backfills tables that already did

# FastAPI App Initialization
app = FastAPI()
app.mount("/static", StaticFiles(directory=static_path), name="static")
app.mount("/downloads", StaticFiles(directory=downloads_path, check_dir=False), name="downloads")
templates = Jinja2Templates(directory=templates_path)


def _utc_iso(dt):
    """Unambiguous UTC ISO-8601 ("...Z") for the browser's relative-time code."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


templates.env.filters["utc_iso"] = _utc_iso

CONFIRMATION_THRESHOLD = 3  # distinct contributors needed before auto-resolution is trusted
CAMPUS_UTC_OFFSET = "-7 hours"  # Arizona doesn't observe DST, so this is fixed year-round

# speedtest-cli's get_best_server() records 3600 s for every latency probe that
# fails, then averages 3 probes over 6: (3 * 3600 / 6) * 1000 = 1,800,000.0 ms.
# So 1800000.0 isn't a measurement, it's "no speedtest server answered".
# Anything above MAX_PLAUSIBLE_LATENCY_MS is rejected so older clients that
# don't filter it themselves can't poison the averages.
SPEEDTEST_FAILED_LATENCY_MS = 1_800_000.0
MAX_PLAUSIBLE_LATENCY_MS = 10_000.0

def _contributor_field():
    # Random per-install ID (contributor.py) or per-browser ID (lite.js).
    return Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class WiFiData(BaseModel):
    location: str
    download: float = Field(ge=0)
    upload: float = Field(ge=0)
    latency: float
    # None = "couldn't be measured". Web Lite approximates jitter/packet loss
    # from fetch() round-trips (browsers can't send ICMP).
    jitter: float | None = Field(default=None, ge=0)
    packet_loss: float | None = Field(default=None, ge=0, le=100)
    # The client's own score is accepted for backwards compatibility and kept
    # for auditing, but the stored score is recomputed from the raw metrics.
    score: float | None = None
    ssid: str = "Unknown (Web Lite)"
    bssid: str | None = None
    score_adjusted: float | None = None   # ignored: recomputed server-side
    device_os: str | None = None
    device_wifi_standard: str | None = None
    source: Literal["native", "web_lite", "passive"] = "native"
    contributor_id: str | None = _contributor_field()
    client_version: int | None = None     # None = pre-Nov-2026 client (buggy jitter/loss parsing)

    @field_validator("latency")
    @classmethod
    def latency_must_be_real(cls, v):
        if v <= 0 or v >= MAX_PLAUSIBLE_LATENCY_MS:
            raise ValueError(
                f"latency {v} ms is not a real measurement "
                f"(speedtest-cli reports {SPEEDTEST_FAILED_LATENCY_MS} ms when every latency probe fails)")
        return v

    @field_validator("bssid")
    @classmethod
    def normalise_bssid(cls, v):
        return v.strip().lower() if v else None


class BSSIDVoteIn(BaseModel):
    bssid: str
    building: str
    contributor_id: str | None = _contributor_field()


class DropEventIn(BaseModel):
    building: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float = Field(ge=0)
    contributor_id: str | None = _contributor_field()

    @model_validator(mode="after")
    def ended_after_started(self):
        if _as_utc(self.ended_at) < _as_utc(self.started_at):
            raise ValueError("ended_at is before started_at")
        return self


def _as_utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def assess_validity(data):
    """Which raw metrics can be trusted. Pre-Nov-2026 native clients
    (client_version is None) had two parsing bugs: jitter always came out 0.0,
    and on macOS/Linux packet loss came out as the ping COUNT (20.0)."""
    legacy_native = data.client_version is None and data.source != "web_lite"
    jitter_valid = data.jitter is not None and not (legacy_native and data.jitter == 0)
    packet_loss_valid = data.packet_loss is not None and not (
        legacy_native and data.packet_loss == 20.0 and (data.device_os or "") != "Windows")
    return jitter_valid, packet_loss_valid


def _touch_contributor(db, contributor_id):
    if not contributor_id:
        return
    now = _utcnow()
    db.execute(sqlite_insert(Contributor)
               .values(id=contributor_id, first_seen=now, last_seen=now)
               .on_conflict_do_update(index_elements=["id"], set_={"last_seen": now}))


def _record_bssid_vote(db, contributor_id, bssid, building):
    """One vote per (contributor, BSSID): INSERT, or replace that contributor's
    earlier answer. A single atomic statement, so concurrent requests can't
    lose each other's updates the way the old read-increment-write could."""
    db.execute(sqlite_insert(BSSIDVote)
               .values(contributor_id=contributor_id, bssid=bssid, building=building, voted_at=_utcnow())
               .on_conflict_do_update(index_elements=["contributor_id", "bssid"],
                                      set_={"building": building, "voted_at": _utcnow()}))


@app.post("/log-wifi")
def log_wifi(data: WiFiData):
    standard_name = get_standard_name(data.location)
    if not standard_name:
        raise HTTPException(status_code=400, detail=f"'{data.location}' is not a recognized UofA building.")

    jitter_valid, packet_loss_valid = assess_validity(data)
    raw = {
        "download": data.download, "upload": data.upload, "latency": data.latency,
        "jitter": data.jitter if jitter_valid else None,
        "packet_loss": data.packet_loss if packet_loss_valid else None,
    }

    db = SessionLocal()
    try:
        _touch_contributor(db, data.contributor_id)
        db.add(WiFiLog(
            location=standard_name, ssid=data.ssid, bssid=data.bssid,
            download=data.download, upload=data.upload, latency=data.latency,
            jitter=data.jitter, packet_loss=data.packet_loss,
            jitter_valid=jitter_valid, packet_loss_valid=packet_loss_valid,
            score=scoring.compute_score(raw),
            client_score=data.score,
            score_adjusted=scoring.compute_adjusted_score(raw, data.device_wifi_standard),
            device_os=data.device_os, device_wifi_standard=data.device_wifi_standard,
            source=data.source, contributor_id=data.contributor_id,
            client_version=data.client_version,
        ))

        # Automated Mapping: a human-initiated run that carries a BSSID is that
        # contributor's vote for the building they confirmed. Passive-monitor
        # runs don't vote (nobody confirmed the building that run), and
        # anonymous legacy clients can't vote (nothing to deduplicate on).
        voted = bool(data.bssid and data.contributor_id and data.source != "passive")
        if voted:
            _record_bssid_vote(db, data.contributor_id, data.bssid, standard_name)

        db.commit()
        return {"status": "success", "recorded_as": standard_name, "bssid_vote_recorded": voted}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


def _resolve(db, bssid):
    """Derives a BSSID's building from the evidence instead of reading a
    stored counter. An admin seed wins; otherwise the building with the most
    distinct-contributor votes, if it has >= CONFIRMATION_THRESHOLD of them
    and strictly beats the runner-up."""
    seed = db.get(BSSIDSeed, bssid)
    tallies = db.query(BSSIDVote.building, func.count().label("votes")) \
        .filter(BSSIDVote.bssid == bssid) \
        .group_by(BSSIDVote.building).order_by(desc("votes")).all()
    top_building, top_votes = (tallies[0] if tallies else (None, 0))
    runner_up = tallies[1][1] if len(tallies) > 1 else 0
    total = sum(t[1] for t in tallies)
    if seed:
        return {"confirmed": True, "building": seed.building, "via": "admin_seed",
                "confirmations": top_votes if top_building == seed.building else 0, "total_votes": total}
    confirmed = top_votes >= CONFIRMATION_THRESHOLD and top_votes > runner_up
    return {"confirmed": confirmed, "building": top_building, "via": "votes" if confirmed else None,
            "confirmations": top_votes, "total_votes": total}


@app.get("/resolve-bssid/{bssid}")
def resolve_bssid(bssid: str):
    db = SessionLocal()
    try:
        r = _resolve(db, bssid.strip().lower())
        if r["confirmed"]:
            return {"confirmed": True, "building": r["building"], "confirmations": r["confirmations"]}
        return {"confirmed": False}
    finally:
        db.close()


@app.post("/vote-bssid")
def vote_bssid(vote: BSSIDVoteIn):
    """Stand-alone vote, used by the passive monitor's one-time building
    prompt. Pre-Nov-2026 WhyPhy.py also called this right after /log-wifi
    (without a contributor_id), which double-counted every run; those calls
    are now acknowledged but ignored."""
    standard_name = get_standard_name(vote.building)
    if not standard_name:
        raise HTTPException(status_code=400, detail="Unrecognized building.")
    if not vote.contributor_id:
        return {"status": "ignored", "reason": "votes need a contributor_id (update your WhyPhy client)"}
    db = SessionLocal()
    try:
        _touch_contributor(db, vote.contributor_id)
        _record_bssid_vote(db, vote.contributor_id, vote.bssid.strip().lower(), standard_name)
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
        candidates = {b for (b,) in db.query(BSSIDVote.bssid).filter(BSSIDVote.building == standard_name).distinct()}
        candidates |= {b for (b,) in db.query(BSSIDSeed.bssid).filter(BSSIDSeed.building == standard_name)}
        rows = []
        for bssid in sorted(candidates):
            r = _resolve(db, bssid)
            if r["building"] == standard_name:
                rows.append({"bssid": bssid, "confirmations": r["confirmations"],
                             "total_votes": r["total_votes"], "confirmed": r["confirmed"]})
        return {"building": standard_name, "bssid_count": len(rows), "bssids": rows}
    finally:
        db.close()


@app.post("/admin/seed-bssid")
def admin_seed_bssid(vote: BSSIDVoteIn, x_admin_token: str = Header(default=None)):
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
        db.merge(BSSIDSeed(bssid=vote.bssid.strip().lower(), building=standard_name, seeded_at=_utcnow()))
        db.commit()
        return {"status": "seeded", "bssid": vote.bssid.strip().lower(), "building": standard_name}
    finally:
        db.close()


@app.post("/log-drop")
def log_drop(event: DropEventIn):
    # Used to fall back to storing whatever string was sent; now a drop must
    # belong to a real building, same as a benchmark.
    standard_name = get_standard_name(event.building)
    if not standard_name:
        raise HTTPException(status_code=400, detail=f"'{event.building}' is not a recognized UofA building.")
    db = SessionLocal()
    try:
        _touch_contributor(db, event.contributor_id)
        result = db.execute(
            sqlite_insert(DropEvent).values(
                building=standard_name, contributor_id=event.contributor_id,
                started_at=_as_utc(event.started_at), ended_at=_as_utc(event.ended_at),
                duration_seconds=event.duration_seconds, reported_at=_utcnow(),
            ).on_conflict_do_nothing(index_elements=["building", "started_at"]))
        db.commit()
        # 200 either way so the client stops retrying; "duplicate" means we
        # already had it (a retry after a lost response).
        return {"status": "success" if result.rowcount else "duplicate"}
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


# ---------------------------------------------------------------------------
# Aggregation queries
#
# 1. Native and Web Lite measure different paths (nearest speedtest.net
#    server vs. HTTPS round trips to whyphy.app), so they are never averaged
#    together: we group by measurement METHOD as well as building. Passive
#    monitor runs use the native pipeline, so they share its method.
# 2. Two-step (nested) aggregation: average per contributor first, then
#    across contributors, so one passive monitor (~48 runs/day) or one
#    enthusiastic tester counts once. In relational algebra:
#      γ location,method; AVG(a) ( γ location,method,who; AVG(score)→a (WiFiLog) )
#    Rows from anonymous legacy clients have no contributor_id, so each such
#    row counts as its own contributor ('row:<id>').
# ---------------------------------------------------------------------------
_METHOD = "CASE WHEN source = 'web_lite' THEN 'web_lite' ELSE 'native' END"
_WHO = "COALESCE(contributor_id, 'row:' || id)"

LEADERBOARD_SQL = text(f"""
    WITH per_contributor AS (
        SELECT location, {_METHOD} AS method, {_WHO} AS who,
               AVG(score) AS a, AVG(score_adjusted) AS a_adj, COUNT(*) AS n
        FROM wifi_logs
        WHERE score IS NOT NULL
        GROUP BY location, method, who
    )
    SELECT location, method,
           AVG(a) AS avg_score, AVG(a_adj) AS avg_score_adjusted,
           SUM(n) AS test_count, COUNT(*) AS contributor_count
    FROM per_contributor
    GROUP BY location, method
    ORDER BY method, avg_score DESC
""")

HOURLY_SQL = text(f"""
    WITH per_contributor AS (
        SELECT location, {_METHOD} AS method,
               CAST(strftime('%H', timestamp, '{CAMPUS_UTC_OFFSET}') AS INTEGER) AS hour,
               {_WHO} AS who, AVG(score) AS a, COUNT(*) AS n
        FROM wifi_logs
        WHERE score IS NOT NULL AND location = :building
        GROUP BY location, method, hour, who
    )
    SELECT method, hour, ROUND(AVG(a), 1) AS avg_score,
           SUM(n) AS test_count, COUNT(*) AS contributor_count
    FROM per_contributor
    GROUP BY method, hour
    ORDER BY method, hour
""")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        logs = db.query(WiFiLog).order_by(desc(WiFiLog.timestamp)).limit(20).all()
        stats = [dict(r._mapping) for r in db.execute(LEADERBOARD_SQL)]
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "logs": logs,
                "native_stats": [s for s in stats if s["method"] == "native"],
                "lite_stats": [s for s in stats if s["method"] == "web_lite"],
                "buildings": sorted(CAMPUS_BUILDINGS)
            }
        )
    finally:
        db.close()


@app.get("/api/hourly/{building}")
def hourly(building: str):
    """Average score by hour of day (Arizona time) for one building - the
    "categorized by building and time of day" view. Fast because of the
    composite (location, timestamp) index."""
    standard_name = get_standard_name(building)
    if not standard_name:
        raise HTTPException(status_code=400, detail="Unrecognized building.")
    db = SessionLocal()
    try:
        rows = [dict(r._mapping) for r in db.execute(HOURLY_SQL, {"building": standard_name})]
        return {"building": standard_name, "timezone": "America/Phoenix", "hours": rows}
    finally:
        db.close()


@app.get("/api/data-quality")
def data_quality():
    """How much of the stored data is flagged, by source - handy for the
    showcase and for checking the effect of the Nov 2026 fixes."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT source,
                   COUNT(*) AS rows,
                   SUM(CASE WHEN jitter_valid = 0 THEN 1 ELSE 0 END) AS jitter_flagged,
                   SUM(CASE WHEN packet_loss_valid = 0 THEN 1 ELSE 0 END) AS packet_loss_flagged,
                   SUM(CASE WHEN contributor_id IS NULL THEN 1 ELSE 0 END) AS anonymous_legacy_rows
            FROM wifi_logs GROUP BY source ORDER BY source
        """))
        return {"by_source": [dict(r._mapping) for r in rows]}
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
