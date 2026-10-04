"""Report actual PostgreSQL role; fail closed when the database is unavailable."""
import psycopg
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database.connection import connect
from database.control import writable

router = APIRouter()


def status():
    with connect() as db:
        state = db.execute("SELECT pg_is_in_recovery() AS standby, current_setting('transaction_read_only') AS read_only").fetchone()
        standby = state["standby"]
        if standby:
            wal = db.execute("SELECT pg_last_wal_receive_lsn()::text AS receive_lsn, pg_last_wal_replay_lsn()::text AS replay_lsn").fetchone()
            receiver = db.execute("SELECT status FROM pg_stat_wal_receiver").fetchone()
            return {"connected": True, "role": "standby", "writable": False,
                    "replication_connected": bool(receiver and receiver["status"] == "streaming"), **wal}
        wal = db.execute("SELECT pg_current_wal_lsn()::text AS current_lsn").fetchone()
        enabled = writable(db)
        return {"connected": True, "role": "primary", "writable": state["read_only"] == "off" and enabled,
                "writes_paused": not enabled, **wal}


@router.get("/api/database/status")
def database_status():
    try:
        return status()
    except psycopg.Error:
        return JSONResponse(status_code=503, content={"connected": False, "role": "unknown", "writable": False})


@router.get("/health/database")
def database_readiness():
    try:
        state = status()
        return JSONResponse(status_code=200 if state["writable"] else 503, content=state)
    except psycopg.Error:
        return JSONResponse(status_code=503, content={"connected": False, "role": "unknown", "writable": False})
