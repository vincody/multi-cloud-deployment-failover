"""Root-side RPC, sent over OpenSSH stdin. Never print secrets or subprocess logs."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import uuid
import urllib.request

ROOT = Path('/opt/dcs29')
KUBECTL = ['/usr/local/bin/k3s', 'kubectl']
OWNER = 'dcs29-automation'


def run(args, data=None, timeout=600, optional=False):
    result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=timeout)
    if result.returncode and not optional:
        raise RuntimeError(f'{Path(args[0]).name} failed; inspect VM logs (output suppressed to protect credentials)')
    return result


def kubectl(*args, **kwargs):
    return run(KUBECTL + list(args), **kwargs)


def write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    # Create private from the start; no interval with world-readable credentials.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text.replace('\r\n', '\n'))
    temp.replace(path)


def get_resource(kind, name):
    result = kubectl('-n', 'issues', 'get', kind, name, '-o', 'json', '--ignore-not-found')
    return json.loads(result.stdout) if result.stdout.strip() else None


def preflight(payload):
    run(['cloud-init', 'status', '--wait'], timeout=900)
    if run(['systemctl', 'is-active', 'k3s'], optional=True).returncode:
        raise RuntimeError('K3s is stopped/guarded. Verify writer roles; do not auto-start a previously fenced VM.')
    resource = get_resource('statefulset', 'postgres')
    if resource:
        if resource['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') != OWNER:
            raise RuntimeError('Existing PostgreSQL is not owned by this automation. Manual adoption/migration required.')
        volumes = resource['spec']['template']['spec']['volumes']
        pvc = next(v['persistentVolumeClaim']['claimName'] for v in volumes if v['name'] == 'data')
        status = kubectl('-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'issues', '-At', '-c', 'SELECT pg_is_in_recovery()', optional=True)
        if status.returncode:
            if payload['site'] != 'azure' or pvc == payload['pvc']:
                raise RuntimeError('Existing PostgreSQL is not responding. Inspect it; an incomplete Azure clone requires a new standby_storage_generation.')
            if (ROOT / 'automation-result.json').exists():
                raise RuntimeError('A previously verified standby is offline. Use the DR recovery runbook; this is not a failed initial clone.')
        elif status.stdout.strip() != ('f' if payload['site'] == 'aws' else 't'):
            raise RuntimeError('Database roles have changed. Automation will not undo failover/failback.')
        if payload['site'] == 'aws' and not status.returncode:
            gate = kubectl('-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'issues', '-At', '-c', "SELECT to_regclass('public.app_control')")
            if gate.stdout.strip():
                enabled = kubectl('-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'issues', '-At', '-c', 'SELECT writes_enabled FROM app_control WHERE singleton=true')
                if enabled.stdout.strip() != 't':
                    raise RuntimeError('Writes are paused. Resume the DR runbook before deployment.')
    return {'ok': True}


def http_probe(payload):
    kubectl('-n', 'kube-system', 'rollout', 'status', 'deployment/traefik', '--timeout=180s')
    with urllib.request.urlopen('http://127.0.0.1' + payload['path'], timeout=15) as response:
        return json.loads(response.read())


def prepare(payload):
    run(['apt-get', '-o', 'Acquire::Retries=5', 'update'])
    run(['apt-get', '-o', 'Acquire::Retries=5', 'install', '-y', 'wireguard', 'ca-certificates'])
    ROOT.mkdir(parents=True, exist_ok=True)
    folder = Path('/etc/wireguard')
    folder.mkdir(mode=0o700, exist_ok=True)
    existing = folder / 'wg0.conf'
    if existing.exists() and '# Managed by dcs29-automation' not in existing.read_text():
        raise RuntimeError('wg0 already has manual configuration; migrate it explicitly before automation.')
    for route in json.loads(run(['ip', '-j', 'route']).stdout):
        dest = route.get('dst', 'default')
        if dest != 'default' and route.get('dev') != 'wg0':
            network = ipaddress.ip_network(dest, strict=False)
            if ipaddress.ip_address(payload['local_ip']) in network or ipaddress.ip_address(payload['peer_ip']) in network:
                raise RuntimeError('WireGuard subnet conflicts with an existing route.')
    key = folder / 'private.key'
    if not key.exists():
        write(key, run(['wg', 'genkey']).stdout)
    public = run(['wg', 'pubkey'], data=key.read_text()).stdout.strip()
    write(folder / 'public.key', public + '\n')
    # Approve only the already-running initial boot. Do not use this RPC to start fenced VMs.
    write('/run/issues-k3s-start-approved', '')
    write('/etc/systemd/system/k3s.service.d/10-dr-start-guard.conf', '[Service]\nExecStartPre=/usr/bin/test -f /run/issues-k3s-start-approved\n')
    run(['systemctl', 'daemon-reload'])
    return {'public_key': public}


def wireguard(payload):
    if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', payload['peer_key']):
        raise RuntimeError('Invalid WireGuard public key')
    key = Path('/etc/wireguard/private.key').read_text().strip()
    write('/etc/wireguard/wg0.conf', f"""# Managed by dcs29-automation
