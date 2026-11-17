"""VM-local recovery operations, run as root on either VM (Azure directly, AWS through rpc.py).

Nothing here decides *when* to act; recovery.py does. Every operation checks the database role first
and refuses when the state is not the one it expects. Old PVCs are never deleted.
"""
import json
from pathlib import Path
import re
import subprocess
import time

import promote

ROOT = promote.ROOT
KUBECTL = ['/usr/local/bin/k3s', 'kubectl']
GUARD = Path('/run/issues-k3s-start-approved')
REJOIN_CONFIG = Path('/etc/rancher/k3s/config.yaml.d/99-rejoin.yaml')
LSN = re.compile(r'[0-9A-F]{1,8}/[0-9A-F]{1,8}')
PVC = re.compile(r'postgres-data-[a-z0-9-]{1,40}')
SOURCE = re.compile(r'10\.200\.29\.[12]')
GATE = "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled={flag} WHERE singleton=true; COMMIT;"


def run(args, timeout=60, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} {args[1] if len(args) > 1 else ""} failed; inspect VM logs')
    return result


def kube(*args, **kwargs):
    return run(KUBECTL + list(args), **kwargs)


def k3s_active():
    return run(['systemctl', 'is-active', '--quiet', 'k3s'], check=False).returncode == 0


def lsn_of(value):
    if not isinstance(value, str) or not LSN.fullmatch(value):
        raise RuntimeError('Invalid WAL position')
    return value


def pod_exists():
    return bool(kube('-n', 'issues', 'get', 'pod', 'postgres-0', '--ignore-not-found', '-o', 'name').stdout.strip())


def wait_api(seconds=180, sleep=time.sleep):
    deadline = time.monotonic() + seconds
    while kube('get', 'namespace', 'issues', check=False).returncode:
        if time.monotonic() > deadline:
            raise RuntimeError('K3s API did not become ready')
        sleep(3)


def ping(payload=None):
    return {'ok': True, 'k3s': k3s_active()}


def db_state(payload=None):
    """role is primary, standby or down; 'down' also covers a stopped K3s."""
    if not k3s_active():
        return {'k3s': False, 'role': 'down'}
    try:
        recovery = promote.sql('SELECT pg_is_in_recovery()')
    except Exception:
        return {'k3s': True, 'role': 'down'}
    if recovery == 'f':
        try:
            enabled = promote.sql('SELECT writes_enabled FROM app_control WHERE singleton=true') == 't'
        except Exception:
            enabled = False
        return {'k3s': True, 'role': 'primary', 'writes_enabled': enabled, 'flush_lsn': promote.sql('SELECT pg_current_wal_flush_lsn()')}
    status, replay = promote.sql("SELECT coalesce((SELECT status FROM pg_stat_wal_receiver LIMIT 1),''), pg_last_wal_replay_lsn()").split('|')
    return {'k3s': True, 'role': 'standby', 'receiver': status, 'replay_lsn': replay}


def gate(payload):
    """Open or close application writes on a primary. Returns the flush LSN after the change."""
    if not isinstance(payload.get('enabled'), bool):
        raise RuntimeError('gate needs enabled=true/false')
    if promote.sql('SELECT pg_is_in_recovery()') != 'f':
        raise RuntimeError('Gate can only be changed on a primary')
    promote.sql(GATE.format(flag='true' if payload['enabled'] else 'false'))
    if promote.sql('SELECT writes_enabled FROM app_control WHERE singleton=true') != ('t' if payload['enabled'] else 'f'):
        raise RuntimeError('Writer gate did not change')
    return {'lsn': promote.sql('SELECT pg_current_wal_flush_lsn()')}


def wait_replay(payload, sleep=time.sleep):
    lsn = lsn_of(payload.get('lsn'))
    deadline = time.monotonic() + int(payload.get('timeout', 30))
    while True:
        row = promote.sql(f"SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn() >= '{lsn}'::pg_lsn")
        recovery, caught_up = row.split('|')
        if recovery != 't':
            raise RuntimeError('Database is not a standby; cannot confirm replication')
        if caught_up == 't':
            return {'caught_up': True}
        if time.monotonic() > deadline:
            return {'caught_up': False}
        sleep(1)


def postgres_listening():
    return any(re.search(r':5432\s', line) for line in run(['ss', '-ltnH']).stdout.splitlines())


def fence_db(payload=None, sleep=time.sleep):
    """Stop this VM's PostgreSQL and prove it: no pod and nothing listening on 5432."""
    kube('-n', 'issues', 'scale', 'statefulset/postgres', '--replicas=0')
    deadline = time.monotonic() + 120
    while pod_exists() or postgres_listening():
        if time.monotonic() > deadline:
            raise RuntimeError('Database is still running; fencing not proven')
        sleep(2)
    return {'fenced': True}


def unfence_db(payload=None):
    kube('-n', 'issues', 'scale', 'statefulset/postgres', '--replicas=1')
    kube('-n', 'issues', 'rollout', 'status', 'statefulset/postgres', '--timeout=300s', timeout=330)
    return db_state()


