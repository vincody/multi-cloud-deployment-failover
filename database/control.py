"""Shared write barrier: the controller can drain writers before switchover."""
from pathlib import Path

from fastapi import HTTPException

LOCK = (2900, 1)


def bootstrap(db):
    db.execute((Path(__file__).parent / "schema/control.sql").read_text(encoding="utf-8"))


def writable(db):
    state = db.execute("SELECT writes_enabled FROM app_control WHERE singleton=TRUE").fetchone()
    return bool(state and state["writes_enabled"])


def guard_write(db):
    # This shared lock lasts until commit/rollback. The controller takes an exclusive
    # session lock to wait for ALL application writes, including direct-origin requests.
    db.execute("SELECT pg_advisory_xact_lock_shared(%s,%s)", LOCK)
    if not writable(db):
        raise HTTPException(503, "Đang chuyển đổi môi trường. Bản nháp được giữ; hãy thử lại sau.")
