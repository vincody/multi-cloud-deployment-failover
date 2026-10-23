"""Failover watcher: fail over to Azure only when two independent checks agree that AWS is down.

Local stand-ins for the cloud setup: the web check is the AWS origin's /health/database and the
database check is a direct connection to the AWS PostgreSQL. If only one fails the watcher keeps
watching. Once it has failed over it stops; it never fails back or restarts the old primary.

    python scripts/watcher.py --interval 5 --threshold 3
"""
import argparse
import sys
import time
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import local_replication as demo


def evaluate(failures, web_ok, db_ok, threshold):
    """Return (new failure count, verdict). Only a run of agreeing failures reaches 'failover'."""
    if web_ok and db_ok:
        return 0, "healthy"
    if web_ok != db_ok:
        return failures, "suspect"
    failures += 1
    return failures, "failover" if failures >= threshold else "failing"


def blocked():
    """Reason the watcher must not act, or None. A running switchover pauses writes on purpose."""
    state = demo.read_state()
    if state["writer"] == "azure" and state["phase"] == "steady":
        return "done"
    if state["writer"] != "aws" or state["phase"] != "steady":
        return "busy"
    return None


def run(check_web, check_db, failover, interval, threshold, blocked=blocked, log=print, sleep=time.sleep):
    failures = 0
    while True:
        reason = blocked()
        if reason == "done":
            log("Azure is already the writer; nothing to do.")
            return "already-failed-over"
        if reason == "busy":
            failures = 0
        else:
            web, db = check_web(), check_db()
            failures, verdict = evaluate(failures, web, db, threshold)
            log(f"web={web} db={db} verdict={verdict} failures={failures}/{threshold}")
            if verdict == "failover":
                try:
                    failover()
                except Exception as error:
                    log(f"Failover stopped, operator needed: {error}")
                    return "operator-needed"
                return "failed-over"
        sleep(interval)


def check_web():
    try:
        return httpx.get(f"http://127.0.0.1:{demo.AWS_WEB}/health/database", timeout=2, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def check_db():
    try:
        with demo.connection(demo.AWS_DB) as db:
            db.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between checks")
    parser.add_argument("--threshold", type=int, default=3, help="consecutive agreeing failures before failover")
    args = parser.parse_args()
    try:
        outcome = run(check_web, check_db, demo.failover, args.interval, args.threshold)
    except KeyboardInterrupt:
        return 0
    print(f"Watcher finished: {outcome}")
    return 2 if outcome == "operator-needed" else 0


if __name__ == "__main__":
    sys.exit(main())
