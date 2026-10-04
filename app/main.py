"""
Return Pulse API: serves Neha's dashboard (frontend/) and the five endpoints it calls.
The JSON shapes are in frontend/API_CONTRACT.md.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from . import analytics
from . import schemas as S
from .db import Database, DatabaseError

log = logging.getLogger("return_pulse")
ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="Return Pulse API",
    version="1.0.0",
    description="Powers the Dhaga & Co. returns dashboard (Neha, Category Head). "
                "All data is synthetic. Every count covers the latest twelve whole months in the data.",
    docs_url="/api/docs", openapi_url="/api/openapi.json",
)
DOWN = {503: {"model": S.ErrorOut, "description": "Database unreachable or empty. `detail` is a sentence to show on screen."}}
db = Database()

# "synthetic" while the database holds the generated data; set DATA_LABEL=real for real data.
DATA_LABEL = os.environ.get("DATA_LABEL", "synthetic")


@app.middleware("http")
async def revalidate(request: Request, call_next):
    """Browsers must check for a newer copy every time, so a deploy shows up on a normal reload.
    Unchanged files still come back as 304 Not Modified."""
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(DatabaseError)
async def database_error(_: Request, exc: DatabaseError):
    log.exception("database error")
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Labelled rows, recomputed only when the pipeline writes new labels.
# ---------------------------------------------------------------------------
_cache = {"version": None, "value": None}
_lock = threading.Lock()


def state():
    version = db.classified_version()
    with _lock:
        if _cache["version"] == version and _cache["value"] is not None:
            return _cache["value"]
        settings = db.settings()
        rows, label_source = analytics.label_rows(db.enriched(), db.classified(), settings)
        if not rows:
            raise DatabaseError("The database has no returns yet. Load the data, then reload this page.")
        wins = analytics.windows(max(r["return_date"] for r in rows))
        current = [r for r in rows if analytics.in_window(r, wins["this"])]
        value = {
            "settings": settings,
            "label_source": label_source,
            "rows": rows,
            "current": current,
            "wins": wins,
            "summary": analytics.summarize(current, wins["this"], settings),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        _cache.update(version=version, value=value)
        return value


def meta(s):
    stub = s["label_source"] == "sample_stub"
    return {
        "data_mode": "sample" if stub else "live",
        "synthetic": DATA_LABEL != "real",
        "generated_at": s["generated_at"],
        "window": {"from": s["wins"]["this"][0].isoformat(), "to": s["wins"]["this"][1].isoformat(),
                   "label": s["wins"]["this"][2]},
        "confidence_threshold": s["settings"]["confidence_threshold"],
        "min_returns_per_hotspot": s["settings"]["min_returns_per_hotspot"],
        "classifier_note": ("The AI reading hasn't run yet, so 'Other' comments are sorted by a simple keyword match. Expect more mistakes than the final version.") if stub else None,
        "corrections_storage": "permanent" if db.corrections_permanent else "temporary",
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def build_sha():
    """The deploy workflow writes build_sha.txt; locally there isn't one."""
    f = ROOT / "build_sha.txt"
    return f.read_text().strip() if f.exists() else os.environ.get("BUILD_SHA", "local")


@app.get("/health", response_model=S.Health, tags=["operations"],
         responses={503: {"model": S.HealthDown, "description": "The data can't be served"}})
def health():
    """For the deploy check and the keep-alive ping. 200 only when the data can be served."""
    try:
        counts = db.table_counts()
        s = state()
    except DatabaseError as e:
        return JSONResponse(status_code=503, content={"status": "down", "database": db.describe(), "error": str(e)})
    return {
        "status": "ok",
        "database": db.describe(),
        "tables": counts,
        "labels_from": s["label_source"],
        "corrections_storage": "permanent" if db.corrections_permanent else "temporary",
        "build": build_sha(),
    }


@app.get("/api/summary", response_model=S.Summary, tags=["dashboard"], responses=DOWN)
def summary():
    """Everything above the trend chart: headline numbers, return drivers, problem spots and locations."""
    s = state()
    return {"meta": meta(s), **s["summary"]}


@app.get("/api/trend", response_model=S.Trend, tags=["dashboard"],
         responses={400: {"model": S.ErrorOut, "description": "Unknown issue"}, **DOWN})
def trend(issue: str = Query("too_small", description="An issue type, e.g. `too_small`")):
    """Monthly returns for one issue: the latest twelve months against the twelve before."""
    if issue not in analytics.ISSUES:
        raise HTTPException(400, f"Unknown issue '{issue}'. Expected one of: {', '.join(analytics.ISSUES)}")
    s = state()
    return analytics.trend(s["rows"], s["wins"], issue)


@app.get("/api/returns", response_model=S.ReturnsPage, tags=["dashboard"],
         responses={400: {"model": S.ErrorOut, "description": "Unknown issue_type"}, **DOWN})
def returns(vendor_id: Optional[str] = Query(None, examples=["V07"]),
            subcategory: Optional[str] = Query(None, examples=["Kurti"]),
            city: Optional[str] = Query(None, examples=["Guwahati"]),
            issue_type: Optional[str] = Query(None, description="Includes `unclear` and `failed`")):
    """The returns behind a problem spot, a city, or the unclear/failed lists. Filters combine with AND; all optional."""
    if issue_type and issue_type not in analytics.ISSUES:
        raise HTTPException(400, f"Unknown issue_type '{issue_type}'")
    rows = analytics.filter_returns(state()["current"], {
        "vendor_id": vendor_id, "subcategory": subcategory, "city": city, "issue_type": issue_type})
    return {"total": len(rows), "returns": rows}


def _accuracy(marks):
    return {"reviewed": len(marks), "correct": sum(1 for m in marks.values() if m["is_correct"])}


@app.get("/api/corrections", response_model=S.Corrections, tags=["corrections"], responses=DOWN)
def corrections():
    """Neha's ✓ / ✗ marks (latest per return) and the accuracy they add up to."""
    marks = db.corrections()
    return {"corrections": marks, "accuracy": _accuracy(marks)}


@app.post("/api/corrections", response_model=S.CorrectionSaved, tags=["corrections"],
          responses={400: {"model": S.ErrorOut, "description": "Marked correct but also given a corrected issue"},
                     404: {"model": S.ErrorOut, "description": "No such return_id"},
                     422: {"model": S.ErrorOut, "description": "Body doesn't match the schema"}, **DOWN})
def add_correction(c: S.CorrectionIn):
    """Save one ✓ / ✗ mark. Sending the same return_id again replaces the earlier mark."""
    if not db.return_exists(c.return_id):
        raise HTTPException(404, f"No return with id {c.return_id}")
    if c.is_correct and c.corrected_issue:
        raise HTTPException(400, "A label marked correct can't also have a corrected issue")
    db.add_correction(c.model_dump())
    marks = db.corrections()
    return {"ok": True, "accuracy": _accuracy(marks), "storage": "permanent" if db.corrections_permanent else "temporary"}


# The dashboard itself. Mounted last so /api and /health win.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
