"""Release the K3s start guard on the Azure VM after a reboot, but only when that is provably safe.

The guard (`ExecStartPre=test -f /run/issues-k3s-start-approved`) keeps a rebooted VM from running a stale
primary. On Azure that is the wrong default for an unattended demo: Azure is either a standby (harmless) or
the legitimate writer after a failover. This service looks at two independent facts and approves only when
both agree:

  1. the role of the LOCAL data directory, read from disk because K3s is not running yet
     (standby.signal present = standby, absent = primary);
  2. whether the PEER (AWS) could be a writer: EC2 `stopped` means fenced; otherwise the AWS recovery RPC or
     the AWS application status must say standby or down.

A local primary is only started when the peer is fenced, a standby or down. Anything unknown is refused and
re-checked until a person or the situation changes. `watcher.pause` is an operator veto. Never installed on AWS:
a rebooted AWS VM is handled as a failover by the watcher.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CONFIG = Path(os.environ.get('DCS29_WATCHER_CONFIG', '/etc/dcs29/watcher.json'))
ROOT = Path('/opt/dcs29')
MANIFEST = ROOT / 'postgres.yaml'
STORAGE = Path('/var/lib/rancher/k3s/storage')
MARKER = Path('/run/issues-k3s-start-approved')
PAUSE = ROOT / 'watcher.pause'
POLL = 30


def log(message):
    print(datetime.now(timezone.utc).isoformat(timespec='seconds'), message, flush=True)


def local_role(manifest=MANIFEST, storage=STORAGE):
    """'standby', 'primary', 'empty' or None when the data directory cannot be identified with certainty."""
    if not manifest.exists():
        return None
    claims = set(re.findall(r'claimName:\s*(\S+)', manifest.read_text()))
    if len(claims) != 1:
        return None
    folders = list(storage.glob(f'pvc-*_issues_{claims.pop()}'))
    if len(folders) != 1:
        return None
    data = folders[0] / 'pgdata'
    if not (data / 'PG_VERSION').exists():
        return 'empty'
    return 'standby' if (data / 'standby.signal').exists() else 'primary'


def peer_view(ec2_state, rpc_role, http_role):
    """'fenced' (EC2 stopped), the role AWS reports, or 'unknown'. Each argument is a callable returning
    a value or raising/returning None when it cannot tell."""
    try:
        state = ec2_state()
    except Exception:
        state = None
    if state == 'stopped':
        return 'fenced'
    if state in (None, 'running', 'pending', 'stopping'):
        for probe in (rpc_role, http_role):
            try:
                role = probe()
            except Exception:
                role = None
            if role in ('primary', 'standby', 'down'):
                return role
    return 'unknown'


def decide(local, peer, paused=False):
    """(approve, reason). Pure so the whole matrix can be tested."""
    if paused:
        return False, 'watcher.pause is present (operator veto)'
    if local not in ('standby', 'primary'):
        return False, f'local database role is {local or "unknown"}; refusing to start K3s'
    if local == 'standby':
        return True, 'local database is a standby; it cannot accept writes'
    if peer in ('fenced', 'standby', 'down'):
        return True, f'local database is the primary and AWS is {peer}'
    if peer == 'primary':
        return False, 'both VMs may hold a primary; an operator must decide'
    return False, 'local database is a primary but the AWS state is unknown'


def k3s_active():
    return subprocess.run(['systemctl', 'is-active', '--quiet', 'k3s']).returncode == 0


def start_k3s():
    MARKER.touch()
    subprocess.run(['systemctl', 'reset-failed', 'k3s'])
    subprocess.run(['systemctl', 'start', 'k3s'], check=True, timeout=300)


def load_config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def probes(cfg):
    def ec2_state():
        import boto3
        reservations = boto3.client('ec2', region_name=cfg['aws_region']).describe_instances(InstanceIds=[cfg['aws_instance_id']])['Reservations']
        return reservations[0]['Instances'][0]['State']['Name']

    def rpc_role():
        if not (cfg.get('recovery') or {}).get('enabled'):
            return None
        import recovery
        return recovery.RemoteAws().call('db_state', timeout=30)['role']

    def http_role():
        with urllib.request.urlopen(cfg['aws_origin'] + '/api/database/status', timeout=8) as response:
            status = json.load(response)
        return 'primary' if status.get('role') == 'primary' else 'standby' if status.get('role') == 'standby' else None
    return ec2_state, rpc_role, http_role


def run(cfg, local=local_role, view=peer_view, sleep=time.sleep, start=start_k3s, active=k3s_active, paused=PAUSE.exists):
    """Return 'already-running' or 'started'. Loops, logging only when the reason changes, until approved."""
    if active():
        return 'already-running'
    if not (ROOT / 'automation-result.json').exists():
        raise RuntimeError('Initial deployment was never verified; not starting K3s')
    last = None
    while True:
        if active():
            return 'already-running'
        role = local()
        peer = view(*probes(cfg)) if role == 'primary' else 'not-needed'
        approved, reason = decide(role, peer, paused())
        if reason != last:
            log(('Approved: ' if approved else 'Refused: ') + reason)
            last = reason
        if approved:
            start()
            log('K3s started.')
            return 'started'
        sleep(POLL)


def main():
    try:
        run(load_config())
    except Exception as error:
        log(f'Unguard stopped: {type(error).__name__}: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
