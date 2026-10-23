"""Local PostgreSQL replication demo: setup, controlled failover, rebuild on new volumes and failback.

No cloud resources or destructive reset."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROJECT = os.getenv("LOCAL_REPL_PROJECT", "hetinhtoanphanbo")
AWS_DB = int(os.getenv("AWS_DB_PORT", "55432"))
AZURE_DB = int(os.getenv("AZURE_DB_PORT", "55433"))
AWS_WEB = int(os.getenv("AWS_WEB_PORT", "8080"))
AZURE_WEB = int(os.getenv("AZURE_WEB_PORT", "8081"))
STATE = ROOT / ".local-replication" / PROJECT
PROXY_WEB = int(os.getenv("PROXY_WEB_PORT", "8090"))

# Imported after ROOT is on sys.path so the script runs directly.
from database.control import LOCK, bootstrap
from database.local_state import read_state, write_state

SERVICES = {"aws": "database", "azure": "database-azure"}
DB_PORTS = {"aws": AWS_DB, "azure": AZURE_DB}
WEB_PORTS = {"aws": AWS_WEB, "azure": AZURE_WEB}


def compose(*args, capture=False):
    files = ["-f", str(ROOT / "database/compose.replication.yml")]
    if (STATE / "compose.override.json").exists():
        files += ["-f", str(STATE / "compose.override.json")]
    return subprocess.run(["docker", "compose", "-p", PROJECT, *files, *args],
                          cwd=ROOT, check=True, text=True, capture_output=capture)


def dsn(port):
    return f"postgresql://taskflow:local-demo-only@127.0.0.1:{port}/taskflow"


def connection(port):
    return psycopg.connect(dsn(port), connect_timeout=3, row_factory=dict_row)


def role(port):
    with connection(port) as db:
        return db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]


def setup():
    with operation_lock():
        _setup()


def _setup():
    STATE.mkdir(parents=True, exist_ok=True)
    state = read_state()
    if state["writer"] != "aws" or state["phase"] != "steady":
        raise RuntimeError("Topology đã chuyển đổi hoặc đang phục hồi; dùng status/failback, không setup lại primary cũ.")
    try:
        if not role(AZURE_DB):
            raise RuntimeError("Azure đã là primary. Từ chối khởi động AWS để tránh hai primary.")
    except psycopg.Error:
        pass
    compose("up", "-d", "--wait", "database")
    with connection(AWS_DB) as db:
        if db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]:
            raise RuntimeError("Database AWS đang là standby, không bootstrap cấu hình ngược.")
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='replicator'").fetchone():
            db.execute("CREATE ROLE replicator WITH LOGIN REPLICATION")
        db.execute(sql.SQL("ALTER ROLE replicator WITH LOGIN REPLICATION PASSWORD {}").format(sql.Literal("local-replication-only")))
    compose("exec", "-T", "database", "bash", "/replication/allow-replication.sh")
    with connection(AWS_DB) as db:
        db.execute("SELECT pg_reload_conf()")
    # Bootstrap schema only on the writer, before copying its entire cluster.
    os.environ["DATABASE_URL"] = dsn(AWS_DB)
    from database.issues import initialize
    initialize()
    compose("up", "-d", "--wait", "database-azure")
    if not role(AZURE_DB):
        raise RuntimeError("Azure không ở chế độ standby. Không tiếp tục.")
    print("Replication configured; AWS primary, Azure standby. Existing primary volume preserved.")
    write_state(state)
    show_status()


def show_status():
    report = {}
    for cloud, port in (("aws", AWS_DB), ("azure", AZURE_DB)):
        try:
            with connection(port) as db:
                standby = db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]
                row = db.execute("SELECT count(*) AS issues FROM issues").fetchone()
                report[cloud] = {"role": "standby" if standby else "primary", **row}
                if standby:
                    report[cloud].update(db.execute("SELECT pg_last_wal_receive_lsn()::text AS received, pg_last_wal_replay_lsn()::text AS replayed").fetchone())
                    report[cloud]["receiver"] = db.execute("SELECT status FROM pg_stat_wal_receiver").fetchone()
                else:
                    report[cloud]["replicas"] = db.execute("SELECT state,sent_lsn::text,replay_lsn::text FROM pg_stat_replication").fetchall()
        except psycopg.Error:
            report[cloud] = {"role": "unavailable"}
    print(json.dumps(report, ensure_ascii=False, indent=2))



def start_proxy():
    STATE.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PROXY_WEB)) == 0:
            raise RuntimeError(f"Port {PROXY_WEB} occupied; stop its existing proxy first.")
    with (STATE / "proxy.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.local_proxy:app", "--host", "127.0.0.1", "--port", str(PROXY_WEB)],
                                   cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (STATE / "proxy.json").write_text(json.dumps({"pid": process.pid, "port": PROXY_WEB}), encoding="utf-8")
    print(f"Shared URL: http://127.0.0.1:{PROXY_WEB}/issues.html (PID {process.pid})")


def start_web():
    STATE.mkdir(parents=True, exist_ok=True)
    processes = {}
    for cloud, web_port, db_port in (("aws", AWS_WEB, AWS_DB), ("azure", AZURE_WEB, AZURE_DB)):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", web_port)) == 0:
                raise RuntimeError(f"Port {web_port} occupied; stop its existing local backend first.")
    for cloud, web_port, db_port in (("aws", AWS_WEB, AWS_DB), ("azure", AZURE_WEB, AZURE_DB)):
        env = os.environ | {"APP_ENV": cloud, "APP_REGION": "local-simulation", "DATABASE_URL": dsn(db_port)}
        with (STATE / f"{cloud}-web.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(web_port)],
                                       cwd=ROOT, env=env, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        processes[cloud] = {"pid": process.pid, "port": web_port}
    (STATE / "web.json").write_text(json.dumps(processes, indent=2), encoding="utf-8")
    for cloud, details in processes.items():
        print(f"{cloud}: http://127.0.0.1:{details['port']}/issues.html (PID {details['pid']})")


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
    compose("stop", SERVICES[cloud])
    container = compose("ps", "--all", "-q", SERVICES[cloud], capture=True).stdout.strip()
    if not container:
        raise RuntimeError(f"Không xác định được container {cloud}; từ chối promote.")
    info = subprocess.run(["docker", "inspect", container], check=True, capture_output=True, text=True)
    state = json.loads(info.stdout)[0]
    if state["State"]["Running"] or state["HostConfig"]["RestartPolicy"]["Name"] not in ("no", ""):
        raise RuntimeError(f"Database {cloud} chưa được cô lập; từ chối promote.")


def gate(cloud, enabled):
    with connection(DB_PORTS[cloud]) as db:
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
        with connection(DB_PORTS[cloud]) as db:
            row = db.execute("SELECT pg_is_in_recovery() AS standby, pg_last_wal_replay_lsn() >= %s::pg_lsn AS caught_up", (target,)).fetchone()
            if not row["standby"]:
                raise RuntimeError(f"{cloud} không phải standby; không thể xác nhận đồng bộ.")
            if row["caught_up"]:
                return
        time.sleep(.2)
    raise RuntimeError(f"{cloud} chưa replay đủ WAL; không đổi vai trò.")


def promote(cloud):
    with connection(DB_PORTS[cloud]) as db:
        db.autocommit = True
        if db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]:
            if not db.execute("SELECT pg_promote(true,30) AS promoted").fetchone()["promoted"]:
                raise RuntimeError(f"Promote {cloud} chưa hoàn tất.")
    gate(cloud, True)


def verify_writer(cloud):
    with connection(DB_PORTS[cloud]) as db:
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


def _already_on_azure():
    state = read_state()
    if state["writer"] != "azure" or state["phase"] != "steady":
        return False
    if role(AZURE_DB):
        raise RuntimeError("State ghi Azure primary nhưng database vẫn standby.")
    try:
        if not role(AWS_DB):
            raise RuntimeError("AWS cũ vẫn là primary; cần cô lập trước khi tiếp tục.")
    except psycopg.Error:
        pass
    verify_writer("azure")
    return True


def _take_over_on_azure():
    """Shared tail of failover and switchover: fence the old primary, promote, verify, route."""
    fence("aws")
    promote("azure")
    verify_writer("azure")
    phase("steady", writer="azure")
    print("Azure is now the writer; shared proxy routes to Azure.")


def failover():
    """AWS is down: fence and promote at once. Unreplicated transactions may be lost (asynchronous replication)."""
    with operation_lock():
        if _already_on_azure():
            return
        phase("switching-to-azure")
        try:
            try:
                role(AWS_DB)
                print("AWS still answers; use 'switchover' to avoid losing recent transactions.")
            except psycopg.Error:
                print("AWS unavailable; unsent transactions may be lost with asynchronous replication.")
            _take_over_on_azure()
        except Exception as error:
            phase("switching-to-azure", error=str(error))
            raise
    show_status()


def switchover():
    """Planned move to Azure with no data loss: stop writes, let Azure catch up, then take over."""
    with operation_lock():
        if _already_on_azure():
            return
        phase("switching-to-azure")
        try:
            try:
                target = gate("aws", False)
            except psycopg.Error:
                raise RuntimeError("AWS không phản hồi; không thể chuyển có kế hoạch. Dùng 'failover'.")
            if role(AZURE_DB):
                wait_replay("azure", target)
            _take_over_on_azure()
        except Exception as error:
            phase("switching-to-azure", error=str(error))
            raise
    show_status()


def rebuild(target, source):
    if role(DB_PORTS[source]):
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
    state["archives"] = state.get("archives", []) + [{"cloud": target, "volume": f"{PROJECT}_{old}"}]
    write_state(state)
    compose("up", "-d", "--wait", service)
    if not role(DB_PORTS[target]):
        raise RuntimeError("Bản sao mới chưa vào standby; volume cũ vẫn được giữ.")
    with connection(DB_PORTS[source]) as db:
        lsn = db.execute("SELECT pg_current_wal_flush_lsn()::text AS lsn").fetchone()["lsn"]
    wait_replay(target, lsn)


def _rejoin_aws():
    state = read_state()
    if state["writer"] != "azure" or role(AZURE_DB):
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
    show_status()


def follows(target, source):
    try:
        with connection(DB_PORTS[target]) as db:
            if not db.execute("SELECT pg_is_in_recovery() AS standby").fetchone()["standby"]:
                return False
            receiver = db.execute("SELECT sender_host,status FROM pg_stat_wal_receiver").fetchone()
            if not receiver or receiver["sender_host"] != SERVICES[source] or receiver["status"] != "streaming":
                return False
        with connection(DB_PORTS[source]) as db:
            lsn = db.execute("SELECT pg_current_wal_flush_lsn()::text AS lsn").fetchone()["lsn"]
        wait_replay(target, lsn)
        return True
    except psycopg.Error:
        return False


def finish_failback():
    verify_writer("aws")
    phase("rebuilding-azure", writer="aws")
    if not follows("azure", "aws"):
        rebuild("azure", "aws")
    phase("steady", final_lsn=None)
    print("Failback complete: proxy routes to AWS; Azure is streaming standby again.")


def is_standby(cloud):
    try:
        return role(DB_PORTS[cloud])
    except psycopg.Error:
        return False


def failback():
    """Return to AWS. Each step is safe to repeat, so a stopped run is resumed by running it again."""
    with operation_lock():
        state = read_state()
        if state["writer"] == "aws":
            if role(AWS_DB):
                raise RuntimeError("State ghi AWS primary nhưng database vẫn standby.")
            verify_writer("aws")
            if state["phase"] == "rebuilding-azure":
                finish_failback()
            return
        # final_lsn is recorded once Azure is write-locked and AWS has caught up;
        # from then on only fence, promote and verify remain.
        resume = state["phase"] == "switching-to-aws" and state.get("final_lsn") is not None
        try:
            if not resume:
                if not is_standby("aws"):
                    _rejoin_aws()
                phase("switching-to-aws")
                try:
                    target = gate("azure", False)
                except psycopg.Error:
                    raise RuntimeError("Azure không phản hồi trước khi xác nhận WAL cuối; từ chối failback có kế hoạch.")
                wait_replay("aws", target)
                phase("switching-to-aws", final_lsn=target)
            elif is_standby("aws"):
                wait_replay("aws", state["final_lsn"])
            fence("azure")
            promote("aws")
            verify_writer("aws")
            # Routing may resume now; rebuilding Azure does not interrupt the writer.
            finish_failback()
        except Exception as error:
            phase(read_state()["phase"], error=str(error))
            raise
    show_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "status", "start-web", "start-proxy", "failover", "switchover", "rejoin-aws", "failback"))
    args = parser.parse_args()
    try:
        {"setup": setup, "status": show_status, "start-web": start_web, "start-proxy": start_proxy,
         "failover": failover, "switchover": switchover, "rejoin-aws": rejoin_aws, "failback": failback}[args.action]()
    except (RuntimeError, psycopg.Error, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
