"""Runtime status dashboard used to demonstrate multi-cloud failover."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

APP_DIR = Path(__file__).resolve().parent
DATASET_PATH = APP_DIR / "data" / "devices.json"
STATIC_DIR = APP_DIR / "static"
STARTED_AT = datetime.now(timezone.utc)
STARTED_MONOTONIC = time.monotonic()

APP_ENV = os.getenv("APP_ENV", "local")
APP_REGION = os.getenv("APP_REGION", "local-development")
APP_VERSION = os.getenv("APP_VERSION", "dev")
COMMIT_SHA = os.getenv("COMMIT_SHA", "unknown")

HTTP_REQUESTS = Counter(
    "dcs29_http_requests_total",
    "Total HTTP requests handled by the dashboard.",
    ("method", "path", "status_code"),
)
HTTP_DURATION = Histogram(
    "dcs29_http_request_duration_seconds",
    "Time spent serving dashboard HTTP requests.",
    ("method", "path"),
)

_request_count = 0
_request_count_lock = threading.Lock()


def _load_devices() -> tuple[list[dict[str, Any]], str]:
    raw_dataset = DATASET_PATH.read_bytes()
    devices = json.loads(raw_dataset)
    return devices, hashlib.sha256(raw_dataset).hexdigest()


DEVICES, DATASET_SHA256 = _load_devices()


def _increment_request_count() -> int:
    global _request_count
    with _request_count_lock:
        _request_count += 1
        return _request_count


def _current_request_count() -> int:
    with _request_count_lock:
        return _request_count


def _status_payload() -> dict[str, Any]:
    return {
        "status": "healthy",
        "environment": APP_ENV,
        "region": APP_REGION,
        "version": APP_VERSION,
        "commit_sha": COMMIT_SHA,
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 3),
        "request_count": _current_request_count(),
        "dataset_sha256": DATASET_SHA256,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app = FastAPI(
    title="DCS29 Multi-Cloud Service Status",
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
        _increment_request_count()
        raise

    duration = time.perf_counter() - started
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, path).observe(duration)
    _increment_request_count()
    response.headers["X-Served-By"] = APP_ENV
    response.headers["X-App-Version"] = APP_VERSION
    if path.startswith(("/api/", "/health/", "/version")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> dict[str, str]:
    return {"status": "ready", "dataset_sha256": DATASET_SHA256}


@app.get("/api/status")
async def service_status() -> dict[str, Any]:
    return _status_payload()


@app.get("/version")
async def version() -> dict[str, Any]:
    return {
        key: value
        for key, value in _status_payload().items()
        if key in {"environment", "region", "version", "commit_sha", "dataset_sha256", "timestamp"}
    }


@app.get("/api/devices")
async def devices() -> dict[str, Any]:
    return {
        "environment": APP_ENV,
        "version": APP_VERSION,
        "dataset_sha256": DATASET_SHA256,
        "devices": DEVICES,
    }


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
