"""Issue integration tests use an isolated PostgreSQL schema."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from database import issues
from app.main import app

pytestmark = pytest.mark.skipif(os.getenv("TASKFLOW_DB_TESTS") != "1", reason="requires local PostgreSQL")


@pytest.fixture
def client(monkeypatch):
    schema = "issues_test_" + uuid4().hex
    original = issues.connect
    with original() as db:
        db.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema)))

    def isolated():
        db = original()
        db.execute(psycopg.sql.SQL("SET search_path TO {}").format(psycopg.sql.Identifier(schema)))
        return db

    monkeypatch.setattr(issues, "connect", isolated)
    try:
        issues.initialize()
        with TestClient(app) as test_client:
            yield test_client
    finally:
        with original() as db:
            db.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema)))


def payload(**changes):
    return {"request_id": str(uuid4()), "author": "Minh", "title": "Không thể đăng nhập", "description": "Lỗi khi gửi form", "status": "open", "version": 0} | changes


def test_concurrent_retry_creates_one_issue(client):
    body = payload()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/issues", json=body), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert len(client.get("/api/issues").json()) == 1
    detail = client.get(f"/api/issues/{responses[0].json()['id']}").json()
    assert len(detail["history"]) == 1
    assert client.post("/api/issues", json=body | {"title": "Khác"}).status_code == 409


def test_comment_retry_and_closed_issue(client):
    issue = client.post("/api/issues", json=payload()).json()
    path = f"/api/issues/{issue['id']}"
    closed = client.put(path, json=payload(version=1, status="closed"))
    assert closed.status_code == 200
    body = {"request_id": str(uuid4()), "author": "Lan", "body": "Đã sửa lỗi."}
    first = client.post(path + "/comments", json=body)
    assert first.status_code == 200
    assert client.post(path + "/comments", json=body).json() == first.json()
    assert client.post(path + "/comments", json=body | {"body": "Khác"}).status_code == 409
    detail = client.get(path).json()
    assert len(detail["comments"]) == 1
    assert detail["issue"]["version"] == 2
    assert [entry["action"] for entry in detail["history"]] == ["commented", "closed", "created"]
    assert client.get("/api/issues").json()[0]["comment_count"] == 1


def test_stale_edit_rejected_and_reopen_recorded(client):
    issue = client.post("/api/issues", json=payload()).json()
    path = f"/api/issues/{issue['id']}"
    assert client.put(path, json=payload(version=1, status="closed")).status_code == 200
    assert client.put(path, json=payload(version=1, title="Ghi đè")).status_code == 409
    assert client.put(path, json=payload(version=2, status="open")).status_code == 200
    detail = client.get(path).json()
    assert detail["issue"]["version"] == 3
    assert detail["issue"]["title"] == "Không thể đăng nhập"
    assert [entry["action"] for entry in detail["history"]] == ["reopened", "closed", "created"]


def test_invalid_input_and_missing_issue_do_not_write(client):
    assert client.post("/api/issues", json=payload(title="  ")).status_code == 422
    assert client.post("/api/issues", json=payload(author=" ")).status_code == 422
    assert client.post("/api/issues", json=payload(status="closed")).status_code == 422
    missing = f"/api/issues/{uuid4()}"
    assert client.get(missing).status_code == 404
    assert client.post(missing + "/comments", json={"request_id": str(uuid4()), "author": "Lan", "body": "Test"}).status_code == 404
    assert client.get("/api/issues").json() == []


def test_paused_writes_keep_retry_safe(client):
    body = payload()
    with issues.connect() as db:
        db.execute("UPDATE app_control SET writes_enabled=FALSE")
    assert client.post("/api/issues", json=body).status_code == 503
    assert client.get("/api/issues").json() == []
    with issues.connect() as db:
        db.execute("UPDATE app_control SET writes_enabled=TRUE")
    assert client.post("/api/issues", json=body).status_code == 200
    assert client.post("/api/issues", json=body).status_code == 200
    assert len(client.get("/api/issues").json()) == 1


def test_queued_writer_cannot_bypass_pause(client):
    from database.control import LOCK
    with issues.connect() as db, ThreadPoolExecutor(max_workers=1) as pool:
        db.execute("SELECT pg_advisory_lock(%s,%s)", LOCK)
        db.commit()
        queued = pool.submit(client.post, "/api/issues", json=payload())
        try:
            until = time.monotonic() + 5
            while time.monotonic() < until:
                waiting = db.execute("SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND classid=2900 AND objid=1 AND NOT granted").fetchone()["n"]
                if waiting:
                    break
                time.sleep(.05)
            assert waiting, "writer did not reach the shared barrier"
            db.execute("UPDATE app_control SET writes_enabled=FALSE")
            db.commit()
        finally:
            db.execute("SELECT pg_advisory_unlock(%s,%s)", LOCK)
            db.commit()
        assert queued.result(timeout=5).status_code == 503
    assert client.get("/api/issues").json() == []
