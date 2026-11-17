#!/usr/bin/env python3
"""
HTTP API + web page for the eligibility engine.

    python api.py                      # http://127.0.0.1:8000  (this computer only)
    python api.py --lan                # also reachable from phones on the same Wi-Fi

Endpoints
    GET  /                 the web page
    GET  /api/status       last sync time and job counts
    GET  /api/jobs         open jobs (?all=true for every job)
    POST /api/match        body: a profile (see profile.example.json)
                           ?include_not_eligible=true to also get not eligible / closed jobs
"""
from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import threading
from datetime import date
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from eligibility import LEVELS, Profile, load_jobs, match

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SR_DB", os.path.join(HERE, "sarkari.db"))
WEB = os.path.join(HERE, "web", "index.html")

app = FastAPI(title="ExamMatch API", version="1.0")


# =========================================================================== jobs cache
class JobStore:
    """Reloads jobs only when the database changes (after a sync)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._stamp = None
        self._jobs: list[dict] = []

    def _stamp_now(self):
        stamps = []
        for p in (self.path, self.path + "-wal"):
            try:
                st = os.stat(p)
                stamps.append((st.st_mtime_ns, st.st_size))
            except OSError:
                stamps.append(None)
        return tuple(stamps)

    def jobs(self) -> list[dict]:
        if not os.path.exists(self.path):
            raise HTTPException(503, "No job database yet. Run sync.py (or run.bat) first.")
        with self._lock:
            stamp = self._stamp_now()
            if stamp != self._stamp:
                self._jobs = load_jobs(self.path)
                self._stamp = stamp
            return self._jobs


store = JobStore(DB_PATH)


def last_sync() -> str | None:
    try:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = db.execute("SELECT value FROM meta WHERE key='last_success'").fetchone()
        db.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


# =========================================================================== schemas
class Education(BaseModel):
    level: Literal["8th", "10th", "12th", "diploma", "graduate", "postgraduate", "phd"]
    degree: Optional[str] = Field(None, max_length=100)
    stream: Optional[str] = Field(None, max_length=100)
    branch: Optional[str] = Field(None, max_length=100)
    subjects: list[str] = Field(default_factory=list, max_length=20)
    percent: Optional[float] = Field(None, ge=0, le=100)


class Pursuing(BaseModel):
    level: Literal["10th", "12th", "diploma", "graduate", "postgraduate", "phd"]
    degree: Optional[str] = Field(None, max_length=100)
    stream: Optional[str] = Field(None, max_length=100)
    branch: Optional[str] = Field(None, max_length=100)
    final_year: bool = False


class ProfileIn(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    dob: date
    gender: Literal["male", "female", "other"]
    category: Literal["GEN", "EWS", "OBC", "SC", "ST"]
    pwd: bool = False
    ex_serviceman: bool = False
    domicile: Optional[str] = Field(None, max_length=3)
    education: list[Education] = Field(..., min_length=1, max_length=10)
    pursuing: Optional[Pursuing] = None
    certificates: list[str] = Field(default_factory=list, max_length=30)
    certificates_complete: bool = True
    experience_years: float = Field(0, ge=0, le=60)

    @field_validator("dob")
    @classmethod
    def plausible_dob(cls, v: date) -> date:
        if not (date(1940, 1, 1) <= v <= date.today()):
            raise ValueError("date of birth looks wrong")
        return v

    @field_validator("domicile")
    @classmethod
    def upper_state(cls, v):
        return v.upper() if v else v


# =========================================================================== routes
@app.get("/", include_in_schema=False)
def index():
    if not os.path.exists(WEB):
        raise HTTPException(404, "web/index.html missing")
    return FileResponse(WEB, media_type="text/html")


@app.get("/api/status")
def status() -> dict[str, Any]:
    jobs = store.jobs()
    today = date.today().isoformat()
    open_jobs = sum(1 for j in jobs if not (j.get("dates") or {}).get("last_date")
                    or j["dates"]["last_date"] >= today)
    return {"last_sync": last_sync(), "jobs": len(jobs), "open_jobs": open_jobs}


@app.get("/api/jobs")
def jobs(all: bool = Query(False, description="include jobs whose last date has passed")):
    today = date.today().isoformat()
    out = []
    for j in store.jobs():
        last = (j.get("dates") or {}).get("last_date")
        if not all and last and last < today:
            continue
        out.append({"title": j["title"], "link": j["link"], "last_date": last,
                    "dates": j.get("dates"), "age": j.get("age"), "fees": j.get("fees"),
                    "total_posts": j.get("total_posts"), "links": j.get("links"),
                    "qualification_text": j.get("qualification_text")})
    out.sort(key=lambda x: x["last_date"] or "9999")
    return {"count": len(out), "jobs": out}


@app.post("/api/match")
def match_profile(body: ProfileIn, include_not_eligible: bool = False):
    data = body.model_dump(mode="json")
    try:
        profile = Profile.load(data)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc))
    today = date.today()
    results = match(profile, store.jobs(), today)
    counts = {s: sum(1 for r in results if r["status"] == s)
              for s in ("eligible", "check_manually", "not_eligible", "closed")}
    if not include_not_eligible:
        results = [r for r in results if r["status"] in ("eligible", "check_manually")]
    for r in results:
        r["days_left"] = (date.fromisoformat(r["last_date"]) - today).days if r["last_date"] else None
    return {"last_sync": last_sync(), "counts": counts, "results": results}


@app.exception_handler(ValueError)
def value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


# =========================================================================== run
def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "your-computer-ip"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Run the ExamMatch web page and API.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="allow phones on the same Wi-Fi to connect")
    args = ap.parse_args()
    import uvicorn
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"\n  Open on this computer: http://127.0.0.1:{args.port}")
    if args.lan:
        print(f"  Open on a phone (same Wi-Fi): http://{lan_ip()}:{args.port}")
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
