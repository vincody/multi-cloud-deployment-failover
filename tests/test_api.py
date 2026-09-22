from fastapi.testclient import TestClient

from app.main import DATASET_SHA256, app


client = TestClient(app)


def test_readiness_reports_loaded_dataset() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dataset_sha256": DATASET_SHA256}


def test_status_exposes_runtime_identity_without_cache() -> None:
    response = client.get("/api/status")
    body = response.json()

    assert response.status_code == 200
    assert body["environment"] == "local"
    assert body["status"] == "healthy"
    assert body["dataset_sha256"] == DATASET_SHA256
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-served-by"] == "local"


def test_devices_match_status_dataset() -> None:
    response = client.get("/api/devices")
    body = response.json()

    assert response.status_code == 200
    assert body["dataset_sha256"] == DATASET_SHA256
    assert len(body["devices"]) == 6
    assert {device["id"] for device in body["devices"]} == {
        "LAB-001",
        "LAB-002",
        "LAB-003",
        "LAB-004",
        "LAB-005",
        "LAB-006",
    }
