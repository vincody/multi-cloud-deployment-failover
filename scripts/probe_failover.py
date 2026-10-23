"""Probe a shared URL once per interval during a failover test and report downtime, errors, latency and lost writes.

Each tick does a read (GET /version) and a write (POST /api/ping). Writes carry an increasing
sequence number, so after the run the script can ask the current writer which acknowledged
writes it no longer has: that count is the measured data loss (RPO) in writes.

    python scripts/probe_failover.py --url http://127.0.0.1:8090 --duration 120 --out probe.csv
"""
import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx


def tick(client, base, run_id, seq):
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "seq": seq,
           "read_status": "", "read_ms": "", "write_status": "", "write_ms": "", "served_by": "", "error": ""}
    for name, call in (("read", lambda: client.get(base + "/version")),
                       ("write", lambda: client.post(base + "/api/ping", json={"run_id": run_id, "seq": seq}))):
        started = time.perf_counter()
        try:
            response = call()
            row[f"{name}_status"] = response.status_code
            if name == "write" or not row["served_by"]:
                row["served_by"] = response.headers.get("x-served-by", row["served_by"])
        except httpx.HTTPError as error:
            row[f"{name}_status"] = "error"
            row["error"] = type(error).__name__
        row[f"{name}_ms"] = round((time.perf_counter() - started) * 1000)
    return row


def outages(rows, key):
    """Return (start, end, seconds) for each run of failed calls; end is the first success after it."""
    result, start, last_ok = [], None, None
    for row in rows:
        at = datetime.fromisoformat(row["at"])
        if row[key] == 200:
            if start is not None:
                result.append((start, at, round((at - start).total_seconds(), 1)))
                start = None
            last_ok = at
        elif start is None:
            start = last_ok or at
    if start is not None:
        result.append((start, None, None))  # still down when the run ended
    return result


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * fraction))] if values else None


def summarize(rows, base, run_id):
    summary = {"run_id": run_id, "ticks": len(rows)}
    for name in ("read", "write"):
        ok = [r for r in rows if r[f"{name}_status"] == 200]
        summary[name] = {
            "error_percent": round(100 * (len(rows) - len(ok)) / len(rows), 1) if rows else None,
            "p50_ms": percentile([r[f"{name}_ms"] for r in ok], .5),
            "p95_ms": percentile([r[f"{name}_ms"] for r in ok], .95),
            "outages": [{"from": s.isoformat(timespec="milliseconds"), "to": e.isoformat(timespec="milliseconds") if e else None, "seconds": d}
                        for s, e, d in outages(rows, f"{name}_status")],
        }
    summary["served_by"] = sorted({r["served_by"] for r in rows if r["served_by"]})
    acked = {r["seq"] for r in rows if r["write_status"] == 200}
    try:
        stored = {p["seq"] for p in httpx.get(f"{base}/api/ping/{run_id}", timeout=5).json()["pings"]}
        lost = sorted(acked - stored)
        summary["lost_writes"] = {"acknowledged": len(acked), "missing_after_run": len(lost), "seqs": lost}
    except (httpx.HTTPError, KeyError, ValueError) as error:
        summary["lost_writes"] = {"error": f"could not verify: {type(error).__name__}"}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between ticks")
    parser.add_argument("--duration", type=float, default=120, help="total seconds; Ctrl+C ends early")
    parser.add_argument("--timeout", type=float, default=3.0, help="per-request timeout in seconds")
    parser.add_argument("--run-id", default="run-" + uuid4().hex[:8])
    parser.add_argument("--out", default="probe.csv")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    rows, seq, end = [], 0, time.monotonic() + args.duration
    print(f"run_id={args.run_id} url={base}; Ctrl+C to stop early")
    try:
        with httpx.Client(timeout=args.timeout, trust_env=False) as client:
            while time.monotonic() < end:
                started = time.monotonic()
                rows.append(tick(client, base, args.run_id, seq))
                row = rows[-1]
                print(f"{row['at']} seq={seq} read={row['read_status']} write={row['write_status']} by={row['served_by'] or '-'} {row['error']}")
                seq += 1
                time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    if not rows:
        return
    with Path(args.out).open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows, base, args.run_id)
    Path(args.out).with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
