"""Run against local PostgreSQL with TASKFLOW_DB_TESTS=1; isolated schema."""
import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from database import tasks
from app.main import app

pytestmark = pytest.mark.skipif(os.getenv("TASKFLOW_DB_TESTS") != "1", reason="requires local PostgreSQL")


@pytest.fixture
def client(monkeypatch):
    schema = "test_" + uuid4().hex
    original = tasks.connect
    with original() as db:
        db.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema)))

    def isolated():
        db = original()
        db.execute(psycopg.sql.SQL("SET search_path TO {}").format(psycopg.sql.Identifier(schema)))
        return db

    monkeypatch.setattr(tasks, "connect", isolated)
    tasks.initialize()
    yield TestClient(app)
    with original() as db:
        db.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema)))


def payload(**changes):
    return {"request_id": str(uuid4()), "title": "Viết báo cáo", "description": "Bản nháp", "status": "todo", "version": 0} | changes


def test_retry_is_atomic_and_rejects_reused_key(client):
    body = payload()
    first = client.post("/api/tasks", json=body)
    assert first.status_code == 200
    assert client.post("/api/tasks", json=body).json() == first.json()
    assert len(client.get("/api/tasks").json()) == 1
    assert len(client.get(f"/api/tasks/{first.json()['id']}/history").json()) == 1
    assert client.post("/api/tasks", json=body | {"title": "Khác"}).status_code == 409


def test_stale_edit_preserves_current_data(client):
    task = client.post("/api/tasks", json=payload()).json()
    path = f"/api/tasks/{task['id']}"
    assert client.put(path, json=payload(version=1, status="done")).json()["version"] == 2
    assert client.put(path, json=payload(version=1, title="Ghi đè")).status_code == 409
    current = client.get("/api/tasks").json()[0]
    assert current["status"] == "done" and current["title"] == "Viết báo cáo"
    assert len(client.get(path + "/history").json()) == 2


def test_blank_title_and_missing_task(client):
    assert client.post("/api/tasks", json=payload(title="  ")).status_code == 422
    assert client.put(f"/api/tasks/{uuid4()}", json=payload(version=1)).status_code == 404
    assert client.get("/api/tasks").json() == []
