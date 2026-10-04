"""Durable local routing/topology state shared by controller and proxy."""
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.getenv("LOCAL_REPL_PROJECT", "hetinhtoanphanbo")
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", PROJECT):
    raise ValueError("Invalid local Compose project name")
STATE = ROOT / ".local-replication" / PROJECT


def read_state():
    path = STATE / "state.json"
    if not path.exists():
        return {"writer": "azure" if (STATE / "promoted").exists() else "aws", "phase": "steady", "archives": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(state):
    STATE.mkdir(parents=True, exist_ok=True)
    state = state | {"updated_at": time.time()}
    temp = STATE / "state.json.tmp"
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE / "state.json")
    with (STATE / "events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(state) + "\n")