[Interface]
Address = {payload['local_ip']}/24
ListenPort = 51820
PrivateKey = {key}
[Peer]
PublicKey = {payload['peer_key']}
Endpoint = {payload['peer_host']}:51820
AllowedIPs = {payload['peer_ip']}/32
PersistentKeepalive = 25
""")
    ufw = run(['sh', '-c', 'command -v ufw'], optional=True)
    if not ufw.returncode and 'Status: active' in run(['ufw', 'status']).stdout:
        run(['ufw', 'allow', 'from', payload['peer_host'], 'to', 'any', 'port', '51820', 'proto', 'udp'])
        run(['ufw', 'allow', 'in', 'on', 'wg0', 'from', payload['peer_ip'], 'to', payload['local_ip'], 'port', '5432', 'proto', 'tcp'])
    run(['systemctl', 'enable', 'wg-quick@wg0'])
    run(['systemctl', 'restart', 'wg-quick@wg0'])
    return {'ok': True}


def credentials(payload):
    path = ROOT / 'postgres.env'
    keys = ('POSTGRES_PASSWORD', 'APP_DB_PASSWORD', 'REPLICATION_PASSWORD')
    existing = None
    if path.exists():
        existing = dict(line.split('=', 1) for line in path.read_text().splitlines() if line)
        if set(existing) != set(keys) or any(not re.fullmatch(r'[0-9a-f]{48}', value) for value in existing.values()):
            raise RuntimeError('Existing credential file has unexpected format; never overwrite database credentials.')
    if payload.get('values'):
        if not existing and get_resource('secret', 'postgres-auth'):
            raise RuntimeError('Azure Secret exists but credential file is missing. Restore credentials, do not overwrite.')
        values = payload['values']
        if existing and existing != values:
            raise RuntimeError('Azure credentials differ from AWS; migration/rotation must be explicit.')
    elif existing:
        values = existing
    else:
        if get_resource('secret', 'postgres-auth'):
            raise RuntimeError('Database Secret exists but credential file is missing. Restore credentials, do not regenerate.')
        values = {key: secrets.token_hex(24) for key in keys}
    if set(values) != set(keys) or any(not re.fullmatch(r'[0-9a-f]{48}', value) for value in values.values()):
        raise RuntimeError('Invalid credential payload')
    write(path, ''.join(f'{key}={values[key]}\n' for key in keys))
    rendered = kubectl('-n', 'issues', 'create', 'secret', 'generic', 'postgres-auth', '--from-env-file=' + str(path), '--dry-run=client', '-o', 'json').stdout
    kubectl('apply', '-f', '-', data=rendered)
    write(ROOT / 'secret.env', f"DATABASE_URL=postgresql://issues_app:{values['APP_DB_PASSWORD']}@postgres.issues.svc.cluster.local:5432/issues\n")
    rendered = kubectl('-n', 'issues', 'create', 'secret', 'generic', 'issues-db', '--from-env-file=' + str(ROOT / 'secret.env'), '--dry-run=client', '-o', 'json').stdout
    kubectl('apply', '-f', '-', data=rendered)
    return {'values': values} if payload.get('export') else {'ok': True}


def apply(payload):
    name = payload['name']
    if name not in ('postgres-config', 'postgres', 'postgres-endpoint', 'issues-app', 'ingress-http'):
        raise RuntimeError('Invalid manifest name')
    if name == 'ingress-http' and get_resource('ingress', 'issues-http'):
        return {'ok': True, 'preserved': True}  # Do not overwrite a later TLS redirect.
    path = ROOT / (name + '.yaml')
    write(path, payload['yaml'], mode=0o640)
    if name == 'postgres' and payload.get('new_standby_pvc'):
        current = get_resource('statefulset', 'postgres')
        if current:
            pvc = next(v['persistentVolumeClaim']['claimName'] for v in current['spec']['template']['spec']['volumes'] if v['name'] == 'data')
            if pvc != payload['new_standby_pvc']:
                kubectl('-n', 'issues', 'scale', 'statefulset/postgres', '--replicas=0')
                kubectl('-n', 'issues', 'wait', '--for=delete', 'pod/postgres-0', '--timeout=120s')
    kubectl('apply', '--dry-run=server', '-f', str(path))
    kubectl('apply', '-f', str(path))
    return {'ok': True}


def database_wait(payload):
    kubectl('-n', 'issues', 'rollout', 'status', 'statefulset/postgres', '--timeout=600s', timeout=630)
    pvc = get_resource('pvc', payload['pvc'])
    if not pvc or not pvc['spec'].get('volumeName'):
        raise RuntimeError('Database PVC is not bound')
    kubectl('patch', 'pv', pvc['spec']['volumeName'], '-p', '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}')
    status = kubectl('-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-U', 'postgres', '-d', 'issues', '-At', '-c', "SELECT pg_is_in_recovery(),current_setting('server_version_num')").stdout.strip().split('|')
    if status[0] != ('f' if payload['site'] == 'aws' else 't') or not 170000 <= int(status[1]) < 180000:
        raise RuntimeError('Unexpected PostgreSQL role/version')
    return {'ok': True}


def app_bootstrap(payload):
    # Wait for the container, not readiness: control schema does not exist yet.
    kubectl('-n', 'issues', 'wait', '--for=jsonpath={.status.phase}=Running', 'pod', '-l', 'app=issues-app', '--timeout=180s')
    kubectl('-n', 'issues', 'exec', 'postgres-0', '--', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'issues', '-c', 'GRANT pg_read_all_stats TO issues_app')
    kubectl('-n', 'issues', 'exec', 'deployment/issues-app', '--', 'python', '-c', 'from database.issues import initialize; initialize()')
    return {'ok': True}


def app_wait(payload):
    kubectl('-n', 'issues', 'rollout', 'status', 'deployment/issues-app', '--timeout=180s')
    return {'ok': True}


def api(payload):
    code = """import json,urllib.request
