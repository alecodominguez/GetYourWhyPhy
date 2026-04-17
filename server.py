from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from datetime import datetime, timezone

# 1. Database Configuration
DATABASE_URL = "sqlite:///./campus_wifi.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. Database Model
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

# 3. FastAPI App Initialization
app = FastAPI()
templates = Jinja2Templates(directory="templates") # call to index.html

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
    db = SessionLocal()
    new_entry = WiFiLog(**data.dict())
    db.add(new_entry)
    db.commit()
    db.close()
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        # This query gets all logs, sorted by the newest first
        logs = db.query(WiFiLog).order_by(desc(WiFiLog.timestamp)).all()
        
        return templates.TemplateResponse("index.html", {"request": request, "logs": logs})
    finally:
        db.close()
