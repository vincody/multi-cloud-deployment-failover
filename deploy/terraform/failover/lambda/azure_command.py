"""Root command executed through SSM on Azure; no credentials or start-K3s logic."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/dcs29')


def sql(query):
    result = subprocess.run(['/usr/local/bin/k3s', 'kubectl', '-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'issues', '-At', '-c', query], text=True, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Database command failed; inspect VM logs')
    return result.stdout.strip()


def execute(phase, owner):
    if phase not in ('preflight', 'promote') or not owner.startswith('arn:aws:states:us-east-1:'):
        raise RuntimeError('Invalid operation/incident')
    if not (ROOT / 'automation-result.json').exists():
        raise RuntimeError('Initial replication has not been verified')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'k3s']).returncode:
        raise RuntimeError('K3s is guarded/stopped; do not auto-start it')
    if not 170000 <= int(sql("SHOW server_version_num")) < 180000:
        raise RuntimeError('Unsupported PostgreSQL version')
    marker = ROOT / 'failover-incident.json'
    previous = json.loads(marker.read_text()) if marker.exists() else None
    recovery = sql('SELECT pg_is_in_recovery()') == 't'
    if previous and previous['owner'] != owner:
        raise RuntimeError('Another incident owns this database; operator rearm required')
    if not recovery:
        if phase != 'promote' or not previous or previous['owner'] != owner:
            raise RuntimeError('Azure is already primary; no unowned promotion allowed')
    else:
        if sql('SELECT pg_is_wal_replay_paused()') != 'f':
            raise RuntimeError('WAL replay is paused')
        deadline = time.monotonic() + 30
        while sql('SELECT pg_last_wal_receive_lsn() IS NOT NULL AND pg_last_wal_replay_lsn() >= pg_last_wal_receive_lsn()') != 't':
            if time.monotonic() >= deadline:
                raise RuntimeError('Azure has not replayed all received WAL')
            time.sleep(1)
    if phase == 'promote':
        # Receipt before mutation makes a retried SSM command safe for this incident.
        if not previous:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'owner': owner}, stream); stream.flush(); os.fsync(stream.fileno())
        if recovery and sql('SELECT pg_promote(true,30)') != 't':
            raise RuntimeError('Promotion did not complete')
        if sql('SELECT pg_is_in_recovery()') != 'f':
            raise RuntimeError('Azure is not primary after promote')
        sql("BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=true WHERE singleton=true; COMMIT;")
        if sql('SELECT writes_enabled FROM app_control WHERE singleton=true') != 't':
            raise RuntimeError('Writer gate did not open')
    return {'ready': True, 'role': 'standby' if phase == 'preflight' else 'primary'}


def main(phase, owner):
    with open('/run/dcs29-promote.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = execute(phase, owner)
    print('DCS29_RESULT=' + json.dumps(result))
