"""Controlled local failover, rebuild on new volumes, and lossless planned failback."""
from contextlib import contextmanager
import json
import os
import subprocess
import time
from uuid import uuid4

import httpx
import psycopg

from database.control import LOCK, bootstrap
from database.local_state import STATE, read_state, write_state
from scripts import local_replication as demo

SERVICES = {"aws": "database", "azure": "database-azure"}
DB_PORTS = {"aws": demo.AWS_DB, "azure": demo.AZURE_DB}
WEB_PORTS = {"aws": demo.AWS_WEB, "azure": demo.AZURE_WEB}


@contextmanager
def operation_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "operation.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("Một thao tác topology đang chạy. Kiểm tra PID trong operation.lock; không chạy hai thao tác cùng lúc.")
    with os.fdopen(fd, "w") as file:
        file.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def phase(value, **fields):
    state = read_state() | fields | {"phase": value}
    if "error" not in fields:
        state.pop("error", None)
    write_state(state)


def fence(cloud):
    demo.compose("stop", SERVICES[cloud])
    container = demo.compose("ps", "--all", "-q", SERVICES[cloud], capture=True).stdout.strip()
    if not container:
        raise RuntimeError(f"Không xác định được container {cloud}; từ chối promote.")
    info = subprocess.run(["docker", "inspect", container], check=True, capture_output=True, text=True)
    state = json.loads(info.stdout)[0]
    if state["State"]["Running"] or state["HostConfig"]["RestartPolicy"]["Name"] not in ("no", ""):
        raise RuntimeError(f"Database {cloud} chưa được cô lập; từ chối promote.")


def gate(cloud, enabled):
    with demo.connection(DB_PORTS[cloud]) as db:
        db.execute("SET lock_timeout='15s'")
        # Wait for every application writer holding the shared transaction lock.
        db.execute("SELECT pg_advisory_lock(%s,%s)", LOCK)
        try:
            bootstrap(db)
            db.execute("UPDATE app_control SET writes_enabled=%s WHERE singleton=TRUE", (enabled,))
            db.commit()
            return db.execute("SELECT pg_current_wal_flush_lsn()::text AS lsn").fetchone()["lsn"]
        finally:
            db.execute("SELECT pg_advisory_unlock(%s,%s)", LOCK)


def wait_replay(cloud, target, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        with demo.connection(DB_PORTS[cloud]) as db:
            row = db.execute("SELECT pg_is_in_recovery() AS standby, pg_last_wal_replay_lsn() >= %s::pg_lsn AS caught_up", (target,)).fetchone()
            if not row["standby"]:
                raise RuntimeError(f"{cloud} không phải standby; không thể xác nhận đồng bộ.")
            if row["caught_up"]:
                return
        time.sleep(.2)
    raise RuntimeError(f"{cloud} chưa replay đủ WAL; không đổi vai trò.")


def promote(cloud):
    with demo.connection(DB_PORTS[cloud]) as db:
        db.autocommit = True
        if db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]:
            if not db.execute("SELECT pg_promote(true,30) AS promoted").fetchone()["promoted"]:
                raise RuntimeError(f"Promote {cloud} chưa hoàn tất.")
    gate(cloud, True)


def verify_writer(cloud):
    with demo.connection(DB_PORTS[cloud]) as db:
        state = db.execute("SELECT pg_is_in_recovery() AS standby, writes_enabled FROM app_control WHERE singleton=TRUE").fetchone()
        if state["standby"] or not state["writes_enabled"]:
            raise RuntimeError(f"{cloud} chưa sẵn sàng nhận ghi.")
        # Verify writes inside a rollback-only probe without changing business data.
        db.execute("UPDATE app_control SET writes_enabled=writes_enabled WHERE singleton=TRUE")
        db.rollback()
    # When a web exists, verify that it actually uses this writable database.
    try:
        response = httpx.get(f"http://127.0.0.1:{WEB_PORTS[cloud]}/health/database", timeout=5, trust_env=False)
    except httpx.ConnectError:
        if os.getenv("LOCAL_REPL_DB_ONLY") == "1":
            return  # Explicit database-only proof; normal routing requires a live origin.
        raise RuntimeError(f"Web {cloud} không kết nối được; proxy vẫn bị khóa.")
    if response.status_code != 200 or response.json().get("writable") is not True:
        raise RuntimeError(f"Web {cloud} chưa sẵn sàng; proxy vẫn bị khóa.")


def failover():
    with operation_lock():
        state = read_state()
        if state["writer"] == "azure" and state["phase"] == "steady":
            if demo.role(demo.AZURE_DB):
                raise RuntimeError("State ghi Azure primary nhưng database vẫn standby.")
            try:
                if not demo.role(demo.AWS_DB):
                    raise RuntimeError("AWS cũ vẫn là primary; cần cô lập trước khi tiếp tục.")
            except psycopg.Error:
                pass
            verify_writer("azure")
            return
        phase("switching-to-azure")
        try:
            aws_available = True
            try:
                demo.role(demo.AWS_DB)
            except psycopg.Error:
                aws_available = False
            if aws_available:
                target = gate("aws", False)
                if demo.role(demo.AZURE_DB):
                    wait_replay("azure", target)
            else:
                print("AWS unavailable; unsent transactions may be lost with asynchronous replication.")
            (STATE / "promoted").write_text("Do not start old AWS primary before rebuilding from Azure.\n", encoding="utf-8")
            fence("aws")
            promote("azure")
            verify_writer("azure")
            phase("steady", writer="azure")
            print("Failover complete: shared proxy now routes to Azure.")
        except Exception as error:
            phase("switching-to-azure", error=str(error))
            raise
    demo.show_status()


