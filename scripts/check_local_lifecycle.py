"""Full shared-URL AWS -> Azure -> AWS proof, isolated from user data."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(LOCAL_REPL_PROJECT="dcs29-cycle-" + uuid4().hex[:8], AWS_DB_PORT="55632", AZURE_DB_PORT="55633",
                  AWS_WEB_PORT="18080", AZURE_WEB_PORT="18081", PROXY_WEB_PORT="18090", LOCAL_REPL_DB_ONLY="0")
from scripts import local_replication as demo
from database.local_state import read_state
from scripts import probe_failover as probe
from scripts import watcher

processes = []
base = "http://127.0.0.1:18090"


def start_app(module, port, env):
    demo.STATE.mkdir(parents=True, exist_ok=True)
    with (demo.STATE / f"proof-{port}.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=ROOT, env=os.environ | env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    processes.append(process)
    until = time.monotonic() + 15
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"Proof app {port} exited; inspect its log")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/" if module == "app.local_proxy:app" else f"http://127.0.0.1:{port}/api/status", timeout=1, trust_env=False).status_code in (200,307):
                return
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    raise RuntimeError("Proof app did not start")


def request(method, path, body=None, cloud=None):
    result = httpx.request(method, base + path, json=body, timeout=15, trust_env=False)
    assert result.status_code == 200, result.text
    if cloud:
        assert result.headers["x-served-by"] == cloud
        assert result.headers["x-local-proxy-origin"] == cloud
    return result.json()


def compare_issue(issue_id):
    path = f"/api/issues/{issue_id}"
    aws = httpx.get("http://127.0.0.1:18080" + path, trust_env=False).json()
    until = time.monotonic() + 15
    while time.monotonic() < until:
        azure = httpx.get("http://127.0.0.1:18081" + path, trust_env=False).json()
        if aws == azure:
            return
        time.sleep(.2)
    raise AssertionError("issue, comments and history differ")


try:
    demo.setup()
    start_app("app.main:app", 18080, {"APP_ENV": "aws", "DATABASE_URL": demo.dsn(demo.AWS_DB)})
    start_app("app.main:app", 18081, {"APP_ENV": "azure", "DATABASE_URL": demo.dsn(demo.AZURE_DB)})
    start_app("app.local_proxy:app", 18090, {})
    body = {"request_id": str(uuid4()), "author": "Demo", "title": "Vòng failover/failback", "description": "Tạo trên AWS", "status": "open", "version": 0}
    issue = request("POST", "/api/issues", body, "aws")
    path = f"/api/issues/{issue['id']}"
    aws_comment = {"request_id": str(uuid4()), "author": "AWS", "body": "Trước failover"}
    request("POST", path + "/comments", aws_comment, "aws")
    compare_issue(issue["id"])
    # The standby must refuse every write path before promotion.
    azure = "http://127.0.0.1:18081"
    assert httpx.get(azure + "/health/database", trust_env=False).status_code == 503
    assert httpx.post(azure + "/api/issues", json=body | {"request_id": str(uuid4())}, trust_env=False).status_code == 409
    assert httpx.post(azure + path + "/comments", json=aws_comment | {"request_id": str(uuid4())}, trust_env=False).status_code == 409
    demo.switchover()
    assert request("GET", "/api/database/status", cloud="azure")["writable"]
    try:
        demo.setup()
        raise AssertionError("setup must reject a promoted stack")
    except RuntimeError:
        pass
    azure_comment = {"request_id": str(uuid4()), "author": "Azure", "body": "Phát sinh khi AWS dừng"}
    first = request("POST", path + "/comments", azure_comment, "azure")
    assert request("POST", path + "/comments", azure_comment, "azure") == first
    request("PUT", path, body | {"request_id": str(uuid4()), "version": 1, "status": "closed"}, "azure")
    demo.rejoin_aws()
    assert demo.role(demo.AWS_DB)
    compare_issue(issue["id"])
    before = request("GET", path, cloud="azure")
    assert before["issue"]["status"] == "closed" and len(before["comments"]) == 2
    demo.failback()
    after = request("GET", path, cloud="aws")
    assert before == after
    assert len(after["history"]) == 4
    assert not demo.role(demo.AWS_DB) and demo.role(demo.AZURE_DB)
    compare_issue(issue["id"])
    request("POST", path + "/comments", {"request_id": str(uuid4()), "author": "AWS", "body": "Sau failback"}, "aws")
    compare_issue(issue["id"])
    archives = read_state()["archives"]
    assert len(archives) == 2
    for archive in archives:
        subprocess.run(["docker", "volume", "inspect", archive["volume"]], check=True, capture_output=True)
    # Repeating failback/rejoin must not create extra volume copies in a healthy state.
    demo.failback()
    assert read_state()["archives"] == archives
    # A second cycle exercises persisted Compose overrides, not just the initial volumes.
    demo.switchover()
    demo.failback()
    compare_issue(issue["id"])
    # A failback stopped after Azure was locked and AWS caught up must resume by re-running it.
    demo.switchover()
    demo.rejoin_aws()
    target = demo.gate("azure", False)
    demo.wait_replay("aws", target)
    demo.phase("switching-to-aws", final_lsn=target)
    demo.failback()
    compare_issue(issue["id"])
    # ...and one stopped right after AWS was promoted.
    demo.switchover()
    demo.rejoin_aws()
    target = demo.gate("azure", False)
    demo.wait_replay("aws", target)
    demo.phase("switching-to-aws", final_lsn=target)
    demo.fence("azure")
    demo.promote("aws")
    demo.failback()
    assert not demo.role(demo.AWS_DB) and demo.role(demo.AZURE_DB)
    compare_issue(issue["id"])
    # Emergency path: AWS database killed with no warning, then a plain failover.
    stop, rows = threading.Event(), []

    def run_probe():
        with httpx.Client(timeout=3, trust_env=False) as client:
            seq = 0
            while not stop.is_set():
                rows.append(probe.tick(client, base, "check-run", seq))
                seq += 1
                stop.wait(.5)

    thread = threading.Thread(target=run_probe)
    thread.start()
    time.sleep(2)
    # The watcher decides on its own: it must see both checks fail three times, then fence and promote.
    outcome = {}
    monitor = threading.Thread(target=lambda: outcome.update(
        result=watcher.run(watcher.check_web, watcher.check_db, demo.failover, interval=.5, threshold=3, log=lambda _: None)))
    monitor.start()
    killed_at = datetime.now(timezone.utc)
    demo.compose("kill", "database")
    monitor.join(timeout=120)
    assert outcome.get("result") == "failed-over", outcome
    started = datetime.fromisoformat(read_state()["timeline"][0]["at"])
    print(f"detection time: {(started - killed_at).total_seconds():.1f}s (kill -> failover start)")
    time.sleep(2)
    stop.set()
    thread.join()
    summary = probe.summarize(rows, base, "check-run")
    assert any(end for _, end, _ in probe.outages(rows, "write_status")), "probe saw no recovered write outage"
    assert {"aws", "azure"} <= set(summary["served_by"]), summary["served_by"]
    assert "error" not in summary["lost_writes"], summary["lost_writes"]
    steps = [entry["step"] for entry in read_state()["timeline"]]
    expected = ["start", "azure-received-lsn", "fenced-aws", "writes-enabled-azure", "verified-azure", "done"]
    assert [s for s in steps if s in expected] == expected, steps
    assert request("GET", "/api/database/status", cloud="azure")["writable"]
    request("POST", path + "/comments", {"request_id": str(uuid4()), "author": "Azure", "body": "Sau khi AWS sập"}, "azure")
    demo.failback()
    request("GET", path, cloud="aws")
    compare_issue(issue["id"])
    print("PASS: one URL, standby write rejection, unsafe setup blocked, proxy switches, AWS/Azure writes, retry, full data/history preservation, archived volumes, repeat cycle, resumed failback (two stop points), watcher-triggered failover after AWS kill with live probe and timeline")
finally:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    if demo.PROJECT.startswith("dcs29-cycle-"):
        demo.compose("down", "--volumes")
