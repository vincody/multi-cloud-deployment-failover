"""Issues service entry point: runtime identity, health checks and static UI."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from app.issues import router as issues_router
from database.role import router as database_router

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
STARTED_AT = datetime.now(timezone.utc)
STARTED_MONOTONIC = time.monotonic()

APP_ENV = os.getenv("APP_ENV", "local")
APP_REGION = os.getenv("APP_REGION", "local-development")
APP_VERSION = os.getenv("APP_VERSION", "v0.1.0")


def _local_commit_sha() -> str:
    """Return the checked-out Git revision for local development only."""
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=APP_DIR.parent,
            capture_output=True,
            check=True,
            text=True,
            timeout=1,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=APP_DIR.parent,
            capture_output=True,
            check=False,
            timeout=1,
        ).returncode != 0
    except (OSError, subprocess.SubprocessError):
        return "not-built"

    return f"{revision}-dirty" if dirty else revision


COMMIT_SHA = os.getenv("COMMIT_SHA") or _local_commit_sha()

HTTP_REQUESTS = Counter(
    "dcs29_http_requests_total",
    "Total HTTP requests handled by the service.",
    ("method", "path", "status_code"),
)
HTTP_DURATION = Histogram(
    "dcs29_http_request_duration_seconds",
    "Time spent serving HTTP requests.",
    ("method", "path"),
)


def _status_payload() -> dict[str, Any]:
    return {
        "status": "healthy",
        "environment": APP_ENV,
        "region": APP_REGION,
        "version": APP_VERSION,
        "commit_sha": COMMIT_SHA,
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app = FastAPI(
    title="DCS29 Multi-Cloud Issues",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def instrument_requests(request: Request, call_next: Any) -> Response:
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - started
        HTTP_REQUESTS.labels(request.method, path, "500").inc()
        HTTP_DURATION.labels(request.method, path).observe(duration)
        raise

    duration = time.perf_counter() - started
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, path).observe(duration)
    response.headers["X-Served-By"] = APP_ENV
    response.headers["X-App-Version"] = APP_VERSION
    if path.startswith(("/api/", "/health/", "/version")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/api/status")
async def service_status() -> dict[str, Any]:
    return _status_payload()


@app.get("/version")
async def version() -> dict[str, Any]:
    return {
        key: value
        for key, value in _status_payload().items()
        if key in {"environment", "region", "version", "commit_sha", "timestamp"}
    }


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse("/issues.html")


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(issues_router)
app.include_router(database_router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
