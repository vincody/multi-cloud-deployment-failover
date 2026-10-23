from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_status_exposes_runtime_identity_without_cache() -> None:
    response = client.get("/api/status")
    body = response.json()

    assert response.status_code == 200
    assert body["environment"] == "local"
    assert body["status"] == "healthy"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-served-by"] == "local"


def test_root_redirects_to_issues() -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/issues.html"
