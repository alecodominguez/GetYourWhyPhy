from fastapi import FastAPI, Request, HTTPException
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
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
downloads_path = os.path.join(BASE_DIR, "downloads")
templates_path = os.path.join(BASE_DIR, "templates")

# Database Configuration
DATABASE_URL = "sqlite:///./data/campus_wifi.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Model
class WiFiLog(Base):
    __tablename__ = "wifi_logs"
    id = Column(Integer, primary_key=True, index=True)
    location = Column(String)
    ssid = Column(String)
    download = Column(Float)
    upload = Column(Float)
    latency = Column(Float)
    jitter = Column(Float)
    packet_loss = Column(Float)
    score = Column(Float)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

# FastAPI App Initialization
app = FastAPI()
app.mount("/static", StaticFiles(directory=static_path), name="static")
app.mount("/downloads", StaticFiles(directory=downloads_path), name="downloads")
templates = Jinja2Templates(directory=templates_path)


class WiFiData(BaseModel):
    location: str
    download: float
    upload: float
    latency: float
    jitter: float
    packet_loss: float
    score: float
    ssid: str

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
        db.commit()
        return {"status": "success", "recorded_as": standard_name}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
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