import httpx
from fastapi.testclient import TestClient

from app import local_proxy


def test_transition_blocks_requests_without_contacting_origin(monkeypatch):
    monkeypatch.setattr(local_proxy, "read_state", lambda: {"writer": "aws", "phase": "switching-to-azure"})
    with TestClient(local_proxy.app) as client:
        response = client.post("/api/issues", json={"title": "Not forwarded"})
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/proxy/status").json()["routing_enabled"] is False


def test_proxy_preserves_request_and_does_not_retry_failed_write(monkeypatch):
    monkeypatch.setattr(local_proxy, "read_state", lambda: {"writer": "azure", "phase": "steady"})
    seen = []

    def origin(request):
        seen.append(request)
        return httpx.Response(503, json={"detail": "Unknown commit result"}, headers={"X-Served-By": "azure"})

    with TestClient(local_proxy.app) as client:
        original = local_proxy.app.state.client
        local_proxy.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(origin))
        try:
            response = client.post("/api/issues?test=1", json={"request_id": "same-request"})
        finally:
            client.portal.call(local_proxy.app.state.client.aclose)
            local_proxy.app.state.client = original
        assert response.status_code == 503
        assert response.headers["x-served-by"] == "azure"
        assert response.headers["x-local-proxy-origin"] == "azure"
        assert len(seen) == 1
        assert seen[0].url.port == 8081
        assert seen[0].url.query == b"test=1"
        assert b"same-request" in seen[0].content
