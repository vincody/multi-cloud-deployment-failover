"""Decision loop shared by the local and the cloud failover watchers (no I/O of its own)."""
import time


def evaluate(failures, web_ok, db_ok, threshold):
    """Return (new failure count, verdict). Only a run of agreeing failures reaches 'failover'."""
    if web_ok and db_ok:
        return 0, "healthy"
    if web_ok != db_ok:
        return failures, "suspect"
    failures += 1
    return failures, "failover" if failures >= threshold else "failing"


def run(check_web, check_db, failover, interval, threshold, blocked, log=print, sleep=time.sleep):
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
