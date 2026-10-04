"""End-to-end replication/failover proof on a disposable, isolated Compose project."""
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Unique volumes and alternate ports: never promote or delete the user's local stack.
os.environ.update(LOCAL_REPL_PROJECT="dcs29-proof-" + uuid4().hex[:8], AWS_DB_PORT="55532", AZURE_DB_PORT="55533",
                  AWS_WEB_PORT="18080", AZURE_WEB_PORT="18081", LOCAL_REPL_DB_ONLY="1")
from scripts import local_replication as demo
from fastapi.testclient import TestClient
from app.main import app


def eventually(check, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(.2)
    raise AssertionError("Replication did not converge in time")


try:
    demo.setup()
    client = TestClient(app)
    os.environ["DATABASE_URL"] = demo.dsn(demo.AWS_DB)
    body = {"request_id": str(uuid4()), "title": "Kiểm thử replication", "description": "Tạo trên AWS", "author": "Demo", "status": "open", "version": 0}
    response = client.post("/api/issues", json=body)
    assert response.status_code == 200, response.text
    issue = response.json()
    path = f"/api/issues/{issue['id']}"
    comment = {"request_id": str(uuid4()), "author": "AWS", "body": "Bình luận cần đồng bộ"}
    assert client.post(path + "/comments", json=comment).status_code == 200
    update = body | {"request_id": str(uuid4()), "title": "Đã sửa trên AWS", "version": 1}
    assert client.put(path, json=update).status_code == 200
    os.environ["DATABASE_URL"] = demo.dsn(demo.AZURE_DB)
    eventually(lambda: client.get(path).status_code == 200 and client.get(path).json()["issue"]["version"] == 2)
    detail = client.get(path).json()
    assert detail["issue"]["title"] == "Đã sửa trên AWS"
    assert len(detail["comments"]) == 1 and len(detail["history"]) == 3
    assert client.get("/api/database/status").json()["role"] == "standby"
    assert client.get("/health/database").status_code == 503
    assert client.post("/api/issues", json=body | {"request_id": str(uuid4())}).status_code == 409
    assert client.post(path + "/comments", json=comment | {"request_id": str(uuid4())}).status_code == 409
    assert client.put(path, json=update | {"request_id": str(uuid4()), "version": 2}).status_code == 409
    demo.failover()
    assert client.get("/api/database/status").json()["writable"] is True
    assert client.get("/health/database").status_code == 200
    after = {"request_id": str(uuid4()), "author": "Azure", "body": "Tiếp tục sau failover"}
    first = client.post(path + "/comments", json=after)
    assert first.status_code == 200
    assert client.post(path + "/comments", json=after).json() == first.json()
    assert client.put(path, json=update | {"request_id": str(uuid4()), "version": 2, "status": "closed"}).status_code == 200
    final = client.get(path).json()
    assert final["issue"]["status"] == "closed" and len(final["comments"]) == 2 and len(final["history"]) == 5
    os.environ["DATABASE_URL"] = demo.dsn(demo.AWS_DB)
    assert client.get("/api/database/status").status_code == 503
    try:
        demo.setup()
        raise AssertionError("setup must reject a promoted stack")
    except RuntimeError:
        pass
    print("PASS: replication of create/edit/comment/history, standby write rejection, fenced promotion, Azure write/retry, old primary unavailable, unsafe restart blocked")
finally:
    # This project and its volumes were created only by this run, never the user's project.
    if demo.PROJECT.startswith("dcs29-proof-"):
        demo.compose("down", "--volumes")