def rebuild(target, source):
    if demo.role(DB_PORTS[source]):
        raise RuntimeError("Nguồn sao chép không phải primary.")
    fence(target)
    path = STATE / "compose.override.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"services": {}, "volumes": {}}
    service = SERVICES[target]
    old_volumes = config["services"].get(service, {}).get("volumes", [])
    old = old_volumes[0].split(":")[0] if old_volumes else ("taskflow-local" if target == "aws" else "taskflow-azure")
    fresh = f"{target}-rebuild-{uuid4().hex[:10]}"
    config["volumes"][fresh] = {}
    config["services"][service] = {
        "entrypoint": ["bash", "/replication/standby.sh"], "command": [],
        "environment": {"PGDATA": "/var/lib/postgresql/data/standby", "PGPASSWORD": "local-replication-only", "PGPRIMARY": SERVICES[source]},
        "volumes": [f"{fresh}:/var/lib/postgresql/data"]}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(temp, path)
    state = read_state()
    state["archives"] = state.get("archives", []) + [{"cloud": target, "volume": f"{demo.PROJECT}_{old}"}]
    write_state(state)
    demo.compose("up", "-d", "--wait", service)
    if not demo.role(DB_PORTS[target]):
        raise RuntimeError("Bản sao mới chưa vào standby; volume cũ vẫn được giữ.")
    with demo.connection(DB_PORTS[source]) as db:
        lsn = db.execute("SELECT pg_current_wal_flush_lsn()::text AS lsn").fetchone()["lsn"]
    wait_replay(target, lsn)


def _rejoin_aws():
    state = read_state()
    if state["writer"] != "azure" or demo.role(demo.AZURE_DB):
        raise RuntimeError("Chỉ rejoin AWS khi Azure đang phục vụ dưới vai trò primary.")
    phase("rejoining")
    try:
        if not follows("aws", "azure"):
            rebuild("aws", "azure")
        phase("steady")
        print("AWS rebuilt on a new volume, now streaming from Azure. Old volume preserved.")
    except Exception as error:
        phase("rejoining", error=str(error))
        raise


def rejoin_aws():
    with operation_lock():
        _rejoin_aws()
    demo.show_status()


def follows(target, source):
    try:
        with demo.connection(DB_PORTS[target]) as db:
            if not db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]:
                return False
            receiver = db.execute("SELECT sender_host,status FROM pg_stat_wal_receiver").fetchone()
            if not receiver or receiver["sender_host"] != SERVICES[source] or receiver["status"] != "streaming":
                return False
        with demo.connection(DB_PORTS[source]) as db:
            lsn = db.execute("SELECT pg_current_wal_flush_lsn()::text AS lsn").fetchone()["lsn"]
        wait_replay(target, lsn)
        return True
    except psycopg.Error:
        return False


def finish_failback():
    verify_writer("aws")
    phase("rebuilding-azure", writer="aws")
    (STATE / "promoted").unlink(missing_ok=True)
    if not follows("azure", "aws"):
        rebuild("azure", "aws")
    phase("steady", final_lsn=None)
    print("Failback complete: proxy routes to AWS; Azure is streaming standby again.")


def failback():
    with operation_lock():
        state = read_state()
        if state["writer"] == "aws" and state["phase"] in {"steady", "rebuilding-azure"}:
            if demo.role(demo.AWS_DB):
                raise RuntimeError("State ghi AWS primary nhưng database vẫn standby.")
            verify_writer("aws")
            if state["phase"] == "rebuilding-azure":
                finish_failback()
            return
        # Resume safely if a previous command stopped Azure and promoted AWS but
        # failed before it verified the origin or persisted the new routing state.
        if state["phase"] == "switching-to-aws" and state.get("final_lsn") is not None:
            try:
                aws_promoted = not demo.role(demo.AWS_DB)
            except psycopg.Error:
                aws_promoted = False
            if aws_promoted:
                fence("azure")
                promote("aws")
                finish_failback()
                return
        # Ensure the old AWS writer is rebuilt before the planned switchover.
        try:
            aws_standby = demo.role(demo.AWS_DB)
        except psycopg.Error:
            aws_standby = False
        if not aws_standby:
            _rejoin_aws()
        phase("switching-to-aws")
        try:
            azure_available = True
            try:
                demo.role(demo.AZURE_DB)
            except psycopg.Error:
                azure_available = False
            if azure_available:
                target = gate("azure", False)
                wait_replay("aws", target)
            elif read_state().get("final_lsn") is None:
                raise RuntimeError("Azure unavailable before final WAL verification; refuse planned failback.")
            if azure_available:
                phase("switching-to-aws", final_lsn=target)
            else:
                wait_replay("aws", read_state()["final_lsn"])
            fence("azure")
            promote("aws")
            verify_writer("aws")
            # Routing may resume now; rebuilding Azure does not interrupt the writer.
            finish_failback()
        except Exception as error:
            phase(read_state()["phase"], error=str(error))
            raise
    demo.show_status()