p=json.loads(__import__('sys').argv[1])
b=json.dumps(p['body']).encode() if 'body' in p else None
r=urllib.request.Request('http://127.0.0.1:8080'+p['path'],data=b,headers={'Content-Type':'application/json'},method=p.get('method','GET'))
with urllib.request.urlopen(r,timeout=10) as response: print(response.read().decode())
"""
    return json.loads(kubectl('-n', 'issues', 'exec', 'deployment/issues-app', '--', 'python', '-c', code, json.dumps(payload)).stdout)


def sample(payload):
    path = ROOT / 'automation-check.json'
    if path.exists():
        body = json.loads(path.read_text())
    else:
        body = {'request_id': str(uuid.uuid4()), 'author': 'Terraform automation', 'title': 'Replication smoke check', 'description': 'Created on AWS; verified on Azure.', 'status': 'open', 'version': 0}
        write(path, json.dumps(body))  # Persist receipt ID before sending, so retries never duplicate.
    return api({'path': '/api/issues', 'method': 'POST', 'body': body})


def status(payload):
    return api({'path': '/api/database/status'})


def finish(payload):
    write(ROOT / 'automation-result.json', json.dumps(payload, indent=2))
    return {'ok': True}


ACTIONS = {f.__name__: f for f in (preflight, prepare, wireguard, credentials, apply, database_wait, app_bootstrap, app_wait, api, sample, status, finish, http_probe)}


def main():
    os.umask(0o077)
    try:
        request = json.load(sys.stdin)
        result = ACTIONS[request['action']](request.get('payload', {}))
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else f'{type(exc).__name__}: operation failed'
        print(json.dumps({'ok': False, 'error': message}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
