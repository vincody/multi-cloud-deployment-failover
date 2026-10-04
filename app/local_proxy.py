"""One local origin; routing changes only after the controller verifies promotion."""
from contextlib import asynccontextmanager
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
import httpx

from database.local_state import read_state

PORTS = {"aws": int(os.getenv("AWS_WEB_PORT", "8080")), "azure": int(os.getenv("AZURE_WEB_PORT", "8081"))}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


@asynccontextmanager
async def lifespan(app):
    # No automatic request retries: a failed response may follow a committed write.
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def routing():
    state = read_state()
    if state.get("writer") not in PORTS or state.get("phase") not in {"steady", "rejoining", "rebuilding-azure"}:
        return None, state
    return state["writer"], state


@app.get("/api/proxy/status")
def proxy_status():
    try:
        writer, state = routing()
        return {"writer": state.get("writer"), "phase": state.get("phase"), "routing_enabled": writer is not None}
    except (OSError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"routing_enabled": False, "phase": "unknown"})


@app.get("/")
def index():
    return RedirectResponse("/issues.html")


@app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def forward(request: Request, path: str):
    try:
        writer, state = routing()
        if writer is None:
            return JSONResponse(status_code=503, content={"detail": "Đang chuyển đổi môi trường. Bản nháp được giữ; hãy thử lại sau."},
                                headers={"Cache-Control": "no-store", "Retry-After": "2"})
        raw_path = request.scope["raw_path"].decode("ascii")
        query = request.scope["query_string"].decode("ascii")
        url = f"http://127.0.0.1:{PORTS[writer]}{raw_path}" + (f"?{query}" if query else "")
        connection_headers = set(request.headers.get("connection", "").lower().split(","))
        excluded = HOP_HEADERS | connection_headers | {"host", "content-length", "accept-encoding", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-for"}
        headers = {key: value for key, value in request.headers.items() if key.lower() not in excluded}
        headers["X-Forwarded-Host"] = request.headers.get("host", "localhost:8090")
        headers["X-Forwarded-Proto"] = "http"
        upstream = await request.app.state.client.request(request.method, url, content=await request.body(), headers=headers)
        excluded_response = HOP_HEADERS | {"content-length", "content-encoding"}
        excluded_response |= set(upstream.headers.get("connection", "").lower().split(","))
        response = Response(content=upstream.content, status_code=upstream.status_code)
        response.raw_headers = [(key, value) for key, value in upstream.headers.raw if key.decode().lower() not in excluded_response]
        response.headers["X-Local-Proxy-Origin"] = writer
        return response
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=503, content={"detail": "Chưa kết nối được môi trường phục vụ. Giữ bản nháp và thử lại sau."},
                            headers={"Cache-Control": "no-store"})
