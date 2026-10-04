"""Local PostgreSQL replication demo. No cloud resources or destructive reset."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

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
    from scripts.local_lifecycle import operation_lock
    with operation_lock():
        _setup()


def _setup():
    from database.local_state import read_state, write_state
    STATE.mkdir(parents=True, exist_ok=True)
    state = read_state()
    if state["writer"] != "aws" or state["phase"] != "steady":
        raise RuntimeError("Topology đã chuyển đổi hoặc đang phục hồi; dùng status/failback, không setup lại primary cũ.")
    if (STATE / "promoted").exists():
        raise RuntimeError("Failover đã diễn ra. Không khởi động AWS cũ; cần tái đồng bộ trước khi failback.")
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


def wait_replay(target, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        with connection(AZURE_DB) as db:
            row = db.execute("SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn AS caught_up", (target,)).fetchone()
            if row["caught_up"]:
                return
        time.sleep(.2)
    raise RuntimeError("Azure chưa replay tới WAL mục tiêu. Không promote.")


def failover():
    from scripts.local_lifecycle import failover as transition
    transition()


def rejoin_aws():
    from scripts.local_lifecycle import rejoin_aws as transition
    transition()


def failback():
    from scripts.local_lifecycle import failback as transition
    transition()


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "status", "start-web", "start-proxy", "failover", "rejoin-aws", "failback"))
    args = parser.parse_args()
    try:
        {"setup": setup, "status": show_status, "start-web": start_web, "start-proxy": start_proxy,
         "failover": failover, "rejoin-aws": rejoin_aws, "failback": failback}[args.action]()
    except (RuntimeError, psycopg.Error, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