def app_scale(payload):
    replicas = payload.get('replicas')
    if replicas not in (0, 1):
        raise RuntimeError('replicas must be 0 or 1')
    kube('-n', 'issues', 'scale', 'deployment/issues-app', f'--replicas={replicas}')
    if replicas:
        kube('-n', 'issues', 'rollout', 'status', 'deployment/issues-app', '--timeout=180s', timeout=210)
    return {'ok': True}


def render_standby(template, pvc, source_ip, image, replicas):
    if not PVC.fullmatch(pvc) or not SOURCE.fullmatch(source_ip) or replicas not in (0, 1):
        raise RuntimeError('Invalid standby parameters')
    if not re.fullmatch(r'(docker.io/library/)?postgres@sha256:[0-9a-f]{64}', image):
        raise RuntimeError('Unexpected PostgreSQL image reference')
    text = template
    for key, value in {'__PVC_NAME__': pvc, '__SOURCE_IP__': source_ip, '__REPLICAS__': str(replicas), '__POSTGRES_IMAGE__': image}.items():
        text = text.replace(key, value)
    if re.search(r'__[A-Z_]+__', text):
        raise RuntimeError('Unrendered manifest placeholders')
    return text


def current_pvc():
    current = kube('-n', 'issues', 'get', 'statefulset', 'postgres', '-o', 'json', '--ignore-not-found').stdout.strip()
    if not current:
        raise RuntimeError('PostgreSQL StatefulSet not found')
    spec = json.loads(current)['spec']['template']['spec']
    image = next(c['image'] for c in spec['containers'] if c['name'] == 'postgres')
    return next(v['persistentVolumeClaim']['claimName'] for v in spec['volumes'] if v['name'] == 'data'), image


def start_without_agent():
    """Start a guarded K3s with no kubelet so the old primary pod cannot start (runbook A.4.3)."""
    REJOIN_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    REJOIN_CONFIG.write_text('disable-agent: true\n')
    GUARD.touch()
    run(['systemctl', 'reset-failed', 'k3s'], check=False)
    run(['systemctl', 'start', 'k3s'], timeout=180)
    wait_api()


def rejoin(payload, sleep=time.sleep):
    """Make this VM a streaming standby of source_ip on a NEW PVC. Safe to repeat for the same pvc."""
    pvc, source_ip = payload.get('pvc'), payload.get('source_ip')
    if not k3s_active():
        start_without_agent()
    existing, image = current_pvc()
    manifest = ROOT / 'postgres-rejoin.yaml'
    if existing != pvc:
        kube('-n', 'issues', 'scale', 'deployment/issues-app', '--replicas=0', check=False)
        kube('-n', 'issues', 'scale', 'statefulset/postgres', '--replicas=0')
        kube('-n', 'issues', 'delete', 'pod', 'postgres-0', '--ignore-not-found', '--force', '--grace-period=0')
        manifest.write_text(render_standby(payload['template'], pvc, source_ip, image, 0))
        manifest.chmod(0o640)
        kube('apply', '--dry-run=server', '-f', str(manifest))
        kube('apply', '-f', str(manifest))
    if REJOIN_CONFIG.exists():
        REJOIN_CONFIG.rename(ROOT / '99-rejoin.yaml.disabled')
        run(['systemctl', 'restart', 'k3s'], timeout=180)
        wait_api()
    kube('-n', 'issues', 'scale', 'statefulset/postgres', '--replicas=1')
    kube('-n', 'issues', 'rollout', 'status', 'statefulset/postgres', '--timeout=600s', timeout=630)
    volume = json.loads(kube('-n', 'issues', 'get', 'pvc', pvc, '-o', 'json').stdout)['spec'].get('volumeName')
    if not volume:
        raise RuntimeError('Standby PVC is not bound')
    kube('patch', 'pv', volume, '-p', '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}')
    active = ROOT / 'postgres.yaml'
    active.write_text(render_standby(payload['template'], pvc, source_ip, image, 1))
    active.chmod(0o640)
    return {'pvc': pvc}


def promote_local(payload, sleep=time.sleep):
    """Promote this standby after the controller has fenced the other writer. Opens the gate."""
    lsn = lsn_of(payload.get('min_lsn'))
    if promote.sql('SELECT pg_is_in_recovery()') != 't':
        raise RuntimeError('Not a standby; refusing to promote')
    if promote.sql('SELECT pg_is_wal_replay_paused()') != 'f':
        raise RuntimeError('WAL replay is paused')
    deadline = time.monotonic() + 30
    while promote.sql(f"SELECT pg_last_wal_replay_lsn() >= '{lsn}'::pg_lsn") != 't':
        if time.monotonic() > deadline:
            raise RuntimeError('Standby has not replayed the final WAL position')
        sleep(1)
    if promote.sql('SELECT pg_promote(true,30)') != 't':
        raise RuntimeError('Promotion did not complete')
    if promote.sql('SELECT pg_is_in_recovery()') != 'f':
        raise RuntimeError('Not primary after promote')
    gate({'enabled': True})
    return {'role': 'primary'}


ACTIONS = {f.__name__: f for f in (ping, db_state, gate, wait_replay, fence_db, unfence_db, app_scale, rejoin, promote_local)}
